from flask import Flask, request, jsonify
import requests
import hmac
import hashlib
import json
import time
import threading
import logging
import os

app = Flask(__name__)

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s %(message)s'
)

# ── Config ────────────────────────────────────────────────────────────────────
HOST_URL   = os.environ.get("HOST_URL",   "http://127.0.0.1:5050/sync")
HOST_TOKEN = os.environ.get("HOST_TOKEN", "host_token_123")

# Device registry — in production this comes from a DB
# Each device has its own secret — compromise of one doesn't affect others
DEVICE_REGISTRY = {
    "device_1_id": "device_1_secret",
    # "device_2_id": "device_2_secret",  # add more devices here
}

MAX_AGE = 30  # seconds — reject requests older than this

# ── Replay Window ─────────────────────────────────────────────────────────────
# Stores seen signatures so they can't be reused within the MAX_AGE window
_replay_window = {}
_replay_lock   = threading.Lock()

def clean_replay_window():
    """Remove expired entries from the replay window."""
    now = time.time()
    with _replay_lock:
        expired = [k for k, v in _replay_window.items() if v < now]
        for k in expired:
            del _replay_window[k]

def _cleanup_loop():
    while True:
        time.sleep(30)
        clean_replay_window()

threading.Thread(target=_cleanup_loop, daemon=True).start()


# ── HMAC Verification ─────────────────────────────────────────────────────────
def verify_request(req, data):
    """
    Verify the HMAC signature on an incoming request.
    Returns (device_id, None) on success or (None, error_message) on failure.
    """
    device_id = req.headers.get("X-Device-ID")
    timestamp = req.headers.get("X-Timestamp")
    signature = req.headers.get("X-Signature")

    # 1. All headers must be present
    if not all([device_id, timestamp, signature]):
        return None, "Missing authentication headers"

    # 2. Timestamp freshness check — prevent replay of old requests
    try:
        age = abs(int(time.time()) - int(timestamp))
    except ValueError:
        return None, "Invalid timestamp"

    if age > MAX_AGE:
        return None, f"Request expired ({age}s old, max {MAX_AGE}s)"

    # 3. Replay attack check — same signature can't be used twice
    sig_key = f"{device_id}.{signature}"
    with _replay_lock:
        if sig_key in _replay_window:
            logging.warning(f"Replay attack detected from device '{device_id}'")
            return None, "Duplicate request"
        # Register signature as used — expires after MAX_AGE
        _replay_window[sig_key] = time.time() + MAX_AGE

    # 4. Device must be registered
    secret = DEVICE_REGISTRY.get(device_id)
    if not secret:
        logging.warning(f"Unknown device: '{device_id}'")
        return None, "Unknown device"

    # 5. Recompute signature and compare
    #    Must use the exact same message format as network_sync.py:
    #    "{device_id}.{timestamp}.{canonical_body}"
    body     = json.dumps(data, separators=(',', ':'), sort_keys=True)
    message  = f"{device_id}.{timestamp}.{body}".encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    # compare_digest prevents timing attacks
    if not hmac.compare_digest(expected, signature):
        logging.warning(f"Invalid signature from device '{device_id}'")
        return None, "Invalid signature"

    return device_id, None


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/sync", methods=["POST"])
def sync():
    data = request.get_json()

    # 1. Verify HMAC signature
    device_id, error = verify_request(request, data)
    if error:
        return jsonify({"error": error}), 401

    # 2. Validate payload structure
    if not data or "records" not in data:
        return jsonify({"error": "Bad request"}), 400

    records = data["records"]
    for rec in records:
        if "person_name" not in rec or "timestamp" not in rec:
            return jsonify({"error": "Invalid record format"}), 400

    # 3. Forward to host — stamp device_id so host knows the source
    try:
        response = requests.post(
            HOST_URL,
            json={"records": records, "device_id": device_id},
            headers={
                "Content-Type": "application/json",
                "X-Sync-Token": HOST_TOKEN
            },
            timeout=10
        )
        return jsonify(response.json()), response.status_code

    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach host: {e}"}), 502


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    # ── HTTPS setup ───────────────────────────────────────────────────────────
    # For testing without a cert, comment out ssl_context and use plain HTTP.
    # For testing with HTTPS, generate a self-signed cert first:
    #
    #   openssl req -x509 -newkey rsa:4096 -nodes \
    #       -keyout gateway.key -out gateway.crt -days 365 \
    #       -subj "/CN=localhost" \
    #       -addext "subjectAltName=IP:127.0.0.1"
    #
    # Then set gateway_cert="gateway.crt" in AttendanceSyncer (network_sync.py)

    cert_file = "gateway.crt"
    key_file  = "gateway.key"

    if os.path.exists(cert_file) and os.path.exists(key_file):
        print("HTTPS enabled — using gateway.crt / gateway.key")
        ssl_context = (cert_file, key_file)
    else:
        print("WARNING: No cert found — running plain HTTP (testing only)")
        ssl_context = None

    app.run(
        host="127.0.0.1",
        port=5100,
        ssl_context=ssl_context,
        debug=False
    )