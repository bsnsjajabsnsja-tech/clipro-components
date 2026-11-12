#!/usr/bin/env python3
"""Clipro component publisher (TEST channel): 7 component groups.

Modes
  check         metadata + checksum availability for every group. Downloads nothing large.
                Used by the daily schedule. Never publishes.
  build         download, verify, repackage and sign into --output (no GitHub write).
  publish-test  build + publish a separate signed TEST prerelease per group. Manual only.

Rules (enforced in code)
  * Never executes a downloaded file. Checks are static only (hashes, PE header, wheel RECORD).
  * An upstream asset is published only after its SHA256 matched an upstream checksum source
    (or, for aria2 which publishes none, a reviewed hash pinned in components.json).
  * Signing refuses when the secret is missing or does not match keys/publisher.pub.
  * A published tag is never overwritten or re-used; re-runs report ALREADY_PUBLISHED and
    download nothing for that group.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
COMPONENTS_FILE = ROOT / "components.json"
PUBLIC_KEY_FILE = ROOT / "keys" / "publisher.pub"

UPSTREAM = "yt-dlp/yt-dlp"            # kept for the original yt-dlp.exe pilot
API = f"https://api.github.com/repos/{UPSTREAM}/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "CliproComponentPublisher/0.2"}
TARGET_PY = (3, 12)                   # Clipro's runtime
FIXED_TIME = (2020, 2, 2, 0, 0, 0)    # deterministic ZIP timestamps for new groups
GROUP_ORDER = ("yt-dlp-exe", "ffmpeg", "deno", "aria2c", "exiftool", "yt_dlp", "yt_dlp_ejs")


class VerifyError(RuntimeError):
    """Upstream asset failed a verification step: never publish it."""


# --------------------------------------------------------------------------- HTTP
def _headers(url: str) -> dict:
    h = dict(HEADERS)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch(url: str, limit: int = 250_000_000) -> bytes:
    if not url.startswith("https://"):
        raise RuntimeError("HTTPS required")
    request = urllib.request.Request(url, headers=_headers(url))
    with urllib.request.urlopen(request, timeout=90) as response:
        if not response.geturl().startswith("https://"):
            raise RuntimeError("redirected to non-HTTPS")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("Asset exceeds size limit")
    return data


def fetch_json(url: str):
    return json.loads(fetch(url, 5_000_000))


def head_location(url: str) -> str:
    """Final URL after redirects (used for github.com/<repo>/releases/latest)."""
    request = urllib.request.Request(url, headers=_headers(url), method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.geturl()


class Cache:
    """Download cache: one download per upstream asset per run, checked by SHA256."""

    def __init__(self, folder: Path):
        self.folder = folder

    def get(self, url: str, name: str, expected_sha: str, limit: int = 400_000_000) -> bytes:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        if path.is_file():
            data = path.read_bytes()
            if sha(data) == expected_sha:
                print(f"  cache hit {name}")
                return data
            path.unlink()
        data = fetch(url, limit)
        if sha(data) != expected_sha:
            raise VerifyError(f"{name}: SHA256 {sha(data)} != upstream {expected_sha}")
        path.write_bytes(data)
        return data


# --------------------------------------------------------------------------- helpers
def checksum_from_sumfile(data: str, name: str) -> str:
    for line in data.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2).lstrip("./") == name:
            return match.group(1).lower()
    raise RuntimeError("Missing checksum for " + name)


def hex64(text: str) -> str | None:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    return m.group(1).lower() if m else None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def zip_binary(binary: bytes) -> bytes:
    """Original yt-dlp.exe pilot packaging (unchanged)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("yt-dlp.exe", binary)
    return out.getvalue()


