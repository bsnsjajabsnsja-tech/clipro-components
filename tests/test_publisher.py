import hashlib
import io
import unittest
import zipfile
from scripts import publisher as p

class PublisherTests(unittest.TestCase):
    def test_checksum_parser(self):
        digest = "a" * 64
        self.assertEqual(p.checksum_from_sumfile(f"{digest}  yt-dlp.exe\n", "yt-dlp.exe"), digest)
        with self.assertRaises(RuntimeError):
            p.checksum_from_sumfile(f"{digest}  yt-dlp.exe\n", "missing.exe")
    def test_archive_contents(self):
        blob = p.zip_binary(b"MZ" + b"X" * 10)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            self.assertEqual(z.namelist(), ["yt-dlp.exe"])
    def test_hash(self):
        self.assertEqual(p.sha(b"abc"), hashlib.sha256(b"abc").hexdigest())
    def test_canonical(self):
        self.assertEqual(p.canonical({"a":1,"b":2}),p.canonical({"b":2,"a":1}))


# ---------------------------------------------------------------- 7-group additions
import base64 as _b64
import csv as _csv
import os as _os
import struct as _struct
from pathlib import Path as _Path
from unittest import mock as _mock


def _pe(size=400):
    b = bytearray(size)
    b[:2] = b"MZ"
    _struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    _struct.pack_into("<H", b, 0x84, 0x8664)
    return bytes(b)


def _zip(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return out.getvalue()


def _wheel(dist, ver, files, extra_meta=""):
    files = dict(files)
    files[f"{dist}-{ver}.dist-info/METADATA"] = f"Metadata-Version: 2.4\nName: {dist}\nVersion: {ver}\n{extra_meta}".encode()
    rows = []
    for n, d in files.items():
        h = _b64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b"=").decode()
        rows.append([n, "sha256=" + h, str(len(d))])
    rows.append([f"{dist}-{ver}.dist-info/RECORD", "", ""])
    s = io.StringIO()
    _csv.writer(s).writerows(rows)
    files[f"{dist}-{ver}.dist-info/RECORD"] = s.getvalue().encode()
    return _zip(files)


