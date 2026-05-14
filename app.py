"""
Flask Web Application for Face Recognition Attendance Tracking.
Replaces the Kivy Android app with a browser-based interface.

Uses:
- MediaPipe for face detection (replaces Android ML Kit)
- MobileFaceNet/ArcFace for face recognition
- SQLite for local storage
- Browser webcam via JavaScript getUserMedia
"""

import os
import cv2
import numpy as np
import base64
import sqlite3
import shutil
import glob
import time
from datetime import datetime

from flask import Flask, render_template, request, jsonify

from face_detection import FaceDetector
from arcface_recognizer import ArcFaceRecognizer

# ─── App Setup ────────────────────────────────────────────────

import sys

# PyInstaller path resolution
if getattr(sys, 'frozen', False):
    # Running as bundled executable
    BASE_DIR = sys._MEIPASS # Where PyInstaller unpacks templates/static
    EXE_DIR = os.path.dirname(sys.executable) # Where the actual .exe is located
    DATA_DIR = os.path.join(EXE_DIR, "data") # We want data saved next to the .exe
else:
    # Running as normal python script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")

app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

FACES_DIR = os.path.join(DATA_DIR, "registered_faces")
os.makedirs(FACES_DIR, exist_ok=True)

# ─── Initialize Engines ──────────────────────────────────────
detector = FaceDetector()
recognizer = ArcFaceRecognizer()
recognizer.load_database(DATA_DIR)

# ─── Server-side State ───────────────────────────────────────
# Registration sessions keyed by session_id
reg_sessions = {}

# Recognition temporal consensus state
recog_state = {
    "tracking_name": None,
    "consecutive_matches": 0,
    "required_matches": 3,
    "recently_logged": {},
    "log_cooldown": 10.0,
}


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def decode_base64_image(data_url):
    """Convert a base64 data-URL string to a BGR numpy array."""
    try:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        img_bytes = base64.b64decode(data_url)
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"Image decode error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register")
def register_page():
    return render_template("register.html")


@app.route("/recognize")
def recognize_page():
    return render_template("recognize.html")


@app.route("/users")
def users_page():
    users = []
    if os.path.exists(FACES_DIR):
        for d in sorted(os.listdir(FACES_DIR)):
            full = os.path.join(FACES_DIR, d)
            if os.path.isdir(full):
                count = len(glob.glob(os.path.join(full, "*.jpg")))
                users.append({
                    "folder": d,
                    "display": d.replace("_", " "),
                    "images": count,
                })
    return render_template("users.html", users=users)


@app.route("/attendance")
def attendance_page():
    return render_template("attendance.html")


# ═══════════════════════════════════════════════════════════════
#  API — REGISTRATION
# ═══════════════════════════════════════════════════════════════

@app.route("/api/register/start", methods=["POST"])
def api_register_start():
    data = request.get_json()
    name = data.get("name", "").strip().replace(" ", "_")
    if not name:
        return jsonify({"error": "Name is required"}), 400

    person_dir = os.path.join(FACES_DIR, name)
    os.makedirs(person_dir, exist_ok=True)

    sid = f"{name}_{int(time.time())}"
    reg_sessions[sid] = {
        "name": name,
        "dir": person_dir,
        "shots": 0,
        "max_shots": 5,
        "stability": 0,
        "stability_threshold": 12,
        "bursting": False,  # True once stability threshold reached
    }
    return jsonify({"session_id": sid, "name": name, "max_shots": 5})


