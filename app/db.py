"""Sadə SQLite saxlama: videolar, tapşırıqlar, açar-dəyər."""
import json
import sqlite3
import threading
import time
from typing import Any

from config import DB_PATH

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS videos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT, name TEXT, duration REAL, width INT, height INT,
        source TEXT, created_at REAL
    );
    CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INT, plan TEXT, status TEXT, stage TEXT,
        result TEXT, error TEXT, chat_id INT, msg_id INT, origin TEXT,
        created_at REAL, updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
    """
)
_conn.commit()


def _q(sql: str, args: tuple = (), one: bool = False):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        if sql.lstrip().upper().startswith("INSERT"):
            return cur.lastrowid
        rows = cur.fetchall()
    rows = [dict(r) for r in rows]
    return (rows[0] if rows else None) if one else rows


# ---- kv
def kv_get(key: str, default: Any = None) -> Any:
    r = _q("SELECT value FROM kv WHERE key=?", (key,), one=True)
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value: Any) -> None:
    _q("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value)))


# ---- videos
def add_video(path: str, name: str, duration: float, width: int, height: int, source: str) -> int:
    return _q(
        "INSERT INTO videos(path,name,duration,width,height,source,created_at) VALUES(?,?,?,?,?,?,?)",
        (path, name, duration, width, height, source, time.time()),
    )


def get_video(vid: int):
    return _q("SELECT * FROM videos WHERE id=?", (vid,), one=True)


def latest_video():
    return _q("SELECT * FROM videos ORDER BY id DESC LIMIT 1", one=True)


def list_videos(n: int = 10):
    return _q("SELECT * FROM videos ORDER BY id DESC LIMIT ?", (n,))


# ---- jobs
def add_job(video_id: int | None, plan: dict, chat_id: int | None, origin: str) -> int:
    now = time.time()
    return _q(
        "INSERT INTO jobs(video_id,plan,status,stage,result,error,chat_id,msg_id,origin,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (video_id, json.dumps(plan, ensure_ascii=False), "queued", "", "{}", "", chat_id, None, origin, now, now),
    )


def _job(r):
    if r:
        r["plan"] = json.loads(r["plan"] or "{}")
        r["result"] = json.loads(r["result"] or "{}")
    return r


def get_job(jid: int):
    return _job(_q("SELECT * FROM jobs WHERE id=?", (jid,), one=True))


def next_queued_job():
    return _job(_q("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1", one=True))


def latest_job():
    return _job(_q("SELECT * FROM jobs ORDER BY id DESC LIMIT 1", one=True))


def list_jobs(n: int = 20):
    return [_job(r) for r in _q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (n,))]


def update_job(jid: int, **fields) -> None:
    if "plan" in fields:
        fields["plan"] = json.dumps(fields["plan"], ensure_ascii=False)
    if "result" in fields:
        fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
    fields["updated_at"] = time.time()
    cols = ",".join(f"{k}=?" for k in fields)
    _q(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))


def reset_running_jobs() -> None:
    """Server yenidən başlayanda yarımçıq qalan işləri növbəyə qaytarır."""
    _q("UPDATE jobs SET status='queued' WHERE status='running'")