def zip_files(files: dict[str, bytes]) -> bytes:
    """Deterministic ZIP: sorted names, fixed timestamps, regular files only."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name], compresslevel=6)
    return out.getvalue()


def safe_member(name: str) -> str:
    n = name.replace("\\", "/")
    if n.startswith("/") or ":" in n or any(p in ("", ".", "..") for p in n.rstrip("/").split("/")):
        raise VerifyError(f"unsafe path in upstream archive: {name!r}")
    return n


def read_zip(blob: bytes, max_total: int = 1_500_000_000) -> dict[str, bytes]:
    """All regular files of an upstream ZIP (symlinks/unsafe paths rejected)."""
    out: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for info in z.infolist():
            name = safe_member(info.filename)
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise VerifyError(f"symlink in upstream archive: {name}")
            total += info.file_size
            if total > max_total:
                raise VerifyError("upstream archive too large when extracted")
            if name in out:
                raise VerifyError(f"duplicate entry {name}")
            out[name] = z.read(info)
    return out


def strip_prefix(files: dict[str, bytes], prefix: str) -> dict[str, bytes]:
    out = {}
    for name, data in files.items():
        if not name.startswith(prefix):
            raise VerifyError(f"unexpected entry outside {prefix!r}: {name}")
        out[name[len(prefix):]] = data
    return out


def check_pe_x64(name: str, data: bytes) -> None:
    """Static PE header check: 'MZ', 'PE\\0\\0', machine AMD64. Nothing is executed."""
    if len(data) < 0x100 or data[:2] != b"MZ":
        raise VerifyError(f"{name}: not a Windows PE file")
    off = struct.unpack_from("<I", data, 0x3C)[0]
    if off + 6 > len(data) or data[off:off + 4] != b"PE\0\0":
        raise VerifyError(f"{name}: bad PE header")
    machine = struct.unpack_from("<H", data, off + 4)[0]
    if machine != 0x8664:
        raise VerifyError(f"{name}: machine 0x{machine:04x} is not x64")


def verify_wheel(blob: bytes, dist: str, version: str) -> dict[str, bytes]:
    """Every file in the wheel must match its RECORD sha256 and size."""
    files = read_zip(blob, 200_000_000)
    record_name = f"{dist}-{version}.dist-info/RECORD"
    if record_name not in files:
        raise VerifyError(f"wheel has no {record_name}")
    rows = list(csv.reader(io.StringIO(files[record_name].decode("utf-8"))))
    listed = {}
    for row in rows:
        if not row or not row[0]:
            continue
        path, digest, size = (row + ["", ""])[:3]
        listed[path] = (digest, size)
    for name, data in files.items():
        if name == record_name:
            continue
        if name not in listed:
            raise VerifyError(f"wheel file not in RECORD: {name}")
        digest, size = listed[name]
        if not digest.startswith("sha256="):
            raise VerifyError(f"RECORD has no sha256 for {name}")
        want = digest[len("sha256="):]
        got = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if got != want or (size and int(size) != len(data)):
            raise VerifyError(f"wheel RECORD mismatch for {name}")
    missing = [p for p in listed if p != record_name and p not in files]
    if missing:
        raise VerifyError(f"RECORD lists missing files: {missing[:3]}")
    meta = files.get(f"{dist}-{version}.dist-info/METADATA", b"").decode("utf-8", "replace")
    if f"\nVersion: {version}\n" not in meta:
        raise VerifyError(f"METADATA version is not {version}")
    return files


def python_ok(spec: str) -> bool:
    for part in [p.strip() for p in (spec or "").split(",") if p.strip()]:
        m = re.match(r"(>=|<=|>|<|==|!=)\s*([\d.]+)$", part)
        if not m:
            continue
        ver = tuple(int(x) for x in m.group(2).split("."))
        cur = TARGET_PY[:len(ver)] + (0,) * max(0, len(ver) - len(TARGET_PY))
        ok = {">=": cur >= ver, "<=": cur <= ver, ">": cur > ver, "<": cur < ver,
              "==": cur == ver, "!=": cur != ver}[m.group(1)]
        if not ok:
            return False
    return True


# --------------------------------------------------------------------------- GitHub upstream metadata
def gh_latest_tag(repo: str) -> str:
    """Latest *stable* release tag (GitHub's /releases/latest excludes drafts + prereleases)."""
    url = head_location(f"https://github.com/{repo}/releases/latest")
    m = re.search(r"/releases/tag/([^/?#]+)$", url)
    if not m:
        raise RuntimeError(f"{repo}: cannot resolve latest release")
    return urllib.request.unquote(m.group(1))


def gh_asset_digest(repo: str, tag: str, asset: str) -> str | None:
    """GitHub-computed SHA256 of a release asset ('digest' field), API first, page second."""
    try:
        data = fetch_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
        for a in data.get("assets") or []:
            if a.get("name") == asset:
                d = a.get("digest") or ""
                return d.split(":", 1)[1].lower() if d.startswith("sha256:") else None
        return None
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        pass
    html = fetch(f"https://github.com/{repo}/releases/expanded_assets/{tag}", 5_000_000).decode("utf-8", "replace")
    # each asset row: ... href="/repo/releases/download/tag/<asset>" ... sha256:<hex> ...
    pos = html.find(f'/releases/download/{tag}/{asset}"')
    if pos < 0:
        return None
    nxt = html.find("/releases/download/", pos + 20)
    seg = html[pos: nxt if nxt > 0 else len(html)]
    m = re.search(r"sha256:([0-9a-f]{64})", seg)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- groups
class Plan:
    """What one group would publish: resolved before any large download."""

    def __init__(self, cid, version, asset, url, sha256, method, license_, source, extra=None):
        self.cid, self.version, self.asset, self.url, self.sha256 = cid, version, asset, url, sha256
        self.method, self.license, self.source = method, license_, source
        self.extra = extra or {}

    @property
    def tag(self) -> str:
        return f"clipro-{self.cid}-{self.version}-test"


def load_components() -> dict:
    return json.loads(COMPONENTS_FILE.read_text(encoding="utf-8"))


def group_cfg(cid: str) -> dict:
    for g in load_components()["groups"]:
        if g["id"] == cid:
            return g
    raise KeyError(cid)


def release_info():
    """Original yt-dlp.exe pilot lookup (unchanged behaviour)."""
    data = fetch_json(API)
    if data.get("draft") or data.get("prerelease"):
        raise RuntimeError("Not a stable upstream release")
    version = str(data["tag_name"])
    if not re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", version):
        raise RuntimeError("Unexpected version")
    assets = {a["name"]: a["browser_download_url"] for a in data["assets"]}
    for name in ("yt-dlp.exe", "SHA2-256SUMS"):
        if name not in assets:
            raise RuntimeError("Missing official asset: " + name)
        if not assets[name].startswith(f"https://github.com/{UPSTREAM}/releases/download/"):
            raise RuntimeError("Unexpected upstream asset origin")
    return version, assets


_YTDLP_TAG: list = []


def ytdlp_stable_tag() -> str:
    if not _YTDLP_TAG:
        tag = gh_latest_tag("yt-dlp/yt-dlp")
        if not re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", tag):
            raise RuntimeError(f"unexpected yt-dlp tag {tag}")
        _YTDLP_TAG.append(tag)
    return _YTDLP_TAG[0]


def plan_ytdlp_exe() -> Plan:
    tag = ytdlp_stable_tag()
    base = f"https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/"
    sums = fetch(base + "SHA2-256SUMS", 100_000).decode("utf-8")
    return Plan("yt-dlp-exe", tag, "yt-dlp.exe", base + "yt-dlp.exe", checksum_from_sumfile(sums, "yt-dlp.exe"),
                f"SHA2-256SUMS of yt-dlp/yt-dlp release {tag}", "Unlicense (bundled third-party: see "
                "THIRD_PARTY_LICENSES.txt of the yt-dlp release)", f"https://github.com/yt-dlp/yt-dlp/tree/{tag}")


def plan_ffmpeg() -> Plan:
    ver = fetch("https://www.gyan.dev/ffmpeg/builds/release-version", 1000).decode().strip()
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", ver):
        raise RuntimeError(f"unexpected FFmpeg version {ver!r}")
    gh_tag = gh_latest_tag("GyanD/codexffmpeg")
    if gh_tag != ver:
        raise VerifyError(f"gyan.dev release-version {ver} != GyanD/codexffmpeg latest {gh_tag}")
    asset = f"ffmpeg-{ver}-essentials_build.zip"
    d_gh = gh_asset_digest("GyanD/codexffmpeg", ver, asset)
    d_site = hex64(fetch(f"https://www.gyan.dev/ffmpeg/builds/packages/{asset}.sha256", 1000).decode())
    if not d_gh or not d_site:
        raise VerifyError(f"FFmpeg {ver}: need both GitHub digest and gyan.dev .sha256 (got {d_gh}, {d_site})")
    if d_gh != d_site:
        raise VerifyError(f"FFmpeg {ver}: GitHub digest {d_gh} != gyan.dev .sha256 {d_site}")
    return Plan("ffmpeg", ver, asset, f"https://github.com/GyanD/codexffmpeg/releases/download/{ver}/{asset}",
                d_gh, "GitHub asset digest (GyanD/codexffmpeg) == gyan.dev .sha256",
                "GPL-3.0-or-later (gyan.dev essentials build; see LICENSES/ffmpeg)",
                f"https://github.com/FFmpeg/FFmpeg/tree/n{ver} ; build: https://www.gyan.dev/ffmpeg/builds/")


def plan_deno() -> Plan:
    tag = gh_latest_tag("denoland/deno")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise RuntimeError(f"unexpected Deno tag {tag}")
    asset = "deno-x86_64-pc-windows-msvc.zip"
    base = f"https://github.com/denoland/deno/releases/download/{tag}/{asset}"
    d_file = hex64(fetch(base + ".sha256sum", 10_000).decode("utf-8", "replace"))
    d_gh = gh_asset_digest("denoland/deno", tag, asset)
    if not d_file:
        raise VerifyError(f"Deno {tag}: no .sha256sum published")
    if d_gh and d_gh != d_file:
        raise VerifyError(f"Deno {tag}: GitHub digest {d_gh} != .sha256sum {d_file}")
    return Plan("deno", tag[1:], asset, base, d_file,
                ".sha256sum of denoland/deno release" + (" == GitHub asset digest" if d_gh else ""),
                "MIT (see LICENSES/deno/LICENSE.md)", f"https://github.com/denoland/deno/tree/{tag}",
                {"tag": tag})


def plan_aria2c() -> Plan:
    tag = gh_latest_tag("aria2/aria2")
    m = re.fullmatch(r"release-(\d+\.\d+\.\d+)", tag)
    if not m:
        raise RuntimeError(f"unexpected aria2 tag {tag}")
    ver = m.group(1)
    asset = f"aria2-{ver}-win-64bit-build1.zip"
    pinned = (group_cfg("aria2c").get("pinned_sha256") or {}).get(asset)
    d_gh = gh_asset_digest("aria2/aria2", tag, asset)
    if d_gh and pinned and d_gh != pinned:
        raise VerifyError(f"aria2 {ver}: GitHub digest {d_gh} != reviewed pin {pinned}")
    if not (d_gh or pinned):
        raise VerifyError(f"aria2 {ver}: upstream publishes no checksum and no reviewed hash is pinned in "
                          f"components.json for {asset} - refusing")
    method = ("GitHub asset digest" if d_gh else
              "reviewed SHA256 pinned in components.json (aria2 publishes no checksum file)")
    return Plan("aria2c", ver, asset, f"https://github.com/aria2/aria2/releases/download/{tag}/{asset}",
                d_gh or pinned, method, "GPL-2.0-or-later with OpenSSL exception (see LICENSES/aria2c)",
                f"https://github.com/aria2/aria2/releases/download/{tag}/aria2-{ver}.tar.xz")


def plan_exiftool() -> Plan:
    ver = fetch("https://exiftool.org/ver.txt", 100).decode().strip()
    if not re.fullmatch(r"\d+\.\d+", ver):
        raise RuntimeError(f"unexpected ExifTool version {ver!r}")
    asset = f"exiftool-{ver}_64.zip"
    sums = fetch("https://exiftool.org/checksums.txt", 100_000).decode("utf-8", "replace")
    m = re.search(r"SHA2-256\(" + re.escape(asset) + r"\)=\s*([0-9a-f]{64})", sums)
    if not m:
        raise VerifyError(f"ExifTool {ver}: {asset} not in exiftool.org checksums.txt")
    return Plan("exiftool", ver, asset, f"https://downloads.sourceforge.net/project/exiftool/{asset}",
                m.group(1), "exiftool.org checksums.txt (SHA2-256)",
                "ExifTool: same terms as Perl (Artistic or GPL); bundled Strawberry Perl licenses inside "
                "exiftool_files/Licenses_Strawberry_Perl.zip", f"https://exiftool.org/Image-ExifTool-{ver}.tar.gz")


def _pypi(pkg: str, version: str) -> dict:
    d = fetch_json(f"https://pypi.org/pypi/{pkg}/{version}/json")
    if d["info"]["version"] != version:
        raise VerifyError(f"PyPI returned {pkg} {d['info']['version']}, wanted {version}")
    wheels = [u for u in d["urls"] if u["filename"] == f"{pkg.replace('-', '_')}-{version}-py3-none-any.whl"]
    if len(wheels) != 1:
        raise VerifyError(f"{pkg} {version}: no single pure-python wheel on PyPI")
    if any(u.get("yanked") for u in wheels):
        raise VerifyError(f"{pkg} {version}: wheel is yanked")
    return {"info": d["info"], "wheel": wheels[0]}


def ejs_pin_of(requires: list[str]) -> str:
    pins = set()
    for r in requires or []:
        m = re.match(r"yt-dlp-ejs\s*==\s*([0-9][0-9.]*)\s*;.*extra\s*==\s*\"(default|pin)\"", r)
        if m:
            pins.add(m.group(1))
    if len(pins) != 1:
        raise VerifyError(f"yt-dlp does not pin exactly one yt-dlp-ejs version: {sorted(pins)}")
    return pins.pop()


_PAIR: dict = {}


def pair_info() -> dict:
    """yt_dlp version = yt-dlp's latest stable tag; yt_dlp_ejs = the exact version it pins."""
    if not _PAIR:
        ver = ytdlp_stable_tag()
        y = _pypi("yt-dlp", ver)
        pin = ejs_pin_of(y["info"].get("requires_dist"))
        e = _pypi("yt-dlp-ejs", pin)
        for name, info in (("yt-dlp", y["info"]), ("yt-dlp-ejs", e["info"])):
            if not python_ok(info.get("requires_python") or ""):
                raise VerifyError(f"{name} needs Python {info.get('requires_python')}, Clipro uses 3.12")
        _PAIR.update(ytdlp=ver, ejs=pin, y=y, e=e)
    return _PAIR


def plan_yt_dlp() -> Plan:
    p = pair_info()
    w = p["y"]["wheel"]
    return Plan("yt_dlp", p["ytdlp"], w["filename"], w["url"], w["digests"]["sha256"].lower(),
                "PyPI sha256 digest + wheel RECORD (every file)", "Unlicense",
                f"https://github.com/yt-dlp/yt-dlp/tree/{p['ytdlp']}",
                {"compatible_with": {"yt_dlp_ejs": p["ejs"]}, "requires_python": p["y"]["info"].get("requires_python")})


def plan_yt_dlp_ejs() -> Plan:
    p = pair_info()
    w = p["e"]["wheel"]
    return Plan("yt_dlp_ejs", p["ejs"], w["filename"], w["url"], w["digests"]["sha256"].lower(),
                "PyPI sha256 digest + wheel RECORD (every file)", "Unlicense AND MIT AND ISC",
                f"https://github.com/yt-dlp/ejs/tree/{p['ejs']}",
                {"pinned_by": {"yt_dlp": p["ytdlp"]}, "requires_python": p["e"]["info"].get("requires_python")})


PLANNERS = {"yt-dlp-exe": plan_ytdlp_exe, "ffmpeg": plan_ffmpeg, "deno": plan_deno, "aria2c": plan_aria2c,
            "exiftool": plan_exiftool, "yt_dlp": plan_yt_dlp, "yt_dlp_ejs": plan_yt_dlp_ejs}


# --------------------------------------------------------------------------- layouts
def layout_ytdlp_exe(plan: Plan, blob: bytes) -> dict[str, bytes]:
    if len(blob) < 100_000:
        raise VerifyError("yt-dlp.exe too small")
    check_pe_x64("yt-dlp.exe", blob)
    return {"yt-dlp.exe": blob}


def layout_ffmpeg(plan: Plan, blob: bytes) -> dict[str, bytes]:
    files = read_zip(blob)
    pre = f"ffmpeg-{plan.version}-essentials_build/"
    out = {}
    for exe in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):     # atomic group: all three or nothing
        data = files.get(pre + "bin/" + exe)
        if data is None:
            raise VerifyError(f"FFmpeg archive is missing bin/{exe}")
        check_pe_x64(exe, data)
        out[exe] = data
    extra_bin = [n for n in files if n.startswith(pre + "bin/") and n[len(pre) + 4:] not in out]
    if extra_bin:
        raise VerifyError(f"unexpected files in FFmpeg bin/: {extra_bin}")
    for lic, dst in (("LICENSE", "LICENSES/ffmpeg/LICENSE.txt"), ("README.txt", "LICENSES/ffmpeg/README.txt")):
        if pre + lic not in files:
            raise VerifyError(f"FFmpeg archive is missing {lic}")
        out[dst] = files[pre + lic]
    return out