@app.route("/api/register/frame", methods=["POST"])
def api_register_frame():
    data = request.get_json()
    sid = data.get("session_id")
    image = data.get("image")

    if not sid or sid not in reg_sessions:
        return jsonify({"error": "Invalid session"}), 400

    reg = reg_sessions[sid]

    if reg["shots"] >= reg["max_shots"]:
        return jsonify({
            "status": "COMPLETE",
            "shots": reg["shots"],
            "max_shots": reg["max_shots"],
            "progress": 1.0,
        })

    frame = decode_base64_image(image)
    if frame is None:
        return jsonify({"error": "Bad image"}), 400

    faces = detector.detect(frame)

    if not faces:
        reg["stability"] = 0
        reg["bursting"] = False  # Lost face — exit burst mode
        return jsonify({
            "status": "NO_FACE",
            "reasons": ["No face detected"],
            "shots": reg["shots"],
            "max_shots": reg["max_shots"],
            "progress": 0,
        })

    face = faces[0]
    passed, reasons = detector.quality_check(frame, face)

    if not passed:
        reg["stability"] = 0
        reg["bursting"] = False  # Quality failed — exit burst mode
        return jsonify({
            "status": "INVALID",
            "reasons": reasons,
            "shots": reg["shots"],
            "max_shots": reg["max_shots"],
            "progress": 0,
        })

    # ── Burst capture mode: already stabilized, capture every valid frame ──
    if reg["bursting"]:
        crop_bgr = detector.crop_face(frame, face)
        reg["shots"] += 1
        ts = int(time.time() * 1000)
        path = os.path.join(reg["dir"], f"face_{reg['shots']}_{ts}.jpg")
        cv2.imwrite(path, crop_bgr)

        status = "COMPLETE" if reg["shots"] >= reg["max_shots"] else "BURST_CAPTURE"
        progress = 1.0
    else:
        # ── Stabilizing phase: count consecutive good frames ──
        reg["stability"] += 1
        progress = min(reg["stability"] / reg["stability_threshold"], 1.0)

        if reg["stability"] >= reg["stability_threshold"]:
            # Threshold reached — enter burst mode and capture first shot
            reg["bursting"] = True
            crop_bgr = detector.crop_face(frame, face)
            reg["shots"] += 1
            ts = int(time.time() * 1000)
            path = os.path.join(reg["dir"], f"face_{reg['shots']}_{ts}.jpg")
            cv2.imwrite(path, crop_bgr)

            status = "COMPLETE" if reg["shots"] >= reg["max_shots"] else "BURST_CAPTURE"
        else:
            status = "STABILIZING"

    return jsonify({
        "status": status,
        "shots": reg["shots"],
        "max_shots": reg["max_shots"],
        "progress": progress,
    })


