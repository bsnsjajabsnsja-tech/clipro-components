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