def layout_deno(plan: Plan, blob: bytes) -> dict[str, bytes]:
    files = read_zip(blob)
    if set(files) != {"deno.exe"}:
        raise VerifyError(f"Deno archive should contain only deno.exe, has {sorted(files)}")
    check_pe_x64("deno.exe", files["deno.exe"])
    lic = fetch(f"https://raw.githubusercontent.com/denoland/deno/{plan.extra['tag']}/LICENSE.md", 100_000)
    if b"MIT" not in lic:
        raise VerifyError("Deno LICENSE.md does not look like the MIT license")
    return {"deno.exe": files["deno.exe"], "LICENSES/deno/LICENSE.md": lic}


def layout_aria2c(plan: Plan, blob: bytes) -> dict[str, bytes]:
    files = strip_prefix(read_zip(blob), f"aria2-{plan.version}-win-64bit-build1/")
    if "aria2c.exe" not in files:
        raise VerifyError("aria2 archive is missing aria2c.exe")
    check_pe_x64("aria2c.exe", files["aria2c.exe"])
    out = {"aria2c.exe": files["aria2c.exe"]}
    for lic in ("COPYING", "LICENSE.OpenSSL", "AUTHORS", "README.mingw"):
        if lic not in files:
            raise VerifyError(f"aria2 archive is missing {lic}")
        out[f"LICENSES/aria2c/{lic}"] = files[lic]
    return out