@app.route("/api/register/finish", methods=["POST"])
def api_register_finish():
    data = request.get_json()
    sid = data.get("session_id")

    if not sid or sid not in reg_sessions:
        return jsonify({"error": "Invalid session"}), 400

    reg = reg_sessions[sid]
    name = reg["name"]
    person_dir = reg["dir"]

    # ── Build embeddings and store in faces.db ──
    db_path = os.path.join(DATA_DIR, "faces.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT,
            embedding BLOB
        )
    """)
    conn.commit()

    images = glob.glob(os.path.join(person_dir, "*.jpg"))
    embeddings = []
    for img_path in images:
        face = cv2.imread(img_path)
        if face is not None:
            emb = recognizer.get_embedding(face)
            embeddings.append(emb)

    # Average in chunks of 5
    saved = 0
    chunk_size = 5
    for i in range(0, len(embeddings), chunk_size):
        chunk = embeddings[i : i + chunk_size]
        if chunk:
            avg = np.mean(np.array(chunk), axis=0).astype(np.float32)
            cursor.execute(
                "INSERT INTO user_embeddings (person_name, embedding) VALUES (?, ?)",
                (name, avg.tobytes()),
            )
            saved += 1

    conn.commit()
    conn.close()

    # Reload database into memory
    recognizer.load_database(DATA_DIR)

    # Cleanup session
    del reg_sessions[sid]

    return jsonify({"success": True, "name": name, "embeddings_saved": saved})


# ═══════════════════════════════════════════════════════════════
#  API — RECOGNITION
# ═══════════════════════════════════════════════════════════════

@app.route("/api/recognize/frame", methods=["POST"])
def api_recognize_frame():
    data = request.get_json()
    image = data.get("image")

    frame = decode_base64_image(image)
    if frame is None:
        return jsonify({"error": "Bad image"}), 400

    faces = detector.detect(frame)

    if not faces:
        recog_state["tracking_name"] = None
        recog_state["consecutive_matches"] = 0
        return jsonify({"status": "no_face", "name": None, "confidence": 0})

    face = faces[0]
    passed, reasons = detector.quality_check(frame, face)
    if not passed:
        recog_state["tracking_name"] = None
        recog_state["consecutive_matches"] = 0
        return jsonify({
            "status": "low_quality",
            "name": None,
            "confidence": 0,
            "reasons": reasons,
            "logged": False,
            "consecutive": 0,
            "required": recog_state["required_matches"],
        })
    crop_bgr = detector.crop_face(frame, face)
    crop_resized = cv2.resize(crop_bgr, (112, 112))

    name, confidence = recognizer.recognize(crop_resized)

    logged = False
    now = time.time()

    if name != "Unknown":
        if name == recog_state["tracking_name"]:
            recog_state["consecutive_matches"] += 1
        else:
            recog_state["tracking_name"] = name
            recog_state["consecutive_matches"] = 1

        if recog_state["consecutive_matches"] >= recog_state["required_matches"]:
            last = recog_state["recently_logged"].get(name, 0)
            if now - last > recog_state["log_cooldown"]:
                recognizer.log_attendance(name)
                recog_state["recently_logged"][name] = now
                logged = True
    else:
        recog_state["tracking_name"] = None
        recog_state["consecutive_matches"] = 0

    return jsonify({
        "status": "recognized" if name != "Unknown" else "unknown",
        "name": name,
        "confidence": round(confidence, 4),
        "logged": logged,
        "consecutive": recog_state["consecutive_matches"],
        "required": recog_state["required_matches"],
    })


# ═══════════════════════════════════════════════════════════════
#  API — USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@app.route("/api/users/delete/<name>", methods=["POST"])
def api_delete_user(name):
    # Remove from database
    db_path = os.path.join(DATA_DIR, "faces.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_embeddings WHERE person_name = ?", (name,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"Deleted {deleted} embedding(s) for {name}")

    # Remove folder
    user_dir = os.path.join(FACES_DIR, name)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
        print(f"Deleted folder: {name}")

    # Reload
    recognizer.load_database(DATA_DIR)
    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════
#  API — ATTENDANCE
# ═══════════════════════════════════════════════════════════════

@app.route("/api/attendance/today")
def api_attendance_today():
    db_path = os.path.join(DATA_DIR, "attendance.db")
    if not os.path.exists(db_path):
        return jsonify({"records": [], "count": 0})

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT person_name, timestamp FROM attendance_logs "
        "WHERE timestamp LIKE ? ORDER BY timestamp DESC",
        (f"{today}%",),
    )
    rows = cursor.fetchall()
    conn.close()

    records = [{"name": r[0], "timestamp": r[1]} for r in rows]
    return jsonify({"records": records, "count": len(records)})


@app.route("/api/sync", methods=["POST"])
def api_receive_sync():
    """Receive attendance data from external devices (e.g. Android app)."""
    data = request.get_json()
    records = data.get("records", [])

    db_path = os.path.join(DATA_DIR, "attendance.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT,
            timestamp TEXT,
            synced INTEGER DEFAULT 0
        )
    """)

    inserted = 0
    for rec in records:
        cursor.execute(
            "INSERT INTO attendance_logs (person_name, timestamp, synced) VALUES (?, ?, 1)",
            (rec.get("person_name"), rec.get("timestamp")),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return jsonify({"success": True, "inserted": inserted})


from network_sync import AttendanceSyncer

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print("  Face Recognition Attendance System")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    
    # Setup network syncer
    # Change host_url to the IP of your central server receiving the syncs
    # e.g., "http://192.168.1.100:5000/api/sync"
    syncer = AttendanceSyncer(
        db_path=os.path.join(DATA_DIR, "attendance.db"),
        host_url="https://127.0.0.1:5100/sync",  # https
        gateway_cert="gateway.crt"                # pin the cert
    )
    syncer.start_syncing()
    
    try:
        # Use debug=False to prevent Flask's reloader from starting the syncer twice
        app.run(host="127.0.0.1", port=5000, debug=False)
    finally:
        syncer.stop_syncing()       
