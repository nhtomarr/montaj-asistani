"""WhatsApp (Cloud API): müştəri yazışmalarına avtomatik cavab, lead toplama, sahibə xəbər.

Müştəri agenti Jarvis-in sahib alətlərinə (poçt, təqvim, reklam) GİRİŞİ YOXDUR — yalnız bilik bazası,
lead qeydi və sahibə xəbər.
"""
import hashlib
import hmac
import json
import logging
import os
import threading
import time

import httpx

import config
import db
import notify
from jarvis import store

log = logging.getLogger("whatsapp")
V = os.getenv("META_GRAPH_VERSION", "v23.0")

with db._lock:
    db._conn.executescript("""
    CREATE TABLE IF NOT EXISTS wa_msgs(id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, dir TEXT,
        text TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, interest TEXT,
        budget TEXT, notes TEXT, source TEXT, ts REAL);
    """)
    db._conn.commit()


def configured() -> bool:
    return bool(os.getenv("WHATSAPP_TOKEN") and os.getenv("WHATSAPP_PHONE_ID"))


def auto_reply() -> bool:
    return os.getenv("WHATSAPP_AUTO_REPLY", "true").lower() in ("1", "true", "yes")


def verify_signature(body: bytes, header: str) -> bool:
    secret = os.getenv("WHATSAPP_APP_SECRET", "")
    if not secret:
        return True
    exp = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, header or "")


def send_text(to: str, text: str) -> dict:
    r = httpx.post(f"https://graph.facebook.com/{V}/{os.getenv('WHATSAPP_PHONE_ID')}/messages",
                   headers={"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}"},
                   json={"messaging_product": "whatsapp", "to": to, "type": "text",
                         "text": {"preview_url": True, "body": text[:4000]}}, timeout=30)
    j = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"WhatsApp: {j.get('error', {}).get('message', r.text[:200])}")
    db._q("INSERT INTO wa_msgs(phone,name,dir,text,ts) VALUES(?,?,?,?,?)", (to, "", "out", text, time.time()))
    return j


def _download_media(media_id: str) -> bytes:
    h = {"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}"}
    meta = httpx.get(f"https://graph.facebook.com/{V}/{media_id}", headers=h, timeout=30).json()
    return httpx.get(meta["url"], headers=h, timeout=60).content


def conversations(limit: int = 15) -> list[dict]:
    return db._q("SELECT phone, max(name) AS name, count(*) AS n, max(ts) AS last FROM wa_msgs GROUP BY phone "
                 "ORDER BY last DESC LIMIT ?", (limit,))


def history(phone: str, n: int = 20) -> list[dict]:
    rows = db._q("SELECT dir, text, ts FROM wa_msgs WHERE phone=? ORDER BY id DESC LIMIT ?", (phone, n))
    rows.reverse()
    return rows