EXIFTOOL_REQUIRED = ("exiftool_files/exiftool.pl", "exiftool_files/perl.exe", "exiftool_files/LICENSE",
                     "exiftool_files/lib/Image/ExifTool.pm")


def layout_exiftool(plan: Plan, blob: bytes) -> dict[str, bytes]:
    files = strip_prefix(read_zip(blob), f"exiftool-{plan.version}_64/")
    exe = files.pop("exiftool(-k).exe", None)
    if exe is None:
        raise VerifyError("ExifTool archive is missing exiftool(-k).exe")
    check_pe_x64("exiftool(-k).exe", exe)
    readme = files.pop("README.txt", None)
    others = [n for n in files if not n.startswith("exiftool_files/")]
    if others:
        raise VerifyError(f"unexpected ExifTool top-level files: {others}")
    missing = [r for r in EXIFTOOL_REQUIRED if r not in files]
    if not any(re.fullmatch(r"exiftool_files/perl5\d+\.dll", n) for n in files):
        missing.append("exiftool_files/perl5xx.dll")
    if missing:
        raise VerifyError(f"ExifTool supporting files missing: {missing}")
    for n, data in files.items():
        if n.lower().endswith((".exe", ".dll")):
            check_pe_x64(n, data)
    out = {"exiftool.exe": exe}                 # renamed per ExifTool's own Windows instructions
    out.update(files)
    if readme is not None:
        out["LICENSES/exiftool/README.txt"] = readme
    return out


