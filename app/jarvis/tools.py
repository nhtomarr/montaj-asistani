"""Jarvis alətləri: təsvirlər (Claude üçün) və icra."""
import json
import re
from datetime import datetime, timedelta

import config
import db
import notify
from jarvis import google_api as g
from jarvis import store

# Təsdiqsiz göndərilməyəcək məzmun (pul, qiymət, müqavilə, öhdəlik)
SENSITIVE = re.compile(
    r"(\$|€|₼|\bazn\b|manat|dollar|qiym[əe]t|endirim|[öo]d[əe]ni[şs]|faktura|invoice|hesab-faktura|"
    r"m[üu]qavil[əe]|contract|price|discount|payment|imza|\bsign\b|razılaş|z[əe]man[əe]t|c[əe]rim[əe]|"
    r"\d{3,}\s*(azn|usd|eur|manat))", re.I)


def _s(name, desc, props, req=()):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": list(req)}}


STR = {"type": "string"}
INT = {"type": "integer"}

TOOLS = [
    # --- Gmail
    _s("gmail_search", "Gmail-də axtarış (Gmail sorğu sintaksisi: 'is:unread newer_than:1d', 'from:x@y.com', "
       "'subject:təklif'). Məktubların siyahısını qaytarır.",
       {"query": STR, "max_results": INT}, ["query"]),
    _s("gmail_read", "Məktubun tam mətnini oxu.", {"id": STR}, ["id"]),
    _s("gmail_send", "Məktub göndər və ya cavab yaz (reply_to_id ilə eyni zəncirdə). Pul/qiymət/müqavilə/öhdəlik "
       "olan məktublar avtomatik olaraq sahibin təsdiqinə göndərilir.",
       {"to": STR, "subject": STR, "body": STR, "reply_to_id": STR, "cc": STR,
        "risk": {"type": "string", "enum": ["rutin", "həssas"],
                 "description": "Öz qiymətləndirmən: həssas = pul, qiymət, müqavilə, öhdəlik, imtina, şikayət"}},
       ["to", "body", "risk"]),
    _s("gmail_mark", "Məktubu oxunmuş/arxiv/ulduzlu/vacib et.",
       {"id": STR, "action": {"type": "string", "enum": ["read", "unread", "archive", "star", "important"]}},
       ["id", "action"]),
    # --- Calendar
    _s("calendar_list", "Təqvimdəki tədbirləri göstər. Tarixlər ISO formatında (+04:00).",
       {"start": STR, "end": STR}, ["start", "end"]),
    _s("calendar_create", "Təqvimdə görüş yarat. attendees verilsə dəvət göndərilir. meet=true Google Meet linki.",
       {"title": STR, "start": STR, "end": STR, "attendees": {"type": "array", "items": STR},
        "description": STR, "location": STR, "meet": {"type": "boolean"}}, ["title", "start", "end"]),
    _s("calendar_delete", "Təqvimdən tədbiri sil (sahibin təsdiqi ilə).", {"id": STR, "title": STR}, ["id"]),
    # --- Drive / sənədlər
    _s("drive_search", "Google Drive-da fayl axtar.", {"query": STR}, ["query"]),
    _s("drive_read", "Drive faylını (Doc, Sheet, mətn) oxu.", {"id": STR}, ["id"]),
    _s("doc_create", "Google Docs sənədi yarat (təklif, müqavilə layihəsi, hesabat, plan). content — HTML.",
       {"title": STR, "content": STR}, ["title", "content"]),
    # --- Yaddaş
    _s("memory_save", "Vacib məlumatı yaddaşa/arxivə yaz: qərar, kontakt, araşdırma nəticəsi (mənbələrlə), qayda, "
       "sahib haqqında fakt.",
       {"title": STR, "content": STR,
        "kind": {"type": "string", "enum": ["qeyd", "araşdırma", "qərar", "kontakt", "sənəd", "profil"]},
        "tags": STR}, ["title", "content"]),
    _s("memory_search", "Yaddaşda/arxivdə axtar (əvvəlki qərarlar, araşdırmalar, kontaktlar).",
       {"query": STR}, ["query"]),
    _s("memory_get", "Yaddaş qeydinin tam mətnini al.", {"id": INT}, ["id"]),
    # --- İdarəetmə
    _s("task_add", "Tapşırıq əlavə et.", {"title": STR, "details": STR, "owner": STR,
       "due": {"type": "string", "description": "YYYY-MM-DD və ya YYYY-MM-DD HH:MM"},
       "priority": {"type": "string", "enum": ["yüksək", "orta", "aşağı"]}}, ["title"]),
    _s("task_list", "Tapşırıqları göstər.", {"status": {"type": "string", "enum": ["açıq", "bitib", "hamısı"]}}),
    _s("task_update", "Tapşırığı yenilə/bitir.", {"id": INT, "status": {"type": "string", "enum": ["açıq", "bitib"]},
       "details": STR, "due": STR, "owner": STR}, ["id"]),
    _s("reminder_add", "Xatırlatma qur — vaxtı gələndə Telegram-a mesaj gedir.",
       {"at": {"type": "string", "description": "ISO vaxt, məs. 2026-10-28T10:00:00+04:00"}, "text": STR},
       ["at", "text"]),
    _s("reminder_list", "Gələcək xatırlatmaları göstər.", {}),
    _s("reminder_delete", "Xatırlatmanı ləğv et.", {"id": INT}, ["id"]),
    _s("routine_add", "Təkrarlanan avtomatik iş qur (hesabat, monitorinq, xəbər xülasəsi və s.). Vaxtı gələndə "
       "Jarvis prompt-u icra edib nəticəni Telegram-a göndərir.",
       {"name": STR, "schedule": {"type": "string",
        "description": "'daily 09:00' | 'weekly mon 10:00' | 'monthly 1 09:00' | 'every 3h' | 'every 30m'"},
        "prompt": {"type": "string", "description": "Hər dəfə icra olunacaq tam tapşırıq"}},
       ["name", "schedule", "prompt"]),
    _s("routine_list", "Avtomatik rutinləri göstər.", {}),
    _s("routine_delete", "Rutini dayandır.", {"id": INT}, ["id"]),
    _s("activity_log", "Jarvis-in son N saatda gördüyü işlərin jurnalı (hesabat üçün).", {"hours": INT}),
    # --- Reklam kabinetləri
    _s("ads_accounts", "Reklam kabinetləri (Meta/Google Ads/TikTok): status, borc, balans, xərc, ödəniş linki.",
       {"platform": {"type": "string", "enum": ["meta", "google", "tiktok"]}}),
    _s("ads_report", "Kampaniya nəticələri: xərc, göstərilmə, klik, nəticə və nəticə qiyməti.",
       {"platform": {"type": "string", "enum": ["meta", "google", "tiktok"]}, "account_id": STR,
        "period": {"type": "string", "enum": ["today", "yesterday", "last_7d", "last_30d"]}},
       ["platform", "account_id"]),
    _s("ads_campaigns", "Kabinetdəki kampaniyaların siyahısı (id, status, büdcə).",
       {"platform": {"type": "string", "enum": ["meta", "google", "tiktok"]}, "account_id": STR},
       ["platform", "account_id"]),
    _s("ads_set_status", "Kampaniyanı dayandır (dərhal) və ya aktivləşdir (sahibin təsdiqi ilə).",
       {"platform": {"type": "string", "enum": ["meta", "google", "tiktok"]}, "account_id": STR, "campaign_id": STR,
        "campaign_name": STR, "status": {"type": "string", "enum": ["pause", "activate"]}},
       ["platform", "campaign_id", "status"]),
    _s("ads_set_budget", "Kampaniyanın gündəlik büdcəsini dəyiş (valyuta vahidi ilə, məs. 15 = 15 AZN/USD). Təsdiqlə.",
       {"platform": {"type": "string", "enum": ["meta", "google", "tiktok"]}, "account_id": STR, "campaign_id": STR,
        "campaign_name": STR, "daily_budget": {"type": "number"}}, ["platform", "campaign_id", "daily_budget"]),
    _s("ads_copy_campaign", "Meta kampaniyasını kopyala (dayandırılmış halda yaranır). Təsdiqlə.",
       {"account_id": STR, "campaign_id": STR, "campaign_name": STR}, ["campaign_id"]),
    # --- Nəzarət
    _s("monitor_status", "İzlənən saytların vəziyyəti (işləyir/çöküb, cavab vaxtı, SSL/domen günləri).", {}),
    _s("monitor_check", "Saytı indi yoxla (SSL və domen bitmə tarixi daxil).", {"url": STR}, ["url"]),
    _s("monitor_add", "Saytı daimi nəzarətə əlavə et (5 dəqiqədən bir).", {"url": STR}, ["url"]),
    _s("monitor_remove", "Saytı nəzarətdən çıxar.", {"url": STR}, ["url"]),
    # --- WhatsApp və müştərilər
    _s("whatsapp_conversations", "Son WhatsApp yazışmaları (müştərilər).", {}),
    _s("whatsapp_read", "Müştəri ilə yazışmanı oxu.", {"phone": STR}, ["phone"]),
    _s("whatsapp_send", "Müştəriyə WhatsApp mesajı göndər (yalnız sahib istəyəndə; son 24 saatda yazmış müştəriyə).",
       {"phone": STR, "text": STR}, ["phone", "text"]),
    _s("leads_list", "Toplanmış potensial müştərilər (WhatsApp və s.).", {}),
    _s("knowledge_add", "Şirkət bilik bazasına məlumat əlavə et (WhatsApp müştəri agenti bundan istifadə edir): qiymətlər, "
       "layihələr, şərtlər, FAQ.", {"title": STR, "content": STR}, ["title", "content"]),
    # --- Media / sayt
    _s("video_task", "Video montaj/Reels/YouTube/Instagram tapşırığı ver (son yüklənən videoya).",
       {"instruction": STR}, ["instruction"]),
    _s("site_task", "Sayt yarat, düzəlt, geri qaytar və ya siyahıla.",
       {"action": {"type": "string", "enum": ["create", "edit", "undo", "list"]}, "name": STR, "slug": STR,
        "request": STR}, ["action"]),
    _s("telegram_send", "Sahibə Telegram-da mesaj göndər (uzun nəticələr, linklər, siyahılar — xüsusən səsli söhbətdə).",
       {"text": STR}, ["text"]),
    _s("system_status", "Sistem vəziyyəti: qoşulmuş xidmətlər, son video işi, təsdiq gözləyənlər.", {}),
]