def leads(limit: int = 30) -> list[dict]:
    return db._q("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,))


# ---------- webhook emalı
def handle_webhook(payload: dict) -> None:
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name", "") for c in v.get("contacts", [])}
            for m in v.get("messages", []):
                threading.Thread(target=_on_message, args=(m, names.get(m.get("from"), "")), daemon=True).start()


def _on_message(m: dict, name: str) -> None:
    phone = m.get("from")
    typ = m.get("type")
    text = ""
    try:
        if typ == "text":
            text = m["text"]["body"]
        elif typ in ("audio", "voice"):
            import stt
            p = config.WORK_DIR / f"wa_{m['id']}.ogg"
            p.write_bytes(_download_media(m[typ]["id"]))
            text = "[səsli mesaj] " + stt.transcribe_short(p)
            p.unlink(missing_ok=True)
        elif typ == "interactive":
            it = m["interactive"]
            text = (it.get("button_reply") or it.get("list_reply") or {}).get("title", "")
        else:
            text = f"[{typ} göndərdi]" + (f" {m.get(typ, {}).get('caption', '')}" if isinstance(m.get(typ), dict) else "")
    except Exception as e:  # noqa: BLE001
        text = f"[{typ}: oxunmadı — {e}]"
    db._q("INSERT INTO wa_msgs(phone,name,dir,text,ts) VALUES(?,?,?,?,?)", (phone, name, "in", text, time.time()))
    store.log("whatsapp", f"Mesaj: {name or phone}: {text[:120]}")
    if not auto_reply():
        notify.sink.send_text(f"💬 WhatsApp — {name or phone}:\n{text[:1500]}")
        return
    try:
        reply = customer_agent(phone, name)
        if reply:
            send_text(phone, reply)
    except Exception as e:  # noqa: BLE001
        log.exception("wa")
        notify.sink.send_text(f"❌ WhatsApp cavabı alınmadı ({name or phone}): {e}\nMesaj: {text[:500]}")


# ---------- müştəri agenti
CUSTOMER_SYSTEM = """Sən {biz} şirkətinin WhatsApp-dakı virtual satış məsləhətçisisən. Müştəri ilə onun yazdığı dildə
(Azərbaycan/rus/türk/ingilis), nəzakətli, qısa və insan kimi danış (1–4 cümlə). Emoji minimum.

Qaydalar:
- Məlumatı YALNIZ knowledge_search nəticələrindən ver (qiymət, şərtlər, ünvan, iş saatları, layihələr). Bilik bazasında
  yoxdursa, uydurma — "dəqiqləşdirib menecerimiz sizinlə əlaqə saxlayacaq" de və owner_notify çağır.
- Endirim, xüsusi qiymət, müqavilə şərti, zəmanət VƏ ya şikayət — öhdəlik götürmə, owner_notify(urgent=true) çağır.
- Ciddi maraq göstərən müştəridən təbii şəkildə adını, nə axtardığını (məs. otaq sayı, büdcə), əlaqə vaxtını öyrən və
  lead_save ilə qeyd et.
- Görüş/baxış istəyirsə: uyğun gün-saat soruş, lead_save + owner_notify ilə ötür.
- Heç vaxt süni intellekt olduğunu gizlətmə, soruşsalar "virtual köməkçi" olduğunu de.
İndi: {now}."""

CUSTOMER_TOOLS = [
    {"name": "knowledge_search", "description": "Şirkətin bilik bazasında axtarış (qiymətlər, layihələr, şərtlər, FAQ).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "lead_save", "description": "Potensial müştərini qeyd et.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "interest": {"type": "string"},
                      "budget": {"type": "string"}, "notes": {"type": "string"}}, "required": ["interest"]}},
    {"name": "owner_notify", "description": "Menecerə/sahibə Telegram-da xəbər ver (müştəri adı, nə istəyir).",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}, "urgent": {"type": "boolean"}},
                      "required": ["summary"]}},
]


def _ctool(name: str, a: dict, phone: str, cname: str) -> str:
    if name == "knowledge_search":
        rows = db._q("SELECT title, content FROM mem WHERE kind IN ('biznes','faq','sənəd') AND mem MATCH ? "
                     "ORDER BY rank LIMIT 5", (" OR ".join(f'"{w}"' for w in a["query"].split() if len(w) > 2) or '""',))
        return json.dumps([{"title": r["title"], "content": r["content"][:1500]} for r in rows], ensure_ascii=False) \
            or "[]"
    if name == "lead_save":
        db._q("INSERT INTO leads(phone,name,interest,budget,notes,source,ts) VALUES(?,?,?,?,?,?,?)",
              (phone, a.get("name") or cname, a.get("interest", ""), a.get("budget", ""), a.get("notes", ""),
               "whatsapp", time.time()))
        store.log("lead", f"Yeni lead: {a.get('name') or cname or phone} — {a.get('interest', '')}")
        return '{"status":"saxlanıldı"}'
    if name == "owner_notify":
        mark = "🔴 TƏCİLİ " if a.get("urgent") else ""
        notify.sink.send_text(f"{mark}💬 WhatsApp müştərisi {cname or ''} (+{phone}):\n{a['summary']}\n\nYazışma: wa.me/{phone}")
        return '{"status":"göndərildi"}'
    return '{"xəta":"naməlum alət"}'


def customer_agent(phone: str, cname: str) -> str:
    import httpx as hx
    biz = os.getenv("WHATSAPP_BUSINESS", "şirkətimiz")
    hist = history(phone, 16)
    msgs = []
    for h in hist:
        role = "user" if h["dir"] == "in" else "assistant"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + h["text"]
        else:
            msgs.append({"role": role, "content": h["text"]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        return ""
    system = CUSTOMER_SYSTEM.format(biz=biz, now=store.now().strftime("%Y-%m-%d %H:%M"))
    final = ""
    for _ in range(6):
        r = hx.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": config.ANTHROPIC_MODEL, "max_tokens": 1000, "system": system,
                          "tools": CUSTOMER_TOOLS, "messages": msgs}, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(r.text[:300])
        resp = r.json()
        content = resp["content"]
        msgs.append({"role": "assistant", "content": content})
        final = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip() or final
        uses = [b for b in content if b.get("type") == "tool_use"]
        if resp.get("stop_reason") != "tool_use" or not uses:
            break
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u["id"], "content": _ctool(u["name"], u["input"], phone, cname)}
            for u in uses]})
    return final