def _wheel_layout(plan: Plan, blob: bytes, dist: str, pkg: str) -> dict[str, bytes]:
    files = verify_wheel(blob, dist, plan.version)
    out = {}
    for n, data in files.items():
        if n.startswith(f"{dist}-{plan.version}.data/"):
            continue                              # shell completions / man page: not needed
        if not (n.startswith(pkg + "/") or n.startswith(f"{dist}-{plan.version}.dist-info/")):
            raise VerifyError(f"unexpected wheel entry {n}")
        out[n] = data
    return out


def layout_yt_dlp(plan: Plan, blob: bytes) -> dict[str, bytes]:
    out = _wheel_layout(plan, blob, "yt_dlp", "yt_dlp")
    for req in ("yt_dlp/__init__.py", "yt_dlp/version.py", "yt_dlp/YoutubeDL.py"):
        if req not in out:
            raise VerifyError(f"yt_dlp wheel is missing {req}")
    if f"__version__ = '{plan.version}'".encode() not in out["yt_dlp/version.py"]:
        raise VerifyError("yt_dlp/version.py does not match the release version")
    meta = out[f"yt_dlp-{plan.version}.dist-info/METADATA"].decode("utf-8", "replace")
    if f"yt-dlp-ejs=={plan.extra['compatible_with']['yt_dlp_ejs']}" not in meta.replace(" ", ""):
        raise VerifyError("wheel METADATA does not pin the expected yt-dlp-ejs version")
    return out


