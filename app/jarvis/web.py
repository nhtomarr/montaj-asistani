"""Jarvis paneli və API: /jarvis, /api/jarvis/*, telefon agenti üçün /api/agent/jarvis."""
import asyncio
import base64
import os
import shutil
from pathlib import Path
import hmac
import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

import config
import stt
from jarvis import agent, google_api as g, store, tools as jtools

router = APIRouter()


def _check(t):
    if not config.PANEL_TOKEN or not t or not hmac.compare_digest(t, config.PANEL_TOKEN):
        raise HTTPException(403, "icazə yoxdur")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-9:]


# ---------- telefon (ElevenLabs server tool)
@router.post("/api/agent/jarvis")
async def phone(req: Request):
    secret = req.headers.get("x-agent-secret") or ""
    if not config.AGENT_TOOL_SECRET or not hmac.compare_digest(secret, config.AGENT_TOOL_SECRET):
        raise HTTPException(403, "icazə yoxdur")
    body = await req.json()
    caller = body.get("caller_id") or ""
    if config.ALLOWED_CALLER_IDS and _digits(caller) not in {_digits(x) for x in config.ALLOWED_CALLER_IDS}:
        return {"message": "Bağışlayın, bu nömrədən tapşırıq qəbul edə bilmərəm."}
    msg = (body.get("message") or body.get("instruction") or "").strip()
    if not msg:
        return {"message": "Eşitmədim, bir daha deyin."}
    reply = await asyncio.to_thread(agent.ask_safe, msg, "phone")
    return {"message": reply}


# ---------- panel API
@router.get("/api/jarvis/overview")
def overview(t: str | None = None):
    _check(t)
    n = store.now()
    day0 = n.replace(hour=0, minute=0, second=0, microsecond=0)
    out = {"now": n.strftime("%d.%m.%Y %H:%M"), "google": g.configured(), "events": [], "unread": None,
           "tasks": store.task_list("açıq")[:12], "pending": store.pending_open(),
           "routines": store.routine_list(), "reminders": [], "activity": []}
    for r in store.reminders_upcoming()[:6]:
        out["reminders"].append({"text": r["text"],
                                 "at": datetime.fromtimestamp(r["at_ts"], store.TZ).strftime("%d.%m %H:%M")})
    for a in store.activity(day0.timestamp() - 86400, 40):
        out["activity"].append({"t": datetime.fromtimestamp(a["ts"], store.TZ).strftime("%d.%m %H:%M"),
                                "kind": a["kind"], "summary": a["summary"]})
    out["today_actions"] = len(store.activity(day0.timestamp(), 500))
    out["sys"], out["modules"] = _sysinfo(), _modules()
    if g.configured():
        try:
            out["events"] = g.calendar_list(day0.isoformat(), (day0 + timedelta(days=2)).isoformat())
        except Exception as e:  # noqa: BLE001
            out["google_error"] = str(e)[:200]
        try:
            out["unread"] = len(g.gmail_search("is:unread in:inbox -category:promotions -category:social", 25))
        except Exception:  # noqa: BLE001
            pass
    return out


@router.get("/api/jarvis/live")
def live(t: str | None = None):
    _check(t)
    return {"events": agent.live_recent(25)}


@router.post("/api/jarvis/chat")
async def chat(req: Request, t: str | None = None):
    _check(t)
    body = await req.json()
    reply = await asyncio.to_thread(agent.ask_safe, body.get("message", ""), "panel")
    audio = None
    if body.get("speak"):
        b = await asyncio.to_thread(agent.tts, _speakable(reply))
        audio = base64.b64encode(b).decode() if b else None
    return {"reply": reply, "audio": audio}


@router.post("/api/jarvis/voice")
async def voice(t: str | None = None, file: UploadFile = File(...)):
    _check(t)
    p = config.WORK_DIR / f"pv_{uuid.uuid4().hex}.webm"
    p.write_bytes(await file.read())
    try:
        text = await asyncio.to_thread(stt.transcribe_short, p)
    finally:
        p.unlink(missing_ok=True)
    if not text:
        return {"heard": "", "reply": "Eşitmədim, bir daha deyin.", "audio": None}
    reply = await asyncio.to_thread(agent.ask_safe, text, "panel")
    b = await asyncio.to_thread(agent.tts, _speakable(reply))
    return {"heard": text, "reply": reply, "audio": base64.b64encode(b).decode() if b else None}


@router.post("/api/jarvis/approve")
async def approve(req: Request, t: str | None = None):
    _check(t)
    body = await req.json()
    pid = int(body["id"])
    if not body.get("ok"):
        store.pending_set(pid, "ləğv")
        return {"message": "Ləğv edildi."}
    return {"message": await asyncio.to_thread(jtools.execute_pending, pid)}


def _speakable(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|]", "", text)
    return text[:900]


@router.get("/jarvis", response_class=HTMLResponse)
def page(t: str | None = None):
    _check(t)
    return (Path(__file__).parent / "jarvis.html").read_text(encoding="utf-8").replace("__TOKEN__", t)


def _sysinfo() -> dict:
    out = {}
    try:
        out["load"] = round(os.getloadavg()[0], 2)
        out["cpus"] = os.cpu_count()
        mi = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        tot = int(mi["MemTotal"].split()[0]) / 1048576
        av = int(mi["MemAvailable"].split()[0]) / 1048576
        out["mem_total"], out["mem_used"] = round(tot, 1), round(tot - av, 1)
        up = float(Path("/proc/uptime").read_text().split()[0])
        out["uptime"] = f"{int(up // 86400)}g {int(up % 86400 // 3600)}s {int(up % 3600 // 60)}d"
        du = shutil.disk_usage(config.DATA_DIR)
        out["disk_total"], out["disk_used"] = round(du.total / 1e9), round(du.used / 1e9, 1)
    except Exception:  # noqa: BLE001
        pass
    out["memories"] = (store.q("SELECT count(*) AS n FROM mem", one=True) or {}).get("n", 0)
    return out


def _modules() -> dict:
    import db
    return {"CLAUDE": bool(config.ANTHROPIC_API_KEY), "TELEGRAM": bool(db.kv_get("bot_username")),
            "ELEVENLABS": bool(config.ELEVENLABS_API_KEY), "GMAIL": g.configured(), "CALENDAR": g.configured(),
            "DRIVE": g.configured(), "YOUTUBE": bool(os.getenv("YOUTUBE_REFRESH_TOKEN")),
            "INSTAGRAM": bool(config.INSTAGRAM_ACCESS_TOKEN)}


