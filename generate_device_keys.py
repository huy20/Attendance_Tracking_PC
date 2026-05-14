"""
Device Key Generation Script.
Run this ONCE per device during provisioning.

Usage:
    python generate_device_keys.py --device-id cam_lobby

Output:
    keys/cam_lobby/private_key.pem   ← stays on edge device ONLY
    keys/cam_lobby/public_key.pem    ← copy to gateway's public_keys/ folder

Never share or copy private_key.pem anywhere.
"""

import argparse
import os
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


def generate_keys(device_id: str, output_dir: str = "keys"):
    device_dir = os.path.join(output_dir, device_id)
    os.makedirs(device_dir, exist_ok=True)

    private_key_path = os.path.join(device_dir, "private_key.pem")
    public_key_path  = os.path.join(device_dir, "public_key.pem")

    # ── Generate RSA-2048 key pair ─────────────────────────────────────────────
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # ── Save private key ───────────────────────────────────────────────────────
    with open(private_key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
            # In production: use BestAvailableEncryption(passphrase)
        ))

    # ── Save public key ────────────────────────────────────────────────────────
    public_key = private_key.public_key()
    with open(public_key_path, "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))

    print(f"\nKeys generated for device: '{device_id}'")
    print(f"  Private key: {private_key_path}")
    print(f"    → Deploy this to the edge device ONLY. Never copy elsewhere.")
    print(f"  Public key:  {public_key_path}")
    print(f"    → Copy this to the gateway's public_keys/ folder.")
    print(f"\nGateway registration command:")
    print(f"  Copy {public_key_path} → gateway/public_keys/{device_id}.pem")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate RSA key pair for a device.")
    parser.add_argument("--device-id", required=True, help="Unique device identifier e.g. cam_lobby")
    parser.add_argument("--output-dir", default="keys", help="Output directory (default: keys/)")
    args = parser.parse_args()

    generate_keys(args.device_id, args.output_dir)