def layout_yt_dlp_ejs(plan: Plan, blob: bytes) -> dict[str, bytes]:
    out = _wheel_layout(plan, blob, "yt_dlp_ejs", "yt_dlp_ejs")
    js = [n for n in out if n.startswith("yt_dlp_ejs/") and n.endswith(".js")]
    for req in ("yt_dlp_ejs/__init__.py", "yt_dlp_ejs/_version.py", "yt_dlp_ejs/yt/solver/core.min.js",
                "yt_dlp_ejs/yt/solver/lib.min.js"):
        if req not in out:
            raise VerifyError(f"yt_dlp_ejs wheel is missing {req} (JS resources incomplete)")
    if any(len(out[n]) == 0 for n in js):
        raise VerifyError("empty JS resource in yt_dlp_ejs")
    if plan.version.encode() not in out["yt_dlp_ejs/_version.py"]:
        raise VerifyError("yt_dlp_ejs/_version.py does not match the release version")
    return out


LAYOUTS = {"yt-dlp-exe": layout_ytdlp_exe, "ffmpeg": layout_ffmpeg, "deno": layout_deno,
           "aria2c": layout_aria2c, "exiftool": layout_exiftool, "yt_dlp": layout_yt_dlp,
           "yt_dlp_ejs": layout_yt_dlp_ejs}


# --------------------------------------------------------------------------- signing
def _private_key():
    encoded = os.environ.get("CLIPRO_ED25519_PRIVATE_KEY_B64", "")
    if not encoded:
        raise RuntimeError("Missing signing secret; refusing to publish")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key_bytes = base64.b64decode(encoded, validate=True)
    if len(key_bytes) != 32:
        raise RuntimeError("Bad private key size")
    return Ed25519PrivateKey.from_private_bytes(key_bytes)


