from flask import Flask, request, jsonify, render_template_string, send_file
import sqlite3
import json
import os
import shutil
from datetime import datetime

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
LOGS_DB_PATH  = './master_attendance.db'
FACES_DB_PATH = './master_faces.db'
HOST_TOKEN    = os.environ.get("HOST_TOKEN", "host_token_123")

USE_HTTPS = False   # ← flip to True with cert


# ══════════════════════════════════════════════════════════════════════════════
#  DB SETUP
# ══════════════════════════════════════════════════════════════════════════════

def setup_logs_db():
    """Create master attendance database."""
    with sqlite3.connect(LOGS_DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS master_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name TEXT,
                timestamp   TEXT,
                device_id   TEXT,
                UNIQUE(person_name, timestamp)
            )
        ''')
        conn.commit()


def setup_faces_db():
    """Create master faces database with version tracking."""
    with sqlite3.connect(FACES_DB_PATH) as conn:

        # Master face embeddings from all devices
        conn.execute('''
            CREATE TABLE IF NOT EXISTS master_faces (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name   TEXT,
                embedding     BLOB,
                device_id     TEXT,
                registered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_active     INTEGER DEFAULT 1
            )
        ''')

        # Single-row version tracker — id=1 enforced by CHECK constraint
        conn.execute('''
            CREATE TABLE IF NOT EXISTS faces_version (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                version    INTEGER DEFAULT 0,
                updated_at TEXT    DEFAULT CURRENT_TIMESTAMP,
                changed_by TEXT
            )
        ''')

        # Ensure the singleton version row exists
        conn.execute('''
            INSERT OR IGNORE INTO faces_version (id, version, changed_by)
            VALUES (1, 0, 'system')
        ''')

        conn.commit()


# ── Version helpers ───────────────────────────────────────────────────────────

def get_version():
    """Return current (version, updated_at) from faces_version."""
    with sqlite3.connect(FACES_DB_PATH) as conn:
        row = conn.execute(
            "SELECT version, updated_at FROM faces_version WHERE id = 1"
        ).fetchone()
    return row  # (version_int, timestamp_str)


def increment_version(device_id: str):
    """
    Increment the global faces version inside an open connection.
    Call this within the same transaction as the data change so they
    are atomic — both commit or both roll back together.
    """
    with sqlite3.connect(FACES_DB_PATH) as conn:
        conn.execute('''
            UPDATE faces_version
            SET version    = version + 1,
                updated_at = CURRENT_TIMESTAMP,
                changed_by = ?
            WHERE id = 1
        ''', (device_id,))
        conn.commit()


# ── Auth helper ───────────────────────────────────────────────────────────────

def check_token(req):
    return req.headers.get("X-Sync-Token") == HOST_TOKEN


# ══════════════════════════════════════════════════════════════════════════════
#  ATTENDANCE ROUTES  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/', methods=['GET'])
def home():
    return (
        "<h1>Host Server is Running!</h1>"
        "<a href='/logs'>Attendance Logs</a>"
        "<br><a href='/faces'>Face Embeddings Database</a>"
    ), 200


@app.route('/sync', methods=['POST'])
def sync_data():
    if not check_token(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or 'records' not in data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    device_id = data.get("device_id") or request.headers.get("X-Device-ID", "unknown")

    try:
        with sqlite3.connect(LOGS_DB_PATH) as conn:
            new_count = 0
            for record in data['records']:
                conn.execute('''
                    INSERT OR IGNORE INTO master_logs (person_name, timestamp, device_id)
                    VALUES (?, ?, ?)
                ''', (record['person_name'], record['timestamp'], device_id))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    new_count += 1
            conn.commit()

        print(f"[ATTENDANCE] {device_id}: {len(data['records'])} received, {new_count} new.")
        return jsonify({'status': 'success', 'synced_count': new_count})

    except Exception as e:
        print(f"[ATTENDANCE] DB error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/logs', methods=['GET'])
def view_logs():
    try:
        with sqlite3.connect(LOGS_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT * FROM master_logs ORDER BY timestamp DESC"
            ).fetchall()

        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Master Attendance</title>
            <style>
                body  { font-family: Arial, sans-serif; margin: 40px; background-color: #f4f4f9; }
                h2    { color: #333; }
                table { border-collapse: collapse; width: 100%; background-color: white;
                        box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th    { background-color: #007BFF; color: white; }
                tr:nth-child(even) { background-color: #f9f9f9; }
                tr:hover           { background-color: #f1f1f1; }
                .stat-box { margin-bottom: 20px; padding: 10px;
                            background-color: #e2e3e5; border-radius: 5px;
                            display: inline-block; }
            </style>
        </head>
        <body>
            <h2>Master Attendance Dashboard</h2>
            <div class="stat-box">Total Records: <strong>{{ rows|length }}</strong></div>
            <table>
                <tr><th>ID</th><th>Person Name</th><th>Timestamp</th><th>Device ID</th></tr>
                {% for row in rows %}
                <tr>
                    <td>{{ row[0] }}</td>
                    <td><b>{{ row[1] }}</b></td>
                    <td>{{ row[2] }}</td>
                    <td>{{ row[3] }}</td>
                </tr>
                {% endfor %}
            </table>
        </body>
        </html>
        """
        return render_template_string(html_template, rows=rows)
    except Exception as e:
        return f"Error loading database: {e}"

@app.route('/faces', methods=['GET'])
def view_faces():
    try:
        with sqlite3.connect(FACES_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT id, person_name, device_id, registered_at, is_active FROM master_faces ORDER BY registered_at DESC"
            ).fetchall()
            version, updated_at = conn.execute(
                "SELECT version, updated_at FROM faces_version WHERE id = 1"
            ).fetchone()

        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Master Faces</title>
            <style>
                body  { font-family: Arial, sans-serif; margin: 40px; background-color: #f4f4f9; }
                h2    { color: #333; }
                table { border-collapse: collapse; width: 100%; background-color: white;
                        box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th    { background-color: #28a745; color: white; }
                tr:nth-child(even) { background-color: #f9f9f9; }
                tr:hover           { background-color: #f1f1f1; }
                .stat-box { margin-bottom: 20px; padding: 10px; margin-right: 10px;
                            background-color: #e2e3e5; border-radius: 5px;
                            display: inline-block; }
                .inactive { color: #999; font-style: italic; }
                .active   { color: #28a745; font-weight: bold; }
            </style>
        </head>
        <body>
            <h2>Master Faces Dashboard</h2>
            <div class="stat-box">Total Embeddings: <strong>{{ rows|length }}</strong></div>
            <div class="stat-box">DB Version: <strong>{{ version }}</strong></div>
            <div class="stat-box">Last Updated: <strong>{{ updated_at }}</strong></div>
            <table>
                <tr>
                    <th>ID</th>
                    <th>Person Name</th>
                    <th>Device ID</th>
                    <th>Registered At</th>
                    <th>Status</th>
                </tr>
                {% for row in rows %}
                <tr>
                    <td>{{ row[0] }}</td>
                    <td><b>{{ row[1] }}</b></td>
                    <td>{{ row[2] }}</td>
                    <td>{{ row[3] }}</td>
                    <td>
                        {% if row[4] == 1 %}
                            <span class="active">Active</span>
                        {% else %}
                            <span class="inactive">Inactive</span>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>
        </body>
        </html>
        """
        return render_template_string(html_template, rows=rows, version=version, updated_at=updated_at)
    except Exception as e:
        return f"Error loading faces database: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  FACES SYNC ROUTES
# ══════════════════════════════════════════════════════════════════════════════

# ── GET /faces/version ────────────────────────────────────────────────────────
@app.route('/faces/version', methods=['GET'])
def faces_version():
    """
    Lightweight version check — edge devices poll this every 30 minutes.
    Returns current version number and timestamp.
    No file transfer involved.
    """
    if not check_token(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        version, updated_at = get_version()
        return jsonify({
            "version":    version,
            "updated_at": updated_at,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /faces/download ───────────────────────────────────────────────────────
@app.route('/faces/download', methods=['GET'])
def faces_download():
    """
    Returns all active embeddings as JSON.
    Edge device calls this when its local version is older than host version.

    Returns embeddings as base64-encoded strings since BLOB can't be JSON-serialized.
    Edge device decodes base64 → bytes → numpy float32 array.
    """
    if not check_token(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        with sqlite3.connect(FACES_DB_PATH) as conn:
            rows = conn.execute('''
                SELECT person_name, embedding, device_id, registered_at
                FROM master_faces
                WHERE is_active = 1
                ORDER BY registered_at ASC
            ''').fetchall()

            version, updated_at = get_version()

        import base64
        embeddings = [
            {
                "person_name":   row[0],
                "embedding":     base64.b64encode(row[1]).decode(),  # BLOB → base64 string
                "device_id":     row[2],
                "registered_at": row[3],
            }
            for row in rows
        ]

        print(f"[FACES] Download: {len(embeddings)} embeddings at version {version}.")
        return jsonify({
            "version":    version,
            "updated_at": updated_at,
            "count":      len(embeddings),
            "embeddings": embeddings,
        })

    except Exception as e:
        print(f"[FACES] Download error: {e}")
        return jsonify({"error": str(e)}), 500


# ── POST /faces/upload ────────────────────────────────────────────────────────
@app.route('/faces/upload', methods=['POST'])
def faces_upload():
    """
    Edge device pushes newly registered embeddings to host.
    Host merges into master_faces and increments version.
    All other devices will pick up the change on their next poll.

    Expected payload:
    {
        "device_id": "cam_lobby",
        "embeddings": [
            {
                "person_name": "John_Smith",
                "embedding":   "<base64 encoded float32 bytes>"
            },
            ...
        ]
    }
    """
    if not check_token(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or "embeddings" not in data:
        return jsonify({"error": "Bad request — missing embeddings"}), 400

    device_id  = data.get("device_id", "unknown")
    embeddings = data["embeddings"]

    if not embeddings:
        return jsonify({"error": "Empty embeddings list"}), 400

    import base64

    try:
        with sqlite3.connect(FACES_DB_PATH) as conn:
            inserted = 0
            for emb in embeddings:
                person_name = emb.get("person_name")
                embedding   = emb.get("embedding")

                if not person_name or not embedding:
                    continue

                # Decode base64 → raw bytes
                embedding_bytes = base64.b64decode(embedding)

                conn.execute('''
                    INSERT INTO master_faces
                        (person_name, embedding, device_id, registered_at, is_active)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
                ''', (person_name, embedding_bytes, device_id))

                inserted += 1

            # Increment version in the same transaction
            conn.execute('''
                UPDATE faces_version
                SET version    = version + 1,
                    updated_at = CURRENT_TIMESTAMP,
                    changed_by = ?
                WHERE id = 1
            ''', (device_id,))

            conn.commit()

        version, updated_at = get_version()
        print(f"[FACES] Upload: {device_id} pushed {inserted} embeddings. Version now {version}.")

        return jsonify({
            "status":   "success",
            "inserted": inserted,
            "version":  version,
        })

    except Exception as e:
        print(f"[FACES] Upload error: {e}")
        return jsonify({"error": str(e)}), 500


# ── POST /faces/delete ────────────────────────────────────────────────────────
@app.route('/faces/delete', methods=['POST'])
def faces_delete():
    """
    Soft-delete a person from master_faces.
    Sets is_active=0 — record is kept for audit purposes.
    Increments version so all edge devices sync the deletion.

    Expected payload:
    {
        "device_id":   "cam_lobby",
        "person_name": "John_Smith"
    }
    """
    if not check_token(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data or "person_name" not in data:
        return jsonify({"error": "Bad request — missing person_name"}), 400

    person_name = data["person_name"]
    device_id   = data.get("device_id", "unknown")

    try:
        with sqlite3.connect(FACES_DB_PATH) as conn:

            # Soft delete — preserve history, just mark inactive
            conn.execute('''
                UPDATE master_faces
                SET is_active = 0
                WHERE person_name = ?
            ''', (person_name,))

            deactivated = conn.execute("SELECT changes()").fetchone()[0]

            if deactivated == 0:
                return jsonify({"error": f"Person '{person_name}' not found"}), 404

            # Increment version in same transaction
            conn.execute('''
                UPDATE faces_version
                SET version    = version + 1,
                    updated_at = CURRENT_TIMESTAMP,
                    changed_by = ?
                WHERE id = 1
            ''', (device_id,))

            conn.commit()

        version, _ = get_version()
        print(f"[FACES] Delete: '{person_name}' deactivated by {device_id}. Version now {version}.")

        return jsonify({
            "status":      "success",
            "person_name": person_name,
            "deactivated": deactivated,
            "version":     version,
        })

    except Exception as e:
        print(f"[FACES] Delete error: {e}")
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    setup_logs_db()
    setup_faces_db()

    if USE_HTTPS:
        cert_file = "gateway.crt"
        key_file  = "gateway.key"
        if os.path.exists(cert_file) and os.path.exists(key_file):
            print("HTTPS enabled.")
            ssl_context = (cert_file, key_file)
        else:
            print("ERROR: USE_HTTPS=True but cert files not found.")
            exit(1)
    else:
        print("WARNING: Running plain HTTP — testing only.")
        ssl_context = None

    app.run(host='127.0.0.1', port=5050, ssl_context=ssl_context)