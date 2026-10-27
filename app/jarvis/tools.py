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
    _s("monitor_add", "Saytı və ya serveri daimi nəzarətə əlavə et (5 dəqiqədən bir). Sayt: domen/URL; server/port: tcp://IP:port (məs. RDP tcp://1.2.3.4:3389).", {"url": STR}, ["url"]),
    _s("monitor_remove", "Saytı nəzarətdən çıxar.", {"url": STR}, ["url"]),
    # --- WhatsApp və müştərilər
    _s("whatsapp_personal", "Sahibin ÖZ WhatsApp-ından (Mac-dəki WhatsApp Desktop vasitəsilə) istənilən adama mesaj "
       "göndər. to: kontaktın adı (Mac Kontaktlarında axtarılır, məs. 'Anar') və ya nömrə. Bir neçə uyğun kontakt "
       "çıxsa, siyahını sahibə de və hansı olduğunu soruş. Göndərməzdən əvvəl kimə və nə yazılacağını bir cümlə ilə "
       "təsdiqlət (sahib mətni özü dəqiq deyibsə, birbaşa göndər).",
       {"to": STR, "text": STR}, ["to", "text"]),
    _s("whatsapp_conversations", "Son WhatsApp yazışmaları (müştərilər).", {}),
    _s("whatsapp_read", "Müştəri ilə yazışmanı oxu.", {"phone": STR}, ["phone"]),
    _s("whatsapp_send", "Müştəriyə WhatsApp mesajı göndər (yalnız sahib istəyəndə; son 24 saatda yazmış müştəriyə).",
       {"phone": STR, "text": STR}, ["phone", "text"]),
    _s("leads_list", "Toplanmış potensial müştərilər (WhatsApp və s.).", {}),
    _s("knowledge_add", "Şirkət bilik bazasına məlumat əlavə et (WhatsApp müştəri agenti bundan istifadə edir): qiymətlər, "
       "layihələr, şərtlər, FAQ.", {"title": STR, "content": STR}, ["title", "content"]),
    # --- Media / sayt
    _s("video_task", "YENİ video montaj/Reels/YouTube/Instagram tapşırığı ver (son yüklənən videoya). Yalnız sahib "
       "açıq şəkildə montaj/emal istəyəndə; vəziyyət və ya link soruşanda video_jobs işlət.",
       {"instruction": STR}, ["instruction"]),
    _s("video_jobs", "Video işlərinin siyahısı və CANLI vəziyyəti (hansı mərhələdə, neçə faiz, neçə dəqiqədir gedir, qalan "
       "mərhələlər), hazır montaj və Reels linkləri. İstifadəçi videolar haqqında SORUŞANDA bunu işlət — yeni video_task YARATMA.", {}),
    _s("site_task", "Sayt yarat, düzəlt, geri qaytar və ya siyahıla.",
       {"action": {"type": "string", "enum": ["create", "edit", "undo", "list"]}, "name": STR, "slug": STR,
        "request": STR}, ["action"]),
    _s("telegram_send", "Sahibə Telegram-da mesaj göndər (uzun nəticələr, linklər, siyahılar — xüsusən səsli söhbətdə).",
       {"text": STR}, ["text"]),
    _s("mac_task", "Mac-də ÇOXADDIMLI tapşırıq (ekrana baxıb siçan/klaviatura ilə işləyir): məs. 'Instagram-ı brauzerdə "
       "aç, son videoya gir, baxış və şərhləri de', 'YouTube Studio-da son videonun statistikasını yoxla', 'Chrome-da "
       "X saytında formu doldur'. Arxa fonda işləyir, nəticə Telegram-a gəlir. Sadə tək əmrlər üçün mac_control işlət.",
       {"task": STR}, ["task"]),
    _s("mac_task_status", "Mac-də gedən çoxaddımlı tapşırığın vəziyyəti; stop=true dayandırır.",
       {"stop": {"type": "boolean"}}),
    _s("mac_control", "Sahibin Mac kompüterini (iMac) idarə et: proqram aç/bağla, sayt aç, montaj olunmuş videonu "
       "ekranda tam ekran oynat, ekran şəkli çək (Telegram-a göndərilir), bildiriş, Mac-dən səsli danış, səs səviyyəsi, "
       "musiqi/videonu dayandır, ekranı kilidlə, açıq proqramlar. action-lar: open_app (arg: proqram adı, "
       "məs. 'Google Chrome', 'Final Cut Pro', 'Telegram', 'Safari'), quit_app, open_url (arg: URL), "
       "play_video (arg: 'reels' | 'long' | 'latest' | URL — son montajdan), screenshot, notify (arg: mətn), "
       "say (arg: Mac-in dinamikindən deyiləcək mətn), volume (arg: 0-100), media (arg: pause|play|mute|unmute), "
       "lock, apps, perm_check (Mac icazələrini yoxla — xam nəticəni sahibə olduğu kimi de), shell (arg: terminal əmri — HƏMİŞƏ sahibin təsdiqi ilə icra olunur, yalnız lazım olanda).",
       {"action": {"type": "string", "enum": ["open_app", "quit_app", "open_url", "play_video", "screenshot", "notify",
                                              "say", "volume", "media", "lock", "apps", "update", "perm_check", "shell"]}, "arg": STR},
       ["action"]),
    _s("home_devices", "Ağıllı ev cihazları (Xiaomi robot süpürgə, lampalar, rozetkalar, kondisioner, sensorlar) və "
       "vəziyyətləri. query: ad üzrə filtr (məs. 'robot'), domain: vacuum/light/switch/climate/sensor.",
       {"query": STR, "domain": STR}),
    _s("home_action", "Ağıllı ev cihazına əmr. Robot süpürgə (vacuum.*): start (təmizlə), pause, stop, return_to_base "
       "(bazaya qayıt), locate (səs çıxart), set_fan_speed (data: {fan_speed}). İşıq/rozetka: turn_on, turn_off, toggle "
       "(işıq üçün data: {brightness_pct}). Kondisioner: set_temperature (data: {temperature}), set_hvac_mode. "
       "button.* üçün press. Əvvəlcə home_devices ilə entity_id tap.",
       {"entity_id": STR, "service": STR, "data": {"type": "object"}}, ["entity_id", "service"]),
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
    if name == "whatsapp_personal":
        return _ok(_wa_personal(a["to"], a["text"]))
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
    if name == "video_jobs":
        import pipeline
        out = []
        for j in db.list_jobs(8):
            r = j["result"] or {}
            out.append({"id": j["id"], "status": j["status"], "mərhələ": pipeline.STAGES.get(j["stage"], j["stage"]),
                        "tapşırıq": (j["plan"].get("source_text") or "")[:120], "dinamik": j["plan"].get("dynamic"),
                        "montaj": (r.get("long") or {}).get("url"), "reels": (r.get("reels") or {}).get("url"),
                        "xəta": (j["error"] or "")[:200],
                        **({"canlı": pipeline.LIVE[j["id"]]} if j["status"] == "running" and j["id"] in pipeline.LIVE else {})})
        return _ok(out)
    if name == "home_devices":
        from jarvis import home
        return _ok(home.devices(a.get("query", ""), a.get("domain", "")))
    if name == "home_action":
        from jarvis import home
        r = home.action(a["entity_id"], a["service"], a.get("data"))
        store.log("ev", f"{a['entity_id']} → {a['service']}: {'ok' if r.get('ok') else r.get('xəta', '')[:80]}")
        return _ok(r)
    if name == "mac_task":
        from jarvis import mac_operator as operator
        return _ok(operator.start(a["task"]))
    if name == "mac_task_status":
        from jarvis import mac_operator as operator
        if a.get("stop"):
            operator.STATE["stop"] = True
        return _ok(operator.status())
    if name == "mac_control":
        return _ok(_mac(a.get("action", ""), str(a.get("arg") or "")))
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
            "mac": __import__("jarvis.mac", fromlist=["x"]).online(),
        })
    return _ok({"xəta": f"naməlum alət {name}"})