def check_signing_key() -> str:
    """The secret must match the committed keys/publisher.pub (the key pinned in the client)."""
    from cryptography.hazmat.primitives import serialization
    key = _private_key()
    pub = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    if not PUBLIC_KEY_FILE.is_file():
        raise RuntimeError("keys/publisher.pub is missing; refusing to publish")
    expected = PUBLIC_KEY_FILE.read_text(encoding="ascii").strip()
    if pub != expected:
        raise RuntimeError("Signing secret does not match keys/publisher.pub; refusing to publish")
    return pub


def sign(value: dict) -> str:
    private_key = _private_key()
    return base64.b64encode(private_key.sign(canonical(value))).decode("ascii")


# --------------------------------------------------------------------------- build
def create_release_files(directory: Path, version: str, assets: dict) -> dict:
    """Original yt-dlp.exe pilot packaging (unchanged)."""
    checksums = fetch(assets["SHA2-256SUMS"], 100_000).decode("utf-8")
    expected = checksum_from_sumfile(checksums, "yt-dlp.exe")
    binary = fetch(assets["yt-dlp.exe"])
    if sha(binary) != expected:
        raise RuntimeError("Upstream checksum mismatch")
    if len(binary) < 100_000 or binary[:2] != b"MZ":
        raise RuntimeError("Not a plausible Windows PE")
    zip_data = zip_binary(binary)
    filename = f"clipro-yt-dlp-exe-{version}-win64.zip"
    manifest = {
        "schema": 1,
        "channel": "test",
        "component_id": "yt-dlp-exe",
        "version": version,
        "archive_name": filename,
        "archive_sha256": sha(zip_data),
        "archive_size": len(zip_data),
        "files": [{"path": "yt-dlp.exe", "sha256": sha(binary), "size": len(binary)}],
        "upstream": {"repository": UPSTREAM, "version": version},
        "requires_manual_client_integration": True,
    }
    signature = sign(manifest)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(zip_data)
    (directory / "manifest.json").write_bytes(canonical(manifest))
    (directory / "manifest.sig").write_text(signature + "\n", encoding="ascii")
    return manifest


def archive_name(plan: Plan) -> str:
    suffix = "py3" if plan.cid in ("yt_dlp", "yt_dlp_ejs") else "win64"
    return f"clipro-{plan.cid}-{plan.version}-{suffix}.zip"


