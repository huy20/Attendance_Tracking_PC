import os
import sqlite3
import json
import threading
import time
import hmac
import hashlib
import requests

# ── Device Credentials (hardcoded for testing — use keyring in production) ──
DEVICE_ID     = "device_1_id"
DEVICE_SECRET = "device_1_secret"

if not DEVICE_ID or not DEVICE_SECRET:
    raise RuntimeError("Device credentials not found.")


# ── HMAC Header Builder ───────────────────────────────────────────────────────
def make_headers(payload):
    """
    Build signed headers for a sync request.
    The secret never travels over the network — only a signature derived from it.

    Signature covers: device_id + timestamp + body
      - device_id  : prevents using this signature for a different device
      - timestamp  : signature expires after MAX_AGE seconds (replay protection)
      - body       : prevents swapping the payload after signing
    """
    timestamp = str(int(time.time()))
    body      = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    message   = f"{DEVICE_ID}.{timestamp}.{body}".encode()
    signature = hmac.new(
        DEVICE_SECRET.encode(),
        message,
        hashlib.sha256
    ).hexdigest()

    return {
        "Content-Type": "application/json",
        "X-Device-ID":  DEVICE_ID,
        "X-Timestamp":  timestamp,
        "X-Signature":  signature,
    }


# ── Syncer ────────────────────────────────────────────────────────────────────
class AttendanceSyncer:
    def __init__(self, db_path, host_url, sync_interval=60.0, gateway_cert=None):
        self.db_path      = db_path
        self.host_url     = host_url
        self.sync_interval = sync_interval
        self.gateway_cert = gateway_cert  # path to gateway.crt for cert pinning
                                          # set to False to skip verification (testing only)
        self.syncing = False
        self.running = False
        self.thread  = None

    def start_syncing(self):
        if self.running:
            return
        self.running = True
        self.thread  = threading.Thread(target=self._sync_loop, daemon=True)
        self.thread.start()
        print(f"Network Syncer started. Syncing to {self.host_url} every {self.sync_interval}s.")

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
                cursor.execute("ALTER TABLE attendance_logs ADD COLUMN synced INTEGER DEFAULT 0")
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
                response = requests.post(
                    self.host_url,
                    json=payload,
                    headers=make_headers(payload),          # ← HMAC signed headers
                    verify=self.gateway_cert,               # ← cert pinning (or False for testing)
                    timeout=10
                )

                if response.status_code == 200:
                    self._on_success(pending_ids)
                else:
                    print(f"SYNC FAILED: Gateway returned {response.status_code} — {response.text}")
                    self.syncing = False

            except requests.RequestException as e:
                print(f"SYNC FAILED: Could not reach gateway ({e}). Will retry.")
                self.syncing = False

        except Exception as e:
            print(f"Sync Prep Error: {e}")
            self.syncing = False

    def _on_success(self, pending_ids):
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(pending_ids))
            cursor.execute(
                f"UPDATE attendance_logs SET synced = 1 WHERE rowid IN ({placeholders})",
                pending_ids
            )
            conn.commit()
            conn.close()
            print(f"SYNC SUCCESS: Uploaded {len(pending_ids)} records.")
        except Exception as e:
            print(f"Failed to update sync status: {e}")
        finally:
            self.syncing = False