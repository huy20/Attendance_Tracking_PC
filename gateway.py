from flask import Flask, request, jsonify
import requests
import json
import time
import base64
import threading
import logging
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

app = Flask(__name__)

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s %(message)s'
)

# ── Config ────────────────────────────────────────────────────────────────────
HOST_URL   = os.environ.get("HOST_URL",   "http://127.0.0.1:5050/sync")
HOST_TOKEN = os.environ.get("HOST_TOKEN", "host_token_123")  # Must match the token expected by the host server
USE_HTTPS  = True   # ← flip to True after generating gateway.crt / gateway.key

if not HOST_TOKEN:
    raise RuntimeError("HOST_TOKEN environment variable not set.")

# ── Public Key Registry ───────────────────────────────────────────────────────
# Each device's public key stored as:
#   gateway/public_keys/<device_id>.pem
#
# To add a new device:
#   1. Run generate_device_keys.py on the edge device
#   2. Copy the generated public_key.pem here as <device_id>.pem
#   3. Restart the gateway
#
# To revoke a device:
#   1. Delete its .pem file from public_keys/
#   2. Restart the gateway — that device is immediately blocked
PUBLIC_KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_keys")
os.makedirs(PUBLIC_KEYS_DIR, exist_ok=True)

MAX_AGE = 30  # seconds — reject requests older than this

# ── Public Key Loader ─────────────────────────────────────────────────────────
def load_public_keys() -> dict:
    """
    Load all .pem files from public_keys/ into memory.
    Returns {device_id: public_key_object}
    Called at startup and on reload.
    """
    keys = {}
    for filename in os.listdir(PUBLIC_KEYS_DIR):
        if not filename.endswith(".pem"):
            continue
        device_id = filename[:-4]  # strip .pem
        filepath  = os.path.join(PUBLIC_KEYS_DIR, filename)
        try:
            with open(filepath, "rb") as f:
                keys[device_id] = serialization.load_pem_public_key(f.read())
            print(f"Loaded public key for device: '{device_id}'")
        except Exception as e:
            logging.error(f"Failed to load public key '{filename}': {e}")
    return keys

_public_keys = load_public_keys()

if not _public_keys:
    print("WARNING: No public keys loaded. No devices can authenticate.")


# ── Replay Window ─────────────────────────────────────────────────────────────
_replay_window = {}
_replay_lock   = threading.Lock()

def clean_replay_window():
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


# ── RSA Signature Verification ────────────────────────────────────────────────
def verify_request(req, data) -> tuple:
    """
    Verify the RSA signature on an incoming sync request.
    Returns (device_id, None) on success or (None, error_string) on failure.

    Checks in order:
      1. Required headers present
      2. Timestamp freshness (anti-replay)
      3. Signature not reused (anti-replay)
      4. Device has a registered public key
      5. RSA-PSS signature valid against public key
    """
    device_id = req.headers.get("X-Device-ID")
    timestamp = req.headers.get("X-Timestamp")
    signature = req.headers.get("X-Signature")

    # 1. All headers must be present
    if not all([device_id, timestamp, signature]):
        return None, "Missing authentication headers"

    # 2. Timestamp freshness
    try:
        age = abs(int(time.time()) - int(timestamp))
    except ValueError:
        return None, "Invalid timestamp"

    if age > MAX_AGE:
        return None, f"Request expired ({age}s old, max {MAX_AGE}s)"

    # 3. Replay check — same signature can't be used twice
    sig_key = f"{device_id}.{signature[:16]}"  # truncated key to save memory
    with _replay_lock:
        if sig_key in _replay_window:
            logging.warning(f"Replay attack from device '{device_id}'")
            return None, "Duplicate request"
        _replay_window[sig_key] = time.time() + MAX_AGE

    # 4. Device must have a registered public key
    public_key = _public_keys.get(device_id)
    if not public_key:
        logging.warning(f"No public key registered for device '{device_id}'")
        return None, "Unknown device"

    # 5. Verify RSA-PSS signature
    try:
        sig_bytes = base64.b64decode(signature)
        body      = json.dumps(data, separators=(',', ':'), sort_keys=True)
        message   = f"{device_id}.{timestamp}.{body}".encode()

        public_key.verify(
            sig_bytes,
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        # verify() raises InvalidSignature if it fails — no return value needed
        return device_id, None

    except InvalidSignature:
        logging.warning(f"Invalid RSA signature from device '{device_id}'")
        return None, "Invalid signature"
    except Exception as e:
        logging.error(f"Signature verification error: {e}")
        return None, "Verification error"


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/sync", methods=["POST"])
def sync():
    # 1. Parse body first — needed for signature verification
    data = request.get_json()
    if not data:
        return jsonify({"error": "Bad request — empty body"}), 400

    # 2. Verify RSA signature
    device_id, error = verify_request(request, data)
    if error:
        return jsonify({"error": error}), 401

    # 3. Validate payload structure
    if "records" not in data:
        return jsonify({"error": "Missing records"}), 400

    records = data["records"]
    for rec in records:
        if "person_name" not in rec or "timestamp" not in rec:
            return jsonify({"error": "Invalid record format"}), 400

    # 4. Forward to host — include verified device_id
    try:
        response = requests.post(
            HOST_URL,
            json={"records": records, "device_id": device_id},
            headers={
                "Content-Type": "application/json",
                "X-Sync-Token": HOST_TOKEN,
            },
            timeout=10
        )
        return jsonify(response.json()), response.status_code

    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach host: {e}"}), 502


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":          "ok",
        "registered_devices": list(_public_keys.keys()),
    }), 200


if __name__ == "__main__":
    if USE_HTTPS:
        cert_file = "gateway.crt"
        key_file  = "gateway.key"
        if os.path.exists(cert_file) and os.path.exists(key_file):
            print("HTTPS enabled.")
            ssl_context = (cert_file, key_file)
        else:
            print("ERROR: USE_HTTPS=True but cert files not found. Run generate_cert.bat.")
            exit(1)
    else:
        print("WARNING: Running plain HTTP — for testing only.")
        ssl_context = None

    app.run(
        host="127.0.0.1",
        port=5100,
        ssl_context=ssl_context,
        debug=False
    )