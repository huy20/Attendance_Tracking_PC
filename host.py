from flask import Flask, request, jsonify, render_template_string
import sqlite3
import os

app = Flask(__name__)
DB_PATH = './master_attendance.db'
HOST_TOKEN = "host_token_123"  # This should match the token used by the gateway when forwarding data
def setup_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Notice the new UNIQUE constraint at the bottom!
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS master_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT,
            timestamp TEXT,
            device_id TEXT,
            UNIQUE(person_name, timestamp)
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/', methods=['GET'])
def home():
    # Adding a clickable link on the home page to go to the dashboard
    return "<h1>Host Server is Running!</h1><a href='/logs'>Click here to view Attendance Logs</a>", 200

@app.route('/sync', methods=['POST'])
def sync_data():
    if request.headers.get("X-Sync-Token") != HOST_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    if not data or 'records' not in data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        new_records_count = 0
        
        for record in data['records']:
            # INSERT OR IGNORE tells SQLite to skip duplicates instantly
            cursor.execute('''
                INSERT OR IGNORE INTO master_logs (person_name, timestamp, device_id)
                VALUES (?, ?, ?)
            ''', (record['person_name'], record['timestamp'], "Front_Door_Cam_1"))
            
            # cursor.rowcount will be 1 if inserted, or 0 if it was ignored
            if cursor.rowcount > 0:
                new_records_count += 1
                
        conn.commit()
        conn.close()
        
        print(f"Received {len(data['records'])} records. Added {new_records_count} new entries.")
        return jsonify({'status': 'success', 'synced_count': new_records_count})
        
    except Exception as e:
        print(f"Database error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    
# --- NEW: The Dashboard Route ---
@app.route('/logs', methods=['GET'])
def view_logs():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Fetch all records, newest first
        cursor.execute("SELECT * FROM master_logs ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        conn.close()

        # Create a clean HTML table to display the data
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Master Attendance</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background-color: #f4f4f9;}
                h2 { color: #333; }
                table { border-collapse: collapse; width: 100%; background-color: white; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th { background-color: #007BFF; color: white; }
                tr:nth-child(even) { background-color: #f9f9f9; }
                tr:hover { background-color: #f1f1f1; }
                .stat-box { margin-bottom: 20px; padding: 10px; background-color: #e2e3e5; border-radius: 5px; display: inline-block;}
            </style>
        </head>
        <body>
            <h2>Master Attendance Dashboard</h2>
            <div class="stat-box">Total Records: <strong>{{ rows|length }}</strong></div>
            <table>
                <tr>
                    <th>ID</th>
                    <th>Person Name</th>
                    <th>Timestamp</th>
                    <th>Device ID</th>
                </tr>
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

if __name__ == '__main__':
    setup_db()
    app.run(host='127.0.0.1', port=5050)
    