#!/usr/bin/env python3
"""Conservative Clipro publisher: daily metadata check; manual signed test release.

Never executes downloaded binaries. Does not publish on scheduled runs.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import zipfile

UPSTREAM = "yt-dlp/yt-dlp"
API = f"https://api.github.com/repos/{UPSTREAM}/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "CliproComponentPublisher/0.1"}


def fetch(url: str, limit: int = 250_000_000) -> bytes:
    if not url.startswith("https://"):
        raise RuntimeError("HTTPS required")
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("Asset exceeds size limit")
    return data


def release_info():
    data = json.loads(fetch(API, 2_000_000))
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


def checksum_from_sumfile(data: str, name: str) -> str:
    for line in data.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2).lstrip("./") == name:
            return match.group(1).lower()
    raise RuntimeError("Missing checksum for " + name)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def zip_binary(binary: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("yt-dlp.exe", binary)
    return out.getvalue()


def sign(value: dict) -> str:
    encoded = os.environ.get("CLIPRO_ED25519_PRIVATE_KEY_B64", "")
    if not encoded:
        raise RuntimeError("Missing signing secret; refusing to publish")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key_bytes = base64.b64decode(encoded, validate=True)
    if len(key_bytes) != 32:
        raise RuntimeError("Bad private key size")
    private_key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    return base64.b64encode(private_key.sign(canonical(value))).decode("ascii")


def create_release_files(directory: Path, version: str, assets: dict) -> dict:
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


def publish_test(repo: str, directory: Path, version: str, assets: dict):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("Invalid destination repository")
    tag = f"clipro-yt-dlp-exe-{version}-test"
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", repo], capture_output=True, text=True)
    if existing.returncode == 0:
        print("ALREADY_PUBLISHED", tag)
        return
    result = create_release_files(directory, version, assets)
    names = [str(directory / result["archive_name"]), str(directory / "manifest.json"), str(directory / "manifest.sig")]
    command = ["gh", "release", "create", tag, *names, "--repo", repo,
               "--title", f"Clipro yt-dlp.exe {version} TEST",
               "--prerelease", "--notes", "TEST ONLY — not approved for production auto-update."]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError("GitHub publish failed: " + process.stderr[-700:])
    print("PUBLISHED_TEST_RELEASE", process.stdout.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["check", "publish-test"], default="check")
    parser.add_argument("--repo", default="bsnsjajabsnsja-tech/clipro-components")
    parser.add_argument("--output", default="output")
    args = parser.parse_args()
    try:
        version, assets = release_info()
        print("OFFICIAL_YT_DLP_LATEST", version)
        print("OTHER_SIX_GROUPS_PENDING_REVIEW")
        if args.mode == "publish-test":
            publish_test(args.repo, Path(args.output), version, assets)
        return 0
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print("FAILED:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
