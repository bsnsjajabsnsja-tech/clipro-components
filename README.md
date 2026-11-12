# Clipro Component Releases — TEST channel

This repository is the **component publisher**, not the Clipro application or its source code.
Every release here is a **TEST prerelease**. It is not approved for production auto-update.

## Groups (7)
| id | upstream (Windows x64) | integrity check before publishing | license |
|---|---|---|---|
| `yt-dlp-exe` | yt-dlp/yt-dlp latest stable, `yt-dlp.exe` | `SHA2-256SUMS` of the same release | Unlicense (+ bundled third-party) |
| `ffmpeg` | GyanD/codexffmpeg = gyan.dev `release-version`, `ffmpeg-<v>-essentials_build.zip` | GitHub asset digest **and** gyan.dev `.sha256`, both required and equal | GPL-3.0-or-later build |
| `deno` | denoland/deno latest stable, `deno-x86_64-pc-windows-msvc.zip` | `.sha256sum` of the release (+ GitHub digest when present, must match) | MIT |
| `aria2c` | aria2/aria2 latest stable, `aria2-<v>-win-64bit-build1.zip` | **no upstream checksum exists**: reviewed SHA256 pinned in `components.json` | GPL-2.0-or-later + OpenSSL exception |
| `exiftool` | exiftool.org `ver.txt`, `exiftool-<v>_64.zip` (SourceForge) | SHA2-256 line in exiftool.org `checksums.txt` | Perl terms (Artistic/GPL) |
| `yt_dlp` | PyPI `yt-dlp==<yt-dlp latest stable tag>` wheel | PyPI sha256 + every file vs wheel `RECORD` | Unlicense |
| `yt_dlp_ejs` | PyPI `yt-dlp-ejs==<exact version pinned by that yt-dlp>` | PyPI sha256 + every file vs wheel `RECORD` | Unlicense AND MIT AND ISC |

Static checks only (nothing downloaded is ever executed). These checks are: PE header `MZ`/`PE`, machine AMD64 for every `.exe` and `.dll`, the required file layout, and wheel RECORD plus METADATA version. The FFmpeg group is atomic (ffmpeg, ffprobe and ffplay together). ExifTool must contain `exiftool_files/` complete (`exiftool.pl`, `perl.exe`, `perl5xx.dll`, `lib/Image/ExifTool.pm`). `exiftool(-k).exe` is shipped as `exiftool.exe`.

**Python pair:** the `yt_dlp` manifest carries `compatible_with.yt_dlp_ejs = <pin>`, read from the wheel METADATA (`yt-dlp-ejs==<pin>; extra == "default"`). `yt_dlp_ejs` is always the pinned version, never "latest". The client activates the pair only when both are installed and match.

## Release format
Tag `clipro-<id>-<version>-test` (prerelease). It has 3 assets: `<archive>.zip`, `manifest.json` (canonical JSON) and `manifest.sig` (Ed25519 over the exact manifest bytes). Expected manifests are in `docs/expected_manifests/`.

## Safety rules (enforced in `scripts/publisher.py`)
- The daily schedule runs **check only** and never publishes. Publishing is `workflow_dispatch` → `publish-test` only.
- No upstream checksum match means no publish.
- No secret, or a secret that does not match `keys/publisher.pub`, means no publish.
- A published tag is never overwritten or reused. A re-run prints `ALREADY_PUBLISHED` and downloads nothing for that group. A draft left by a failed run is never public; it is removed and rebuilt. Uploads are created as a draft, sizes are checked, and only then is the draft published.
- `yt_dlp` is never published without `yt_dlp_ejs`.
- No tokens or private keys in any client.

## Run
GitHub → Actions → **Clipro Component Pilot** → Run workflow → action `publish-test`, group `all` (or one group, or `py-pair`).
Local checks: `python -m unittest discover -s tests -v`. A dry build with a local test key: `python scripts/publisher.py --mode build --group ffmpeg --output out`. This needs `keys/publisher.pub` to match `CLIPRO_ED25519_PRIVATE_KEY_B64`.

## Open licensing item (before any production use)
FFmpeg (GPL-3.0) and aria2 (GPL-2.0) are redistributed in binary form. Each release names its corresponding source (manifest `upstream.source` and the release notes) and ships the upstream license texts. Before production, decide formally how source availability is provided: attach the source archives, or give a written offer. This TEST channel does not settle that question.
