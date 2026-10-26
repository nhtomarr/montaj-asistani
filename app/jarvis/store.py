"""Jarvis yaddaşı: biliklər (tam mətn axtarışı), tapşırıqlar, xatırlatmalar, rutinlər, jurnal, təsdiqlər, söhbət."""
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db

TZ = ZoneInfo("Asia/Baku")

with db._lock:
    db._conn.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS mem USING fts5(kind, title, content, tags, created UNINDEXED);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, details TEXT,
            owner TEXT, due TEXT, priority TEXT, status TEXT, created_at REAL, updated_at REAL);
        CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, at_ts REAL, text TEXT, sent INT);
        CREATE TABLE IF NOT EXISTS routines(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, schedule TEXT,
            prompt TEXT, last_run REAL, active INT);
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
            summary TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, payload TEXT,
            summary TEXT, status TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, role TEXT,
            content TEXT, ts REAL);
        """
    )
    db._conn.commit()

q = db._q


def now() -> datetime:
    return datetime.now(TZ)


# ---- bilik / yaddaş
def mem_save(title: str, content: str, kind: str = "qeyd", tags: str = "") -> int:
    return q("INSERT INTO mem(kind,title,content,tags,created) VALUES(?,?,?,?,?)",
             (kind, title, content, tags, now().strftime("%Y-%m-%d %H:%M")))


def mem_search(query: str, limit: int = 8) -> list[dict]:
    terms = " OR ".join(f'"{t}"' for t in query.replace('"', " ").split() if len(t) > 1) or '""'
    try:
        rows = q("SELECT rowid AS id, kind, title, snippet(mem, 2, '[', ']', '…', 40) AS snip, tags, created "
                 "FROM mem WHERE mem MATCH ? ORDER BY rank LIMIT ?", (terms, limit))
    except Exception:  # noqa: BLE001
        rows = q("SELECT rowid AS id, kind, title, substr(content,1,300) AS snip, tags, created FROM mem "
                 "WHERE title LIKE ? OR content LIKE ? ORDER BY rowid DESC LIMIT ?",
                 (f"%{query}%", f"%{query}%", limit))
    return rows


def mem_get(mid: int) -> dict | None:
    return q("SELECT rowid AS id, kind, title, content, tags, created FROM mem WHERE rowid=?", (mid,), one=True)


def mem_recent(limit: int = 10) -> list[dict]:
    return q("SELECT rowid AS id, kind, title, created FROM mem ORDER BY rowid DESC LIMIT ?", (limit,))


# ---- tapşırıqlar
def task_add(title, details="", owner="", due="", priority="orta") -> int:
    t = time.time()
    return q("INSERT INTO tasks(title,details,owner,due,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
             (title, details, owner, due, priority, "açıq", t, t))


def task_list(status: str = "açıq") -> list[dict]:
    if status == "hamısı":
        return q("SELECT * FROM tasks ORDER BY id DESC LIMIT 50")
    return q("SELECT * FROM tasks WHERE status=? ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, id LIMIT 50",
             (status,))


def task_update(tid: int, **f) -> None:
    f = {k: v for k, v in f.items() if v is not None}
    if not f:
        return
    f["updated_at"] = time.time()
    q(f"UPDATE tasks SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), tid))


# ---- xatırlatmalar
def reminder_add(at: datetime, text: str) -> int:
    return q("INSERT INTO reminders(at_ts,text,sent) VALUES(?,?,0)", (at.timestamp(), text))


def reminders_due() -> list[dict]:
    return q("SELECT * FROM reminders WHERE sent=0 AND at_ts<=?", (time.time(),))


def reminder_done(rid: int) -> None:
    q("UPDATE reminders SET sent=1 WHERE id=?", (rid,))


def reminders_upcoming() -> list[dict]:
    return q("SELECT * FROM reminders WHERE sent=0 ORDER BY at_ts LIMIT 20")


# ---- rutinlər (avtomatlaşdırma)
DAYS = {"b.e": 0, "be": 0, "mon": 0, "ç.a": 1, "ca": 1, "tue": 1, "ç": 2, "wed": 2, "c.a": 3, "thu": 3,
        "c": 4, "cume": 4, "fri": 4, "ş": 5, "sat": 5, "b": 6, "bazar": 6, "sun": 6}


def routine_add(name: str, schedule: str, prompt: str) -> int:
    next_due(schedule, 0)  # yoxlama — səhv formatda xəta atır
    return q("INSERT INTO routines(name,schedule,prompt,last_run,active) VALUES(?,?,?,?,1)",
             (name, schedule, prompt, time.time()))


def routine_list() -> list[dict]:
    return q("SELECT * FROM routines WHERE active=1 ORDER BY id")


def routine_delete(rid: int) -> None:
    q("UPDATE routines SET active=0 WHERE id=?", (rid,))


def routine_ran(rid: int) -> None:
    q("UPDATE routines SET last_run=? WHERE id=?", (time.time(), rid))


def next_due(schedule: str, last_run: float) -> float:
    """Format: 'daily 09:00' | 'weekly mon 09:00' | 'monthly 1 09:00' | 'every 3h' | 'every 30m'."""
    s = schedule.strip().lower().split()
    last = datetime.fromtimestamp(last_run or time.time(), TZ)
    if s[0] == "every":
        n, unit = int(s[1][:-1]), s[1][-1]
        return last_run + n * (3600 if unit == "h" else 60)
    hh, mm = map(int, s[-1].split(":"))
    cand = last.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if s[0] == "daily":
        if cand <= last:
            cand += timedelta(days=1)
    elif s[0] == "weekly":
        wd = DAYS[s[1]]
        cand += timedelta(days=(wd - cand.weekday()) % 7)
        if cand <= last:
            cand += timedelta(days=7)
    elif s[0] == "monthly":
        day = int(s[1])
        cand = cand.replace(day=min(day, 28))
        if cand <= last:
            m = cand.month % 12 + 1
            cand = cand.replace(year=cand.year + (cand.month == 12), month=m)
    else:
        raise ValueError("Cədvəl formatı: 'daily 09:00', 'weekly mon 09:00', 'monthly 1 09:00', 'every 3h'")
    return cand.timestamp()


# ---- jurnal
def log(kind: str, summary: str, data: dict | None = None) -> None:
    q("INSERT INTO activity(ts,kind,summary,data) VALUES(?,?,?,?)",
      (time.time(), kind, summary[:500], json.dumps(data or {}, ensure_ascii=False)[:4000]))


def activity(since_ts: float, limit: int = 100) -> list[dict]:
    return q("SELECT ts,kind,summary FROM activity WHERE ts>=? ORDER BY id DESC LIMIT ?", (since_ts, limit))


# ---- təsdiq gözləyən əməliyyatlar
def pending_add(kind: str, payload: dict, summary: str) -> int:
    return q("INSERT INTO pending(kind,payload,summary,status,created_at) VALUES(?,?,?,?,?)",
             (kind, json.dumps(payload, ensure_ascii=False), summary, "gözləyir", time.time()))


def pending_get(pid: int) -> dict | None:
    r = q("SELECT * FROM pending WHERE id=?", (pid,), one=True)
    if r:
        r["payload"] = json.loads(r["payload"])
    return r


def pending_open() -> list[dict]:
    return q("SELECT id,kind,summary,created_at FROM pending WHERE status='gözləyir' ORDER BY id DESC LIMIT 20")


def pending_set(pid: int, status: str) -> None:
    q("UPDATE pending SET status=? WHERE id=?", (status, pid))


# ---- söhbət tarixçəsi
def chat_add(channel: str, role: str, content: str) -> None:
    q("INSERT INTO chat(channel,role,content,ts) VALUES(?,?,?,?)", (channel, role, content[:6000], time.time()))


def chat_history(channel: str, n: int = 16, max_age_h: float = 12) -> list[dict]:
    rows = q("SELECT role,content FROM chat WHERE channel=? AND ts>=? ORDER BY id DESC LIMIT ?",
             (channel, time.time() - max_age_h * 3600, n))
    rows.reverse()
    while rows and rows[0]["role"] != "user":
        rows.pop(0)
    return rows