def _latest_output(which: str) -> str | None:
    for j in db.list_jobs(15):
        r = j["result"] or {}
        for k in (["reels", "long"] if which in ("reels", "latest", "") else ["long", "reels"]):
            if k == which or which in ("latest", ""):
                if (r.get(k) or {}).get("url"):
                    return r[k]["url"]
    return None


def _norm_phone(x: str) -> str:
    d = re.sub(r"\D", "", x)
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 10 and d.startswith("0"):
        d = "994" + d[1:]
    elif len(d) == 9:
        d = "994" + d
    return d


def _wa_personal(to: str, text: str) -> dict:
    from jarvis import mac

    if not mac.online():
        return {"ok": False, "text": "Mac oflayndır — şəxsi WhatsApp mesajı Mac-dəki WhatsApp vasitəsilə gedir."}
    phone = _norm_phone(to) if len(re.sub(r"\D", "", to)) >= 7 else ""
    if not phone:
        r = mac.send("contacts", to.strip(), timeout=60)
        if r.get("ok") and r.get("text", "").strip() in ("", "tapılmadı") and len(to.split()) > 1:
            r = mac.send("contacts", to.split()[0], timeout=60)  # səs tanıma soyadı səhv yaza bilər
        if not r.get("ok"):
            return {"ok": False, "text": "Kontaktlara baxa bilmədim. Xam xəta: " + r.get("text", "")[:300],
                    "alternativ": "mac_task ilə WhatsApp-ı açıb axtarışda adı tapıb yazmaq olar — sahibə təklif et"}
        people = []
        for line in r["text"].splitlines():
            nm, _, phs = line.partition(": ")
            nums = [_norm_phone(p) for p in phs.split(",") if len(re.sub(r"\D", "", p)) >= 7]
            if nm.strip() and nums:
                people.append({"ad": nm.strip(), "nömrələr": list(dict.fromkeys(nums))})
        if not people:
            return {"ok": False, "text": f"Kontaktlarda «{to}» adlı nömrəsi olan adam tapılmadı. Nömrəni soruş."}
        if len(people) > 1 or len(people[0]["nömrələr"]) > 1:
            return {"ok": False, "seçim_lazımdır": people[:10],
                    "text": "Bir neçə uyğun kontakt/nömrə var — sahibdən hansı olduğunu soruş, sonra nömrə ilə göndər."}
        phone = people[0]["nömrələr"][0]
        to = people[0]["ad"]
    r = mac.send("whatsapp", f"{phone}\n{text}", timeout=60)
    store.log("whatsapp", f"Şəxsi WhatsApp → {to} (+{phone}): {text[:100]} [{r.get('text', '')[:60]}]")
    return {**r, "kimə": to, "nömrə": "+" + phone}


