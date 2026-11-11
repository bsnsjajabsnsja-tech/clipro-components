# Clipro Component Releases — Test Pilot

This repository is the **component publisher**, not the Clipro application or its source code.

## Current status
- **Daily 02:20 UTC:** checks official `yt-dlp/yt-dlp` stable release metadata. **Does not auto-publish** while trust and compatibility checks are being tested.
- **Manual test release:** optional GitHub Actions `publish-test` creates a *prerelease* containing verified `yt-dlp.exe`, a ZIP, a JSON manifest and its Ed25519 signature. Requires a securely configured signing key.
- **Six other groups pending:** FFmpeg, Deno, aria2c, ExifTool, Python yt_dlp, Python yt_dlp_ejs.
- **No Clipro client integration yet.** Test ZIPs must not be used as unverified automatic production updates.

## Sign a pilot release
1. Generate a private/public Ed25519 pair locally; never commit or share private key.
2. Add `CLIPRO_ED25519_PRIVATE_KEY_B64` as a repository Actions secret.
3. Pin the public key in the client and publish its authenticated fingerprint through a trusted release process.
4. Run GitHub Actions → Clipro Component Pilot → Run workflow → `publish-test`.

## Safety design
- A publisher must verify **upstream file authenticity**, hashes, expected file layout, licenses, and compatibility before releasing.
- ZIP and SHA256 alone are not authenticity proof if an attacker can replace both ZIP and checksum.
- The **client** must verify a trusted pinned Ed25519 signature, archive SHA256, extracted-file hashes, perform staged activation with backup and rollback, and never delete a healthy component before verification.
- GitHub hosting does **not** guarantee antivirus approval; Kaspersky findings remain a separate release blocker.
- Never put GitHub write tokens, signing private keys, user cookies or license secrets in Clipro client.

Local checks: `python -m unittest discover -s tests -v`.

See `components.json` for rollout status.
