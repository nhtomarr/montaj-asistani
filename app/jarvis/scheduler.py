"""Avtomatik işlər: xatırlatmalar, rutinlər, poçt nəzarəti, səhər hesabatı."""
import logging
import os
import threading
import time
from datetime import datetime

import db
import notify
from jarvis import agent, google_api as g, store

log = logging.getLogger("jarvis.sched")

INBOX_EVERY = int(os.getenv("JARVIS_INBOX_MINUTES", "5")) * 60
REPORT_AT = os.getenv("JARVIS_REPORT_TIME", "09:00")

INBOX_PROMPT = """Poçt qutusuna yeni məktublar gəlib:
{items}

Hər birini çeşidlə: müştəri müraciəti / əməkdaşlıq-təklif / vacib / adi / reklam-spam.
- Reklam, bülleten, no-reply, avtomatik bildiriş: heç nə etmə (lazımsa archive).
- Real insanlardan gələnləri gmail_read ilə oxu. Rutin olanlara (salam, görüş vaxtı, sadə məlumat) cavab yaz.
  Görüş istəyənlər üçün calendar_list ilə boş vaxt tap və 2 variant təklif et (və ya razılaşdırılıbsa təqvimə yaz).
  Qiymət/müqavilə/pul məsələsi — risk="həssas" ilə cavab layihəsi hazırla (sahib təsdiqləyəcək).
- Ciddi potensial müştərini/təklifi memory_save(kind='kontakt') ilə qeyd et.
Sonda sahib üçün QISA hesabat yaz: nə gəldi, nə etdin, nə onun qərarını gözləyir. Diqqətə layiq heç nə yoxdursa yalnız "—" yaz."""

REPORT_PROMPT = """Səhər hesabatını hazırla (Telegram üçün, qısa və strukturlu):
1. Dünən nə etdim — activity_log(hours=24).
2. Bu günün görüşləri — calendar_list (bu gün 00:00–23:59).
3. Açıq və vaxtı keçən tapşırıqlar — task_list.
4. Cavabsız vacib məktublar — gmail_search('is:unread in:inbox newer_than:2d -category:promotions -category:social').
5. Təsdiq gözləyənlər — system_status.
6. Azərbaycan biznes/iqtisadiyyat üzrə 2–3 vacib xəbər və USD/AZN məzənnəsi — web_search."""


EVENING_AT = os.getenv("JARVIS_EVENING_TIME") or "20:30"
WEEKLY_AT = os.getenv("JARVIS_WEEKLY_TIME") or "19:00"  # bazar günü
MAC_BRIEF = os.getenv("JARVIS_MAC_BRIEF", "1") != "0"

REPORT_EXTRA = """
7. Yaddaşdakı açıq VƏDLƏR və gözlənilən cavablar — memory_search('vəd') və memory_search('gözləyir'): vaxtı
   çatan/keçənləri qeyd et (məs. "Anara cümə günü qiymət göndərəcəkdin").
8. Cavab gözləyən adamlar: gmail_search('in:inbox newer_than:7d older_than:2d -category:promotions -category:social
   -category:updates') — real insanlardan olub cavablanmayanları (gmail_search('in:sent to:<ünvan> newer_than:7d') ilə
   yoxla) "X 3 gündür cavab gözləyir" kimi yaz.
9. Nəzarətdəki sayt/serverlər — monitor_status (yalnız problem varsa yaz).
10. Bu gün üçün 3 prioritet təklif et.
Google qoşulmayıbsa, o bölmələri keç. Boş bölmələri yazma.
ƏN SONDA ayrıca sətirdə "SƏS:" ilə başlayan, Mac dinamikindən deyiləcək 3–4 cümləlik danışıq versiyası yaz
(səmimi, "Sabahınız xeyir, patron" ilə başla, yalnız ən vacibləri, rəqəmləri sözlə)."""

EVENING_PROMPT = """Axşam yekununu hazırla (Telegram, qısa):
1. Bu gün nə edildi — activity_log(hours=14).
2. Hansı tapşırıqlar bağlandı, hansılar açıq qaldı — task_list.
3. Sabahın görüşləri — calendar_list (sabah).
4. Bu günkü söhbətlərdən qalan vədlər/öhdəliklər — memory_search('vəd').
5. Sabah üçün 3 əsas iş.
Boş bölmələri yazma. Çox qısa ol."""

WEEKLY_PROMPT = """Həftəlik icmal hazırla (Telegram):
1. Bu həftə görülən əsas işlər — activity_log(hours=168).
2. Verilmiş vədlər: hansılar yerinə yetirilib, hansılar gecikir — memory_search('vəd'), task_list.
3. Reklam nəticələri (qoşulubsa) — son 7 gün, ümumi xərc və nəticə.
4. Sayt/server problemləri — monitor_status.
5. Gələn həftənin təqvimi — calendar_list (7 gün).
6. Diqqət tələb edən 3 məsələ və tövsiyə.
Qısa, strukturlu. Boş bölmələri yazma."""