def _ok(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:9000]


def _approval(kind: str, payload: dict, summary: str) -> str:
    pid = store.pending_add(kind, payload, summary)
    notify.sink.confirm_action(pid, summary)
    store.log("təsdiq", f"Təsdiq istəndi: {summary[:150]}")
    return _ok({"status": "təsdiq_gözləyir", "pending_id": pid,
                "qeyd": "Sahibə Telegram-da 'Təsdiqlə' düyməsi göndərildi. Təsdiqlənəndə avtomatik icra olunacaq."})


def run(name: str, a: dict, ctx: dict) -> str:
    try:
        return _run(name, a, ctx)
    except Exception as e:  # noqa: BLE001
        return _ok({"xəta": str(e)[:500]})


def _run(name: str, a: dict, ctx: dict) -> str:
    if name == "gmail_search":
        return _ok(g.gmail_search(a["query"], a.get("max_results", 10)))
    if name == "gmail_read":
        return _ok(g.gmail_read(a["id"]))
    if name == "gmail_send":
        text = f"{a.get('subject', '')}\n{a['body']}"
        summary = f"📧 {a['to']}\nMövzu: {a.get('subject') or '(cavab)'}\n\n{a['body'][:1500]}"
        sensitive = a.get("risk") == "həssas" or SENSITIVE.search(text) or not config_auto_email()
        if sensitive:
            return _approval("gmail_send", a, summary)
        r = g.gmail_send(a["to"], a.get("subject", ""), a["body"], a.get("reply_to_id"), a.get("cc"))
        store.log("məktub", f"Göndərildi → {a['to']}: {a.get('subject') or 'cavab'}", {"body": a["body"][:500]})
        return _ok({"status": "göndərildi", **r})
    if name == "gmail_mark":
        g.gmail_modify(a["id"], a["action"])
        return _ok({"status": "ok"})
    if name == "calendar_list":
        return _ok(g.calendar_list(a["start"], a["end"]))
    if name == "calendar_create":
        r = g.calendar_create(a["title"], a["start"], a["end"], a.get("attendees"), a.get("description", ""),
                              a.get("location", ""), a.get("meet", False))
        store.log("görüş", f"Təqvimə əlavə: {a['title']} ({a['start'][:16].replace('T', ' ')})", r)
        return _ok({"status": "yaradıldı", **r})
    if name == "calendar_delete":
        return _approval("calendar_delete", a, f"🗓 Təqvimdən silinsin: {a.get('title') or a['id']}")
    if name == "drive_search":
        return _ok(g.drive_search(a["query"]))
    if name == "drive_read":
        return g.drive_read(a["id"])[:9000]
    if name == "doc_create":
        r = g.doc_create(a["title"], a["content"])
        store.log("sənəd", f"Sənəd yaradıldı: {a['title']}", r)
        return _ok(r)
    if name == "memory_save":
        mid = store.mem_save(a["title"], a["content"], a.get("kind", "qeyd"), a.get("tags", ""))
        store.log("yaddaş", f"Yadda saxlandı: {a['title']}")
        return _ok({"status": "saxlanıldı", "id": mid})
    if name == "memory_search":
        return _ok(store.mem_search(a["query"]))
    if name == "memory_get":
        return _ok(store.mem_get(a["id"]))
    if name == "task_add":
        tid = store.task_add(a["title"], a.get("details", ""), a.get("owner", ""), a.get("due", ""),
                             a.get("priority", "orta"))
        store.log("tapşırıq", f"Yeni tapşırıq: {a['title']}")
        return _ok({"status": "əlavə edildi", "id": tid})
    if name == "task_list":
        return _ok(store.task_list(a.get("status", "açıq")))
    if name == "task_update":
        store.task_update(a["id"], status=a.get("status"), details=a.get("details"), due=a.get("due"),
                          owner=a.get("owner"))
        if a.get("status") == "bitib":
            store.log("tapşırıq", f"Tapşırıq #{a['id']} bitdi")
        return _ok({"status": "yeniləndi"})
    if name == "reminder_add":
        at = datetime.fromisoformat(a["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=store.TZ)
        rid = store.reminder_add(at, a["text"])
        return _ok({"status": "quruldu", "id": rid, "vaxt": at.strftime("%Y-%m-%d %H:%M")})
    if name == "reminder_list":
        return _ok([{"id": r["id"], "text": r["text"],
                     "vaxt": datetime.fromtimestamp(r["at_ts"], store.TZ).strftime("%Y-%m-%d %H:%M")}
                    for r in store.reminders_upcoming()])
    if name == "reminder_delete":
        store.reminder_done(a["id"])
        return _ok({"status": "ləğv edildi"})
    if name == "routine_add":
        rid = store.routine_add(a["name"], a["schedule"], a["prompt"])
        store.log("rutin", f"Yeni rutin: {a['name']} ({a['schedule']})")
        return _ok({"status": "quruldu", "id": rid})
    if name == "routine_list":
        return _ok(store.routine_list())
    if name == "routine_delete":
        store.routine_delete(a["id"])
        return _ok({"status": "dayandırıldı"})
    if name == "activity_log":
        since = (store.now() - timedelta(hours=a.get("hours", 24))).timestamp()
        rows = store.activity(since)
        for r in rows:
            r["vaxt"] = datetime.fromtimestamp(r.pop("ts"), store.TZ).strftime("%d.%m %H:%M")
        return _ok(rows)
    if name == "ads_accounts":
        from jarvis import ads
        return _ok(ads.accounts(a.get("platform")))
    if name == "ads_report":
        from jarvis import ads
        return _ok(ads.report(a["platform"], a["account_id"], a.get("period", "last_7d")))
    if name == "ads_campaigns":
        from jarvis import ads
        return _ok(ads.campaigns(a["platform"], a["account_id"]))
    if name == "ads_set_status":
        from jarvis import ads
        if a["status"] == "pause":
            msg = ads.execute("ads_status", a)
            store.log("reklam", msg)
            return _ok({"status": "ok", "nəticə": msg})
        return _approval("ads_status", a, f"📣 {a['platform'].upper()} kampaniyası AKTİVLƏŞDİRİLSİN: "
                                          f"{a.get('campaign_name') or a['campaign_id']}")
    if name == "ads_set_budget":
        return _approval("ads_budget", a, f"💰 {a['platform'].upper()} «{a.get('campaign_name') or a['campaign_id']}» "
                                          f"gündəlik büdcə → {a['daily_budget']}")
    if name == "ads_copy_campaign":
        a["platform"] = "meta"
        return _approval("ads_copy", a, f"📑 Meta kampaniyası kopyalansın: {a.get('campaign_name') or a['campaign_id']}")
    if name == "monitor_status":
        from jarvis import monitor
        return _ok(monitor.status())
    if name == "monitor_check":
        from jarvis import monitor
        return _ok(monitor.check(a["url"] if a["url"].startswith("http") else "https://" + a["url"], deep=True))
    if name == "monitor_add":
        from jarvis import monitor
        store.log("nəzarət", f"Nəzarətə əlavə: {a['url']}")
        return _ok({"izlənir": monitor.add(a["url"])})
    if name == "monitor_remove":
        from jarvis import monitor
        return _ok({"izlənir": monitor.remove(a["url"])})
    if name == "whatsapp_conversations":
        from jarvis import whatsapp
        return _ok(whatsapp.conversations())
    if name == "whatsapp_read":
        from jarvis import whatsapp
        return _ok(whatsapp.history(a["phone"].lstrip("+"), 30))
    if name == "whatsapp_send":
        from jarvis import whatsapp
        r = whatsapp.send_text(a["phone"].lstrip("+"), a["text"])
        store.log("whatsapp", f"Göndərildi → {a['phone']}: {a['text'][:80]}")
        return _ok({"status": "göndərildi", "id": (r.get("messages") or [{}])[0].get("id")})
    if name == "leads_list":
        from jarvis import whatsapp
        return _ok(whatsapp.leads())
    if name == "knowledge_add":
        mid = store.mem_save(a["title"], a["content"], "biznes", "")
        store.log("yaddaş", f"Bilik bazasına: {a['title']}")
        return _ok({"status": "əlavə edildi", "id": mid})
    if name == "video_task":
        import bot
        import planner

        plan = planner.plan_from_text(a["instruction"], db.latest_video(),
                                      [p.stem for p in config.LOGOS_DIR.glob("*.png")])
        if plan.get("action") != "process":
            plan["action"] = "process"
        res = bot.create_job(plan, db.kv_get("owner_chat"), ctx.get("channel", "jarvis"))
        if res.get("ok"):
            store.log("video", f"Video tapşırığı #{res['job_id']}: {a['instruction'][:100]}")
        return _ok(res)
    if name == "site_task":
        import sites

        act = a["action"]
        if act == "list":
            return _ok(sites.list_sites())
        slugs = [s["slug"] for s in sites.list_sites()]
        slug = a.get("slug") if a.get("slug") in slugs else (slugs[-1] if slugs else None)
        if act == "create" or not slug:
            r = sites.create(a.get("name") or "sayt", a.get("request", ""))
        elif act == "edit":
            r = sites.edit(slug, a.get("request", ""))
        else:
            r = {"ok": sites.undo(slug), "url": sites.site_url(slug)}
        store.log("sayt", f"Sayt ({act}): {r.get('url')}")
        return _ok(r)
    if name == "telegram_send":
        notify.sink.send_text(a["text"])
        return _ok({"status": "göndərildi"})
    if name == "system_status":
        import os
        j = db.latest_job()
        return _ok({
            "google": g.configured(), "telegram_bot": db.kv_get("bot_username"),
            "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
            "son_video_isi": {"id": j["id"], "status": j["status"], "result": j["result"]} if j else None,
            "təsdiq_gözləyən": store.pending_open(), "rutinlər": len(store.routine_list()),
            "reklam": __import__("jarvis.ads", fromlist=["x"]).platforms(),
            "whatsapp": bool(os.getenv("WHATSAPP_TOKEN")),
        })
    return _ok({"xəta": f"naməlum alət {name}"})


def config_auto_email() -> bool:
    import os
    return os.getenv("JARVIS_AUTO_EMAIL", "true").lower() in ("1", "true", "yes")


def execute_pending(pid: int) -> str:
    p = store.pending_get(pid)
    if not p or p["status"] != "gözləyir":
        return "Bu əməliyyat artıq icra olunub və ya ləğv edilib."
    a = p["payload"]
    if p["kind"] == "gmail_send":
        g.gmail_send(a["to"], a.get("subject", ""), a["body"], a.get("reply_to_id"), a.get("cc"))
        store.log("məktub", f"Təsdiqlə göndərildi → {a['to']}: {a.get('subject') or 'cavab'}")
        msg = f"✅ Məktub göndərildi: {a['to']}"
    elif p["kind"] in ("ads_status", "ads_budget", "ads_copy"):
        from jarvis import ads
        msg = "✅ " + ads.execute(p["kind"], a)
        store.log("reklam", msg)
    elif p["kind"] == "calendar_delete":
        g.calendar_delete(a["id"])
        store.log("görüş", f"Təqvimdən silindi: {a.get('title') or a['id']}")
        msg = "✅ Tədbir silindi."
    else:
        msg = "Naməlum əməliyyat."
    store.pending_set(pid, "icra olundu")
    return msg
