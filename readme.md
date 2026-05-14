# Attendance Tracking System — Quick Start

## Architecture
```
[app.py :5000]  →  [gateway.py :5100]  →  [host.py :5050]
 Face recognition    RSA auth + forward    Master attendance DB
```

---

## First Time Setup

### 1. Install dependencies
```bash
pip install flask flask-limiter requests cryptography waitress
```

### 2. Generate device keys
```bash
python generate_device_keys.py --device-id device_1_id
```
Output:
```
keys/device_1_id/private_key.pem   ← stays here, never copy
keys/device_1_id/public_key.pem    ← copy to gateway
```

### 3. Register device on gateway
```bash
mkdir public_keys
cp keys/device_1_id/public_key.pem public_keys/device_1_id.pem
```

### 4. Set environment variable
```bash
# Windows CMD
set HOST_TOKEN=host_token_123

# Git Bash / Linux
export HOST_TOKEN=host_token_123
```

---

## Running (3 terminals)

### Terminal 1 — Host
```bash
python host.py
# Runs on http://127.0.0.1:5050
# Dashboard: http://127.0.0.1:5050/logs
```

### Terminal 2 — Gateway
```bash
python gateway.py
# Runs on http://127.0.0.1:5100
```

### Terminal 3 — App (face recognition)
```bash
python app.py
# Runs on http://127.0.0.1:5000
# Open this in browser for the UI
```

---

## Testing the Pipeline

### Check gateway is alive
```bash
curl http://127.0.0.1:5100/health
# {"registered_devices": ["device_1_id"], "status": "ok"}
```

### Send a test sync manually
```bash
curl -X POST http://127.0.0.1:5100/sync \
     -H "Content-Type: application/json" \
     -d "{\"records\": [{\"person_name\": \"Test_User\", \"timestamp\": \"2024-01-01 09:00:00\"}]}"
# Expected: {"error": "Missing authentication headers"} ← correct, no RSA sig
```

### Check host dashboard
```
http://127.0.0.1:5050/logs
```

---

## Current Security Settings

| Setting | Current | Production |
|---|---|---|
| Transport | HTTP | HTTPS (flip USE_HTTPS=True) |
| Auth | RSA-PSS signature | same |
| Device secret | hardcoded (testing) | keyring |
| HOST_TOKEN | env variable | env variable |

### To enable HTTPS when ready
```bash
# 1. Generate cert (run once)
openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout gateway.key -out gateway.crt -days 365 \
    -subj "/CN=localhost" \
    -addext "subjectAltName=IP:127.0.0.1"

# 2. Flip flag in network_sync.py and gateway.py
USE_HTTPS = True

# 3. Update app.py host_url
host_url = "https://127.0.0.1:5100/sync"
```

---

## Adding a New Device

```bash
# 1. Generate keys on the new device
python generate_device_keys.py --device-id cam_lobby

# 2. Copy public key to gateway
cp keys/cam_lobby/public_key.pem public_keys/cam_lobby.pem

# 3. Restart gateway
```

## Revoking a Device

```bash
# Delete its public key and restart gateway
rm public_keys/cam_lobby.pem
# restart gateway.py — device is immediately blocked
```

---

## File Structure
```
project/
├── app.py                  # Face recognition UI + Flask server
├── gateway.py              # API gateway (RSA verification)
├── host.py                 # Master attendance server + dashboard
├── network_sync.py         # Background sync client
├── arcface_recognizer.py   # ArcFace embedding + recognition
├── face_detection.py       # YuNet face detector
├── generate_device_keys.py # Run once per device to generate RSA keys
├── generate_cert.bat       # Run once to generate HTTPS cert
├── gateway.crt             # HTTPS cert (generated, not committed)
├── gateway.key             # HTTPS private key (generated, never share)
├── public_keys/            # One .pem per registered device
│   └── device_1_id.pem
├── keys/                   # Device private keys (never commit)
│   └── device_1_id/
│       ├── private_key.pem
│       └── public_key.pem
└── data/                   # Runtime data (never commit)
    ├── faces.db
    ├── attendance.db
    └── registered_faces/
```

---

## .gitignore — make sure these are excluded
```
.env
*.key
*.db
data/
keys/
public_keys/
gateway.crt
```