def build_group(plan: Plan, directory: Path, cache: Cache, sign_it: bool = True) -> dict:
    if plan.cid == "yt-dlp-exe":
        # Same packaging as the published pilot, so tags/assets stay consistent.
        version, assets = release_info()
        if version != plan.version:
            raise VerifyError(f"yt-dlp tag changed during run: {plan.version} -> {version}")
        if not sign_it:
            raise RuntimeError("yt-dlp-exe build needs the signing key")
        return create_release_files(directory, version, assets)
    blob = cache.get(plan.url, plan.asset, plan.sha256)
    files = LAYOUTS[plan.cid](plan, blob)
    zip_data = zip_files(files)
    name = archive_name(plan)
    manifest = {
        "schema": 1,
        "channel": "test",
        "component_id": plan.cid,
        "version": plan.version,
        "archive_name": name,
        "archive_sha256": sha(zip_data),
        "archive_size": len(zip_data),
        "files": [{"path": p, "sha256": sha(files[p]), "size": len(files[p])} for p in sorted(files)],
        "upstream": {"asset": plan.asset, "url": plan.url, "sha256": plan.sha256,
                     "checksum_method": plan.method, "version": plan.version, "source": plan.source},
        "license": plan.license,
        "requires_manual_client_integration": True,
    }
    if plan.extra.get("compatible_with"):
        manifest["compatible_with"] = plan.extra["compatible_with"]
    if plan.extra.get("pinned_by"):
        manifest["pinned_by"] = plan.extra["pinned_by"]
    if plan.extra.get("requires_python"):
        manifest["requires_python"] = plan.extra["requires_python"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(zip_data)
    (directory / "manifest.json").write_bytes(canonical(manifest))
    if sign_it:
        (directory / "manifest.sig").write_text(sign(manifest) + "\n", encoding="ascii")
    return manifest


# --------------------------------------------------------------------------- GitHub publish
def gh(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def release_state(repo: str, tag: str) -> str:
    """'none', 'draft' or 'published' for a tag in OUR repository."""
    r = gh("release", "view", tag, "--repo", repo, "--json", "isDraft")
    if r.returncode != 0:
        if "not found" in (r.stderr or "").lower() or "release not found" in (r.stderr or "").lower():
            return "none"
        tags = gh("api", f"repos/{repo}/git/refs/tags/{tag}")
        if tags.returncode == 0:
            raise RuntimeError(f"tag {tag} exists without a release; refusing to reuse it")
        return "none"
    return "draft" if json.loads(r.stdout).get("isDraft") else "published"


def publish_group(repo: str, plan: Plan, out_root: Path, cache: Cache) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("Invalid destination repository")
    tag = plan.tag
    state = release_state(repo, tag)
    if state == "published":
        print("ALREADY_PUBLISHED", tag)          # never overwritten, nothing downloaded
        return "already-published"
    if state == "draft":
        # A draft was never public (an earlier run failed mid-upload): remove and rebuild it.
        r = gh("release", "delete", tag, "--repo", repo, "--yes")
        if r.returncode:
            raise RuntimeError(f"cannot remove incomplete draft {tag}: {r.stderr[-300:]}")
        print("REMOVED_INCOMPLETE_DRAFT", tag)
    directory = out_root / tag
    manifest = build_group(plan, directory, cache)
    names = [str(directory / manifest["archive_name"]), str(directory / "manifest.json"),
             str(directory / "manifest.sig")]
    title = f"Clipro {plan.cid} {plan.version} TEST"
    notes = (f"TEST ONLY - not approved for production auto-update.\n\nUpstream: {plan.url}\n"
             f"Upstream SHA256: {plan.sha256} ({plan.method})\nLicense: {plan.license}\nSource: {plan.source}\n")
    r = gh("release", "create", tag, *names, "--repo", repo, "--title", title, "--prerelease", "--draft",
           "--notes", notes)
    if r.returncode:
        raise RuntimeError("GitHub publish failed: " + r.stderr[-700:])
    view = gh("release", "view", tag, "--repo", repo, "--json", "assets")
    if view.returncode:
        raise RuntimeError("cannot read back draft assets: " + view.stderr[-300:])
    got = {a["name"]: a["size"] for a in json.loads(view.stdout)["assets"]}
    for path in names:
        p = Path(path)
        if got.get(p.name) != p.stat().st_size:
            raise RuntimeError(f"uploaded size mismatch for {p.name}; draft left unpublished")
    r = gh("release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease")
    if r.returncode:
        raise RuntimeError("could not publish draft: " + r.stderr[-300:])
    print("PUBLISHED_TEST_RELEASE", tag)
    return "published"


# --------------------------------------------------------------------------- main
def selected(group: str) -> list[str]:
    if group == "all":
        return list(GROUP_ORDER)
    if group == "py-pair":
        return ["yt_dlp", "yt_dlp_ejs"]
    if group not in PLANNERS:
        raise RuntimeError(f"unknown group {group}")
    return [group]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "build", "publish-test"], default="check")
    parser.add_argument("--group", default="all", help="all | py-pair | " + " | ".join(GROUP_ORDER))
    parser.add_argument("--repo", default="bsnsjajabsnsja-tech/clipro-components")
    parser.add_argument("--output", default="output")
    args = parser.parse_args(argv)
    groups = selected(args.group)
    if args.mode == "publish-test" and "yt_dlp" in groups and "yt_dlp_ejs" not in groups:
        groups.append("yt_dlp_ejs")         # never publish yt_dlp without its exact ejs pair
    out_root = Path(args.output)
    cache = Cache(out_root / "cache")
    failures = 0
    if args.mode != "check":
        try:
            pub = check_signing_key()
            print("SIGNING_KEY_MATCHES keys/publisher.pub", pub[:8] + "...")
        except Exception as exc:  # noqa: BLE001
            print("FAILED:", exc, file=sys.stderr)
            return 1
    for cid in groups:
        try:
            plan = PLANNERS[cid]()
            print(f"OK_UPSTREAM {cid} {plan.version} asset={plan.asset} sha256={plan.sha256} [{plan.method}]"
                  f" tag={plan.tag}")
            if args.mode == "build":
                m = build_group(plan, out_root / plan.tag, cache)
                print(f"BUILT {plan.tag} {m['archive_name']} {m['archive_size']} bytes "
                      f"sha256={m['archive_sha256']} files={len(m['files'])}")
            elif args.mode == "publish-test":
                publish_group(args.repo, plan, out_root, cache)
        except (KeyError, ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            failures += 1
            print(f"FAILED {cid}: {exc}", file=sys.stderr)
            if args.mode == "publish-test" and cid == "yt_dlp":
                print("STOP: yt_dlp failed, so yt_dlp_ejs is not published alone", file=sys.stderr)
                break
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
