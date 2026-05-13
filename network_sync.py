import os
import sqlite3
import json
import threading
import time
import requests

class AttendanceSyncer:
    def __init__(self, db_path, host_url, sync_interval=60.0):
        self.db_path = db_path
        self.host_url = host_url
        self.sync_interval = sync_interval
        
        self.syncing = False 
        self.running = False
        self.thread = None

    def start_syncing(self):
        """Starts the background loop"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.thread.start()
        print(f"Network Syncer started. Syncing to {self.host_url} every {self.sync_interval} seconds.")

    def stop_syncing(self):
        """Stops the background loop"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            print("Network Syncer stopped.")

    def _sync_loop(self):
        while self.running:
            self.sync_with_host()
            # Sleep in small chunks to allow quick shutdown
            for _ in range(int(self.sync_interval)):
                if not self.running:
                    break
                time.sleep(1)

    def sync_with_host(self):
        """Reads unsynced data and sends it to the host."""
        if not os.path.exists(self.db_path) or self.syncing or not self.host_url:
            return 
            
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Ensure the 'synced' column exists
            try:
                cursor.execute("ALTER TABLE attendance_logs ADD COLUMN synced INTEGER DEFAULT 0")
                conn.commit()
            except sqlite3.OperationalError:
                pass # Column already exists
            
            # Select ONLY records that haven't been synced yet
            cursor.execute("SELECT rowid, person_name, timestamp FROM attendance_logs WHERE synced = 0")
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return # Nothing new to sync

            self.syncing = True 
            pending_ids = [row[0] for row in rows]

            payload = {"records": [{"person_name": row[1], "timestamp": row[2]} for row in rows]}
            
            try:
                # Send to host
                response = requests.post(
                    self.host_url, 
                    json=payload, 
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )
                
                if response.status_code == 200:
                    self._on_success(pending_ids)
                else:
                    print(f"SYNC FAILED: Host returned status {response.status_code}")
                    self.syncing = False
            except requests.RequestException as e:
                print(f"SYNC FAILED: Could not reach host ({e}). Will try again later.")
                self.syncing = False
            
        except Exception as e:
            print(f"Sync Prep Error: {e}")
            self.syncing = False

    def _on_success(self, pending_ids):
        """Called when the host replies with success."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            placeholders = ','.join(['?'] * len(pending_ids))
            cursor.execute(f"UPDATE attendance_logs SET synced = 1 WHERE rowid IN ({placeholders})", pending_ids)
            
            conn.commit()
            conn.close()
            
            print(f"SYNC SUCCESS: Uploaded {len(pending_ids)} new records.")
            
        except Exception as e:
            print(f"Failed to update local sync status: {e}")
        finally:
            self.syncing = False
