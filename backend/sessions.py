"""Durable session snapshots and an ordered event log, independent of CLI state."""
import json
import sqlite3
import threading
from pathlib import Path


class SessionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_session ON events(session_id, seq);
        """)

    def save(self, session, event_type=None, data=None, turn_id=None):
        with self.lock, self.db:
            event = None
            if event_type:
                event = dict(type=event_type, session_id=session["id"], turn_id=turn_id, data=data or {})
                cursor = self.db.execute("INSERT INTO events(session_id,payload) VALUES (?,?)",
                                         (session["id"], json.dumps(event, ensure_ascii=False)))
                session["seq"] = cursor.lastrowid
                event["seq"] = cursor.lastrowid
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)",
                            (session["id"], json.dumps(session, ensure_ascii=False)))
            return event

    def get(self, session_id):
        with self.lock:
            row = self.db.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise KeyError(session_id)
            return json.loads(row[0])

    def list(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM sessions ORDER BY rowid DESC")]

    def events(self, session_id, after):
        with self.lock:
            rows = self.db.execute("SELECT seq,payload FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 200",
                                   (session_id, after)).fetchall()
            return [{**json.loads(payload), "seq": seq} for seq, payload in rows]

    def close(self):
        self.db.close()