class SevenGroupTests(unittest.TestCase):
    def test_pe_check(self):
        p.check_pe_x64("a.exe", _pe())
        bad = bytearray(_pe()); _struct.pack_into("<H", bad, 0x84, 0x014C)
        with self.assertRaises(p.VerifyError):
            p.check_pe_x64("a.exe", bytes(bad))
        with self.assertRaises(p.VerifyError):
            p.check_pe_x64("a.exe", b"not a pe" * 100)

    def test_unsafe_upstream_paths_rejected(self):
        for n in ("../x.exe", "/abs.exe", "a/../../b", "C:/x"):
            with self.assertRaises(p.VerifyError):
                p.safe_member(n)

    def test_deterministic_zip(self):
        f = {"b.exe": b"1", "a/c.txt": b"2"}
        self.assertEqual(p.zip_files(f), p.zip_files(dict(reversed(list(f.items())))))

    def test_ffmpeg_atomic_group(self):
        plan = p.Plan("ffmpeg", "9.0.2", "x", "u", "0" * 64, "m", "l", "s")
        pre = "ffmpeg-9.0.2-essentials_build/"
        full = {pre + "bin/ffmpeg.exe": _pe(), pre + "bin/ffprobe.exe": _pe(), pre + "bin/ffplay.exe": _pe(),
                pre + "LICENSE": b"GPL", pre + "README.txt": b"r", pre + "doc/x.html": b"d"}
        out = p.layout_ffmpeg(plan, _zip(full))
        self.assertEqual(sorted(out), ["LICENSES/ffmpeg/LICENSE.txt", "LICENSES/ffmpeg/README.txt",
                                       "ffmpeg.exe", "ffplay.exe", "ffprobe.exe"])
        del full[pre + "bin/ffplay.exe"]
        with self.assertRaises(p.VerifyError):
            p.layout_ffmpeg(plan, _zip(full))

    def test_exiftool_requires_supporting_files(self):
        plan = p.Plan("exiftool", "13.63", "x", "u", "0" * 64, "m", "l", "s")
        pre = "exiftool-13.63_64/"
        full = {pre + "exiftool(-k).exe": _pe(), pre + "README.txt": b"r",
                pre + "exiftool_files/exiftool.pl": b"pl", pre + "exiftool_files/perl.exe": _pe(),
                pre + "exiftool_files/perl532.dll": _pe(), pre + "exiftool_files/LICENSE": b"l",
                pre + "exiftool_files/lib/Image/ExifTool.pm": b"pm"}
        out = p.layout_exiftool(plan, _zip(full))
        self.assertIn("exiftool.exe", out)
        self.assertNotIn("exiftool(-k).exe", out)
        del full[pre + "exiftool_files/lib/Image/ExifTool.pm"]
        with self.assertRaises(p.VerifyError):
            p.layout_exiftool(plan, _zip(full))

    def test_wheel_record_verified(self):
        w = _wheel("yt_dlp_ejs", "0.8.0", {"yt_dlp_ejs/__init__.py": b"", "yt_dlp_ejs/_version.py": b"version = '0.8.0'",
                                          "yt_dlp_ejs/yt/solver/core.min.js": b"x", "yt_dlp_ejs/yt/solver/lib.min.js": b"y"})
        plan = p.Plan("yt_dlp_ejs", "0.8.0", "x", "u", "0" * 64, "m", "l", "s")
        out = p.layout_yt_dlp_ejs(plan, w)
        self.assertIn("yt_dlp_ejs/yt/solver/core.min.js", out)
        # tamper one file after RECORD was written
        with zipfile.ZipFile(io.BytesIO(w)) as z:
            ents = {n: z.read(n) for n in z.namelist()}
        ents["yt_dlp_ejs/yt/solver/core.min.js"] = b"evil"
        with self.assertRaises(p.VerifyError):
            p.layout_yt_dlp_ejs(plan, _zip(ents))

    def test_ejs_js_resources_required(self):
        w = _wheel("yt_dlp_ejs", "0.8.0", {"yt_dlp_ejs/__init__.py": b"", "yt_dlp_ejs/_version.py": b"0.8.0"})
        plan = p.Plan("yt_dlp_ejs", "0.8.0", "x", "u", "0" * 64, "m", "l", "s")
        with self.assertRaises(p.VerifyError):
            p.layout_yt_dlp_ejs(plan, w)

    def test_ejs_pin_parsing(self):
        req = ['yt-dlp-ejs==0.8.0; extra == "default"', 'yt-dlp-ejs==0.8.0; extra == "pin"', "x; extra == 'y'"]
        self.assertEqual(p.ejs_pin_of(req), "0.8.0")
        with self.assertRaises(p.VerifyError):
            p.ejs_pin_of(['yt-dlp-ejs==0.8.0; extra == "default"', 'yt-dlp-ejs==0.9.0; extra == "pin"'])
        with self.assertRaises(p.VerifyError):
            p.ejs_pin_of([])

    def test_python_requires(self):
        self.assertTrue(p.python_ok(">=3.10"))
        self.assertFalse(p.python_ok(">=3.13"))

    def test_yt_dlp_pair_metadata_checked(self):
        files = {"yt_dlp/__init__.py": b"", "yt_dlp/YoutubeDL.py": b"",
                 "yt_dlp/version.py": b"__version__ = '2026.10.26'\n"}
        plan = p.Plan("yt_dlp", "2026.10.26", "x", "u", "0" * 64, "m", "l", "s",
                      {"compatible_with": {"yt_dlp_ejs": "0.8.0"}})
        good = _wheel("yt_dlp", "2026.10.26", files, 'Requires-Dist: yt-dlp-ejs==0.8.0; extra == "default"\n')
        self.assertIn("yt_dlp/version.py", p.layout_yt_dlp(plan, good))
        bad = _wheel("yt_dlp", "2026.10.26", files, 'Requires-Dist: yt-dlp-ejs==0.7.0; extra == "default"\n')
        with self.assertRaises(p.VerifyError):
            p.layout_yt_dlp(plan, bad)

    def test_signing_key_must_match_committed_public_key(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization as s
        import tempfile
        k = Ed25519PrivateKey.generate()
        priv = _b64.b64encode(k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())).decode()
        pub = _b64.b64encode(k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode()
        with tempfile.TemporaryDirectory() as d:
            f = _Path(d) / "publisher.pub"
            with _mock.patch.object(p, "PUBLIC_KEY_FILE", f), \
                    _mock.patch.dict(_os.environ, {"CLIPRO_ED25519_PRIVATE_KEY_B64": priv}):
                with self.assertRaises(RuntimeError):          # missing pub file
                    p.check_signing_key()
                f.write_text(pub + "\n")
                self.assertEqual(p.check_signing_key(), pub)
                f.write_text(_b64.b64encode(b"\1" * 32).decode())
                with self.assertRaises(RuntimeError):          # mismatch
                    p.check_signing_key()
            with _mock.patch.dict(_os.environ, {"CLIPRO_ED25519_PRIVATE_KEY_B64": ""}):
                with self.assertRaises(RuntimeError):
                    p.check_signing_key()

    def test_published_tag_is_never_rebuilt(self):
        plan = p.Plan("deno", "2.9.8", "a", "u", "0" * 64, "m", "l", "s", {"tag": "v2.9.8"})
        with _mock.patch.object(p, "release_state", return_value="published"), \
                _mock.patch.object(p, "build_group") as build, _mock.patch.object(p, "gh") as gh:
            self.assertEqual(p.publish_group("o/r", plan, _Path("x"), None), "already-published")
            build.assert_not_called()
            gh.assert_not_called()

    def test_group_selection_and_tags(self):
        self.assertEqual(p.selected("py-pair"), ["yt_dlp", "yt_dlp_ejs"])
        self.assertEqual(len(p.selected("all")), 7)
        self.assertEqual(p.Plan("yt_dlp_ejs", "0.8.0", "", "", "", "", "", "").tag, "clipro-yt_dlp_ejs-0.8.0-test")
        self.assertEqual(p.archive_name(p.Plan("ffmpeg", "9.0.2", "", "", "", "", "", "")),
                         "clipro-ffmpeg-9.0.2-win64.zip")