def _mac(action: str, arg: str) -> dict:
    from jarvis import mac

    if action == "shell":
        if not mac.online():
            return {"ok": False, "text": "Mac oflayndır."}
        return json.loads(_approval("mac_shell", {"cmd": arg}, f"💻 Mac-də terminal əmri icra olunsun:\n{arg[:1500]}"))
    if action == "play_video":
        url = arg if arg.startswith("http") else _latest_output(arg.lower().strip())
        if not url:
            return {"ok": False, "text": "Hazır montaj olunmuş video tapılmadı."}
        arg = url
    if action == "say":
        from jarvis.agent import tts
        import uuid
        audio = tts(arg)
        if not audio:
            return {"ok": False, "text": "Səs yaradılmadı (ElevenLabs)."}
        p = mac.SHOTS / f"say_{uuid.uuid4().hex[:12]}.mp3"
        p.write_bytes(audio)
        action, arg = "play_audio", f"{config.PUBLIC_URL}/media/mac/{p.name}"
    r = mac.send(action, arg, timeout=600 if action == "play_video" else 45)
    if action == "screenshot" and r.get("file"):
        notify.sink.send_photo(r["file"], "🖥 Mac ekranı")
        r["text"] = "Ekran şəkli Telegram-a göndərildi."
    store.log("mac", f"{action} {arg[:80]} → {r.get('text', '')[:80]}")
    return r


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
    elif p["kind"] == "mac_shell":
        from jarvis import mac
        r = mac.send("shell", a["cmd"], timeout=120)
        msg = ("✅" if r.get("ok") else "⚠️") + " Mac:\n" + (r.get("text") or "")[:3000]
    elif p["kind"] == "calendar_delete":
        g.calendar_delete(a["id"])
        store.log("görüş", f"Təqvimdən silindi: {a.get('title') or a['id']}")
        msg = "✅ Tədbir silindi."
    else:
        msg = "Naməlum əməliyyat."
    store.pending_set(pid, "icra olundu")
    return msg