def _once(key: str, at: str, weekday: int | None = None) -> bool:
    """Gündə (və ya həftədə) bir dəfə: vaxtı çatanda True; 3 saatdan çox gecikibsə (server söndürülmüşdü) ötür."""
    n = store.now()
    if weekday is not None and n.weekday() != weekday:
        return False
    day = n.strftime("%Y-%m-%d")
    if db.kv_get(key) == day:
        return False
    hh, mm = map(int, at.split(":"))
    mins = n.hour * 60 + n.minute - (hh * 60 + mm)
    if mins < 0:
        return False
    db.kv_set(key, day)
    return mins <= 180


def _mac_say(text: str) -> None:
    from jarvis import mac, tools
    if MAC_BRIEF and text and mac.online():
        try:
            tools._mac("say", text[:900])
        except Exception as e:  # noqa: BLE001
            log.warning("mac say: %s", e)


def _evening() -> None:
    if _once("jarvis_evening_day", EVENING_AT):
        _send("🌙 Axşam yekunu\n\n" + agent.ask_safe(EVENING_PROMPT, channel="auto", remember=False))


def _weekly() -> None:
    if _once("jarvis_weekly_day", WEEKLY_AT, weekday=6):
        _send("📊 Həftəlik icmal\n\n" + agent.ask_safe(WEEKLY_PROMPT, channel="auto", remember=False))


def _daily_report() -> None:
    if not _once("jarvis_report_day", REPORT_AT):
        return
    out = agent.ask_safe(REPORT_PROMPT + REPORT_EXTRA, channel="auto", remember=False)
    text, _, voice = out.partition("SƏS:")
    _send("☀️ Səhər brifinqi\n\n" + text.strip())
    _mac_say(voice.strip())


def _send(text: str) -> None:
    if text and text.strip() not in ("—", "-", ""):
        notify.sink.send_text(text)


def _check_inbox() -> None:
    if not g.configured():
        return
    seen = set(db.kv_get("jarvis_seen", []) or [])
    try:
        msgs = g.gmail_search("in:inbox is:unread newer_than:2d -category:promotions -category:social -category:updates", 15)
    except Exception as e:  # noqa: BLE001
        log.warning("inbox: %s", e)
        return
    new = [m for m in msgs if m["id"] not in seen]
    if not new:
        return
    items = "\n".join(f"- id={m['id']} | {m['from']} | {m['subject']} | {m['snippet'][:200]}" for m in new)
    store.log("poçt", f"{len(new)} yeni məktub yoxlanıldı")
    out = agent.ask_safe(INBOX_PROMPT.format(items=items), channel="auto", remember=False)
    seen.update(m["id"] for m in new)
    db.kv_set("jarvis_seen", list(seen)[-500:])
    _send("📬 " + out if out.strip() not in ("—", "-") else "")


def _reminders() -> None:
    for r in store.reminders_due():
        store.reminder_done(r["id"])
        _send(f"⏰ Xatırlatma: {r['text']}")


def _routines() -> None:
    for r in store.routine_list():
        try:
            due = store.next_due(r["schedule"], r["last_run"])
        except Exception:  # noqa: BLE001
            continue
        if time.time() >= due:
            store.routine_ran(r["id"])
            out = agent.ask_safe(r["prompt"], channel="auto", remember=False)
            store.log("rutin", f"İcra olundu: {r['name']}")
            _send(f"🔁 {r['name']}\n\n{out}")


def _monitor(deep: bool) -> None:
    from jarvis import monitor
    if not monitor.targets():
        return
    for msg in monitor.run_checks(deep):
        store.log("nəzarət", msg.splitlines()[0])
        _send(msg)


def _ads() -> None:
    from jarvis import ads
    if not any(ads.platforms().values()):
        return
    sent = set(db.kv_get("ads_alerts_sent", []) or [])
    cur = set()
    for msg in ads.check_alerts():
        key = msg.splitlines()[0]
        cur.add(key)
        if key not in sent:
            store.log("reklam", key)
            _send(msg)
    db.kv_set("ads_alerts_sent", list(cur))


def _disk() -> None:
    import shutil
    import config
    du = shutil.disk_usage(config.DATA_DIR)
    pct = du.used / du.total * 100
    level = 95 if pct >= 95 else 85 if pct >= 85 else 0
    if level and db.kv_get("disk_alert_level") != level:
        _send(f"💾 Server diski {pct:.0f}% doludur (boş: {du.free / 1e9:.1f} GB). Köhnə videoları silmək və ya diski "
              f"böyütmək lazımdır (Hetzner → Volumes və ya Rescale).")
    db.kv_set("disk_alert_level", level)


def loop() -> None:
    last_inbox = last_mon = last_deep = last_ads = 0.0
    while True:
        try:
            _reminders()
            _routines()
            _daily_report()
            _evening()
            _weekly()
            if time.time() - last_inbox > INBOX_EVERY:
                last_inbox = time.time()
                _check_inbox()
            if time.time() - last_mon > 300:
                last_mon = time.time()
                deep = time.time() - last_deep > 86400
                if deep:
                    last_deep = time.time()
                _monitor(deep)
            if time.time() - last_mon < 31:
                _disk()
            if time.time() - last_ads > 7200:
                last_ads = time.time()
                _ads()
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler: %s", e)
        time.sleep(30)


def start() -> None:
    threading.Thread(target=loop, daemon=True, name="jarvis-scheduler").start()


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, store.TZ).strftime("%d.%m %H:%M")
