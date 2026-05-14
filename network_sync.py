import os
import sqlite3
import json
import threading
import time
import base64
import requests

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ── Device Identity ───────────────────────────────────────────────────────────
# DEVICE_ID        : unique identifier, matches public key filename on gateway
# PRIVATE_KEY_PATH : this device's private key — never leaves this machine
#
# Production: load DEVICE_ID from keyring, restrict private key file permissions
DEVICE_ID        = "device_1_id"
PRIVATE_KEY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "keys", DEVICE_ID, "private_key.pem"
)

USE_HTTPS    = False   # ← flip to True after generating gateway cert
GATEWAY_CERT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway.crt")


def _load_private_key():
    if not os.path.exists(PRIVATE_KEY_PATH):
        raise FileNotFoundError(
            f"Private key not found at: {PRIVATE_KEY_PATH}\n"
            f"Run: python generate_device_keys.py --device-id {DEVICE_ID}"
        )
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)

_private_key = _load_private_key()


# ── RSA Signing ───────────────────────────────────────────────────────────────
def make_headers(payload: dict) -> dict:
    timestamp = str(int(time.time()))

    # Serialize ONCE — this exact string is what gets sent and what gets signed
    body    = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    message = f"{DEVICE_ID}.{timestamp}.{body}".encode()

    signature = _private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    return {
        "Content-Type": "application/json",
        "X-Device-ID":  DEVICE_ID,
        "X-Timestamp":  timestamp,
        "X-Signature":  base64.b64encode(signature).decode(),
    }


# ── Syncer ────────────────────────────────────────────────────────────────────
class AttendanceSyncer:
    def __init__(self, db_path, host_url, sync_interval=60.0):
        self.db_path       = db_path
        self.host_url      = host_url
        self.sync_interval = sync_interval
        self.syncing       = False
        self.running       = False
        self.thread        = None

        # HTTPS cert verification
        if USE_HTTPS and os.path.exists(GATEWAY_CERT):
            self.verify = GATEWAY_CERT
            print(f"HTTPS enabled. Cert pinned to: {GATEWAY_CERT}")
        elif USE_HTTPS:
            print("WARNING: USE_HTTPS=True but gateway.crt not found — using HTTP.")
            self.verify = False
        else:
            self.verify = False

    def start_syncing(self):
        if self.running:
            return
        self.running = True
        self.thread  = threading.Thread(target=self._sync_loop, daemon=True)
        self.thread.start()
        print(f"Network Syncer started → {self.host_url} every {self.sync_interval}s.")

    def stop_syncing(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            print("Network Syncer stopped.")

    def _sync_loop(self):
        while self.running:
            self.sync_with_host()
            for _ in range(int(self.sync_interval)):
                if not self.running:
                    break
                time.sleep(1)

    def sync_with_host(self):
        if not os.path.exists(self.db_path) or self.syncing or not self.host_url:
            return

        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            try:
                cursor.execute(
                    "ALTER TABLE attendance_logs ADD COLUMN synced INTEGER DEFAULT 0"
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

            cursor.execute(
                "SELECT rowid, person_name, timestamp FROM attendance_logs WHERE synced = 0"
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return

            self.syncing = True
            pending_ids  = [row[0] for row in rows]
            payload      = {
                "records": [
                    {"person_name": row[1], "timestamp": row[2]} for row in rows
                ]
            }

            try:
                body_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)

                response = requests.post(
                    self.host_url,
                    data=body_str,                  # ← raw string, not json=
                    headers=make_headers(payload),  # signs the same canonical form
                    verify=self.verify,
                    timeout=10
    )

                if response.status_code == 200:
                    self._on_success(pending_ids)
                else:
                    print(f"SYNC FAILED: {response.status_code} — {response.text}")
                    self.syncing = False

            except requests.RequestException as e:
                print(f"SYNC FAILED: Could not reach gateway ({e}). Will retry.")
                self.syncing = False

        except Exception as e:
            print(f"Sync Prep Error: {e}")
            self.syncing = False

    def _on_success(self, pending_ids):
        try:
            with sqlite3.connect(self.db_path) as conn:
                placeholders = ','.join(['?'] * len(pending_ids))
                conn.execute(
                    f"UPDATE attendance_logs SET synced = 1 WHERE rowid IN ({placeholders})",
                    pending_ids
                )
                conn.commit()
            print(f"SYNC SUCCESS: Uploaded {len(pending_ids)} records.")
        except Exception as e:
            print(f"Failed to update sync status: {e}")
        finally:
            self.syncing = False