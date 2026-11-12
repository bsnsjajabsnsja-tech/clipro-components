# Tags and archives

The real `publish-test` run produces these. Sizes and archive SHA256 come from a local dry build with a TEST key on 2026-11-12, from the same upstream files. The signature always differs. The archive bytes are expected to be identical because ZIP output is deterministic, but they can differ if the runner's zlib differs. The client trusts the signed manifest, not this table.

| tag | archive | bytes | files | first paths |
|---|---|---|---|---|
| `clipro-aria2c-1.37.0-test` | `clipro-aria2c-1.37.0-win64.zip` | 2461502 | 5 | LICENSES/aria2c/AUTHORS, LICENSES/aria2c/COPYING, LICENSES/aria2c/LICENSE.OpenSSL, LICENSES/aria2c/README.mingw … |
| `clipro-deno-2.9.8-test` | `clipro-deno-2.9.8-win64.zip` | 41820675 | 2 | LICENSES/deno/LICENSE.md, deno.exe |
| `clipro-exiftool-13.63-test` | `clipro-exiftool-13.63-win64.zip` | 11103562 | 511 | LICENSES/exiftool/README.txt, exiftool.exe, exiftool_files/LICENSE, exiftool_files/Licenses_Strawberry_Perl.zip … |
| `clipro-ffmpeg-9.0.2-test` | `clipro-ffmpeg-9.0.2-win64.zip` | 117087989 | 5 | LICENSES/ffmpeg/LICENSE.txt, LICENSES/ffmpeg/README.txt, ffmpeg.exe, ffplay.exe … |
| `clipro-yt-dlp-exe-2026.10.26-test` (ALREADY PUBLISHED, unchanged) | `clipro-yt-dlp-exe-2026.10.26-win64.zip` | 23358401 | 1 | yt-dlp.exe |
| `clipro-yt_dlp-2026.10.26-test` | `clipro-yt_dlp-2026.10.26-py3.zip` | 3071743 | 1052 | yt_dlp-2026.10.26.dist-info/METADATA, yt_dlp-2026.10.26.dist-info/RECORD, yt_dlp-2026.10.26.dist-info/WHEEL, yt_dlp-2026.10.26.dist-info/entry_points.txt … |
| `clipro-yt_dlp_ejs-0.8.0-test` | `clipro-yt_dlp_ejs-0.8.0-py3.zip` | 53443 | 10 | yt_dlp_ejs-0.8.0.dist-info/METADATA, yt_dlp_ejs-0.8.0.dist-info/RECORD, yt_dlp_ejs-0.8.0.dist-info/WHEEL, yt_dlp_ejs-0.8.0.dist-info/licenses/LICENSE … |

Upstream inputs (SHA256 verified 2026-11-12):
- ffmpeg-9.0.2-essentials_build.zip `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba` (GitHub digest = gyan.dev .sha256)
- deno-x86_64-pc-windows-msvc.zip v2.9.8 `cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf` (.sha256sum = GitHub digest)
- aria2-1.37.0-win-64bit-build1.zip `67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288` (pinned; same in downloads on 2026-11-10 and 2026-11-12)
- exiftool-13.63_64.zip `7a4bff4b1d6ee15adef882837847f76c0ed23228d294feed285939b6c9b293bd` (exiftool.org checksums.txt)
- yt_dlp-2026.10.26-py3-none-any.whl `a05c5b5d8562f7e8a62f5334348b406af4930d56d3a65f8021d14af0e22339ae` (PyPI)
- yt_dlp_ejs-0.8.0-py3-none-any.whl `79300e5fca7f937a1eeede11f0456862c1b41107ce1d726871e0207424f4bdb4` (PyPI; pinned by yt-dlp 2026.10.26)
