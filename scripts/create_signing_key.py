"""Generate an Ed25519 signing key on a trusted computer. Never commit private key."""
import base64
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
private_path = ROOT / ".secrets" / "publisher.key"
public_path = ROOT / "keys" / "publisher.pub"
if private_path.exists() or public_path.exists():
    raise SystemExit("Existing key file: refusing overwrite")
private_path.parent.mkdir(parents=True, exist_ok=True)
public_path.parent.mkdir(parents=True, exist_ok=True)
key = Ed25519PrivateKey.generate()
private_bytes = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
public_bytes = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
private_path.write_text(base64.b64encode(private_bytes).decode() + "\n")
public_path.write_text(base64.b64encode(public_bytes).decode() + "\n")
print("Generated secret in .secrets/publisher.key — NEVER commit, paste, or share")
print("Public key generated in keys/publisher.pub — safe to commit after authentication")
