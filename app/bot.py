"""Telegram bot: video qəbulu, mətn/səsli əmrlər, gedişat, təsdiq düymələri."""
import asyncio
import logging
import os
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

import config
import db
import pipeline
import planner
import sites
import stt
import videos
from editor.transcript import fmt_ts

log = logging.getLogger("bot")

PRESET_BUTTONS = [
    ("🎬 Montaj + YouTube", "full_youtube"),
    ("📱 Reels + Instagram", "reels_instagram"),
    ("⚡ Hər ikisi", "both"),
    ("✂️ Yalnız montaj", "edit_only"),
]


# ---------- icazə
def _owner_ids() -> list[int]:
    ids = list(config.TELEGRAM_ALLOWED_USER_IDS)
    o = db.kv_get("owner_user")
    if o:
        ids.append(int(o))
    return ids


def _allowed(update: Update) -> bool:
    u = update.effective_user
    if not u:
        return False
    owners = _owner_ids()
    if not owners:
        # İlk /start yazan sahib olur
        db.kv_set("owner_user", u.id)
        db.kv_set("owner_chat", update.effective_chat.id)
        return True
    if u.id in owners:
        db.kv_set("owner_chat", update.effective_chat.id)
        return True
    return False


def _logos() -> list[str]:
    return [p.stem for p in config.LOGOS_DIR.glob("*.png")]


def _video_line(v) -> str:
    return f"#{v['id']} {v['name']} — {fmt_ts(v['duration'])}, {v['width']}×{v['height']}"


# ---------- komandalar
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return await update.message.reply_text(f"İcazə yoxdur. Sizin ID: {update.effective_user.id}")
    await update.message.reply_text(
        "Salam! Mən video montaj asistanınızam.\n\n"
        "• Video göndərin (2 GB-a qədər) — sonra nə edəcəyimi deyin və ya düyməni seçin.\n"
        "• Mətnlə və ya səsli mesajla tapşırıq verin: \"son videonu montaj et, reels hazırla, YouTube-a yüklə\".\n"
        "• Loqo: PNG faylı *sənəd* kimi göndərin, izahına `loqo nizami` yazın.\n"
        "• Sayt: \"Village Pro üçün menyu saytı yığ...\" yazın; şəkil göndərsəniz sayta qoyulur. "
        "/saytlar, /domen, /saytsil\n"
        "• /qaydalar — montaj qaydalarına bax / dəyiş\n"
        "• /status — son tapşırıq\n"
        "• /videolar — son videolar\n\n"
        f"Sizin Telegram ID: `{update.effective_user.id}`",
        parse_mode="Markdown",
    )


async def cmd_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    text = update.message.text.partition(" ")[2].strip()
    if text:
        config.set_rules(text)
        return await update.message.reply_text("✅ Qaydalar yeniləndi.")
    await update.message.reply_text(
        "Hazırkı montaj qaydaları:\n\n" + config.rules() + "\nDəyişmək üçün: /qaydalar <yeni mətn>")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text(status_text())


async def cmd_videos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    vs = db.list_videos(8)
    await update.message.reply_text("\n".join(_video_line(v) for v in vs) or "Hələ video yoxdur.")


def status_text() -> str:
    j = db.latest_job()
    if not j:
        return "Hələ tapşırıq yoxdur."
    st = {"queued": "növbədədir", "running": "icra olunur", "done": "hazırdır", "error": "xəta ilə bitdi"}
    s = f"Tapşırıq #{j['id']}: {st.get(j['status'], j['status'])}"
    if j["status"] == "running" and j["stage"]:
        s += f" ({pipeline.STAGES.get(j['stage'], j['stage'])})"
    r = j["result"] or {}
    for k, label in (("youtube", "YouTube"), ("instagram", "Instagram")):
        if r.get(k):
            s += f"\n{label}: {r[k]}"
    if j["status"] == "error":
        s += f"\nXəta: {j['error'][:300]}"
    return s


# ---------- video qəbulu
async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    m = update.message
    obj = m.video or m.document
    if m.document and not (m.document.mime_type or "").startswith("video/"):
        return await on_document(update, ctx)
    msg = await m.reply_text("📥 Video qəbul edilir...")
    f = await obj.get_file(read_timeout=600)
    tmp = config.WORK_DIR / f"tg_{obj.file_unique_id}"
    await f.download_to_drive(tmp, read_timeout=1800)
    name = getattr(obj, "file_name", None) or f"telegram_{obj.file_unique_id}.mp4"
    v = await asyncio.to_thread(videos.ingest, tmp, name, "telegram")
    if m.caption:
        await msg.edit_text(f"📥 {_video_line(v)}")
        return await handle_text(update, ctx, m.caption, video_id=v["id"])
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=f"p:{k}:{v['id']}")] for t, k in PRESET_BUTTONS])
    await msg.edit_text(f"📥 Qəbul edildi: {_video_line(v)}\n\nNə edək? Düyməni seçin və ya yazın/deyin.",
                        reply_markup=kb)


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    m = update.message
    d = m.document
    cap = (m.caption or "").strip()
    parts = cap.lower().replace("/", "").split()
    is_png = (d.mime_type or "") == "image/png" or (d.file_name or "").lower().endswith(".png")
    if is_png and parts and parts[0] in ("loqo", "logo"):
        name = parts[1] if len(parts) > 1 else "default"
        f = await d.get_file()
        await f.download_to_drive(config.LOGOS_DIR / f"{name}.png")
        return await m.reply_text(f"✅ Loqo saxlanıldı: {name}\nMövcud loqolar: {', '.join(_logos())}")
    if (d.mime_type or "").startswith("image/"):
        f = await d.get_file()
        tmp = config.WORK_DIR / f"doc_{d.file_unique_id}"
        await f.download_to_drive(tmp)
        sites.add_inbox_image(tmp, d.file_name or "sekil.jpg")
        if cap:
            return await handle_text(update, ctx, cap)
        return await m.reply_text(f"🖼 Şəkil götürüldü ({sites.inbox_count()} ədəd gözləyir). "
                                  "Loqo idisə, izahına \"loqo <ad>\" yazıb yenidən göndərin.")
    await m.reply_text("Bu faylı tanımadım. Video, şəkil və ya PNG loqo göndərin.")


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    m = update.message
    obj = m.voice or m.audio
    f = await obj.get_file()
    p = config.WORK_DIR / f"voice_{obj.file_unique_id}.ogg"
    await f.download_to_drive(p)
    try:
        text = await asyncio.to_thread(stt.transcribe_short, p)
    finally:
        p.unlink(missing_ok=True)
    if not text:
        return await m.reply_text("Səsi başa düşmədim, yenidən deyin.")
    await m.reply_text(f"🎙 «{text}»")
    await handle_text(update, ctx, text)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await handle_text(update, ctx, update.message.text)


async def handle_text(update: Update, ctx, text: str, video_id: int | None = None):
    latest = db.get_video(video_id) if video_id else db.latest_video()
    site_slugs = [s["slug"] for s in sites.list_sites()]
    plan = await asyncio.to_thread(planner.plan_from_text, text, latest, _logos(), site_slugs)
    if plan.get("action", "").startswith("site_"):
        return await handle_site(update, plan, site_slugs)
    if plan.get("action") == "status":
        return await update.effective_message.reply_text(status_text())
    if plan.get("action") == "chat":
        return await update.effective_message.reply_text(plan.get("reply") or "Video göndərin və tapşırığı deyin.")
    res = create_job(plan, update.effective_chat.id, "telegram", video_id)
    await update.effective_message.reply_text(res["message"])


async def handle_site(update: Update, plan: dict, slugs: list[str]):
    msg = update.effective_message
    action = plan["action"]
    req = plan.get("site_request") or plan.get("source_text", "")
    slug = plan.get("site_slug") if plan.get("site_slug") in slugs else (slugs[-1] if slugs else None)
    try:
        if action == "site_create" or not slug:
            n = sites.inbox_count()
            m = await msg.reply_text(f"🛠 Sayt yığılır{f' ({n} şəkil ilə)' if n else ''}... 1–3 dəqiqə.")
            r = await asyncio.to_thread(sites.create, plan.get("site_name") or "sayt", req)
            await m.edit_text(f"✅ Sayt hazırdır: {r['url']}\n\nDüzəliş üçün sadəcə yazın, məs. "
                              f"\"rəngi tündləşdir, WhatsApp düyməsi əlavə et\".")
        elif action == "site_edit":
            m = await msg.reply_text(f"✏️ {slug} saytında düzəliş edilir...")
            r = await asyncio.to_thread(sites.edit, slug, req)
            await m.edit_text(f"✅ Yeniləndi: {r['url']}\n(Brauzerdə səhifəni yeniləyin.) Geri qaytarmaq üçün: \"geri qaytar\"")
        elif action == "site_undo":
            ok = await asyncio.to_thread(sites.undo, slug)
            await msg.reply_text(f"↩️ Əvvəlki versiya qaytarıldı: {sites.site_url(slug)}" if ok
                                 else "Əvvəlki versiya yoxdur.")
    except Exception as e:  # noqa: BLE001
        log.exception("site")
        await msg.reply_text(f"❌ Sayt alınmadı: {str(e)[:500]}")


async def cmd_sites(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    ss = sites.list_sites()
    await update.message.reply_text("\n".join(f"• {s['slug']}: {s['url']}" for s in ss) or "Hələ sayt yoxdur.",
                                    disable_web_page_preview=True)


async def cmd_domain(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    args = ctx.args or []
    if len(args) != 2:
        return await update.message.reply_text("İstifadə: /domen <sayt> <domen>\nməs: /domen village-pro villagepro.az")
    slug, dom = args
    if slug not in [s["slug"] for s in sites.list_sites()]:
        return await update.message.reply_text("Belə sayt yoxdur. /saytlar ilə baxın.")
    sites.set_domain(slug, dom)
    ip = config.PUBLIC_URL.split("://")[-1].split(".sslip.io")[0].replace("-", ".")
    await update.message.reply_text(
        f"✅ {dom} → {slug} bağlandı.\n\nDomen panelində (Cloudflare və s.) A qeydi yaradın:\n"
        f"{dom} → {ip}\nwww → {ip}\n(Cloudflare-də proxy söndürülü — boz bulud.)\n"
        f"DNS yayılandan sonra https://{dom} özü açılacaq, SSL avtomatik verilir.")


async def cmd_delete_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    if not ctx.args:
        return await update.message.reply_text("İstifadə: /saytsil <sayt>")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🗑 Bəli, {ctx.args[0]} silinsin",
                                                     callback_data=f"sdel:{ctx.args[0]}")]])
    await update.message.reply_text("Əminsiniz? Bu geri qaytarılmır.", reply_markup=kb)


async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    (config.DATA_DIR / ".update").write_text("1")
    await update.message.reply_text("🔄 Son versiya yüklənir və sistem yenidən yığılır (2–4 dəq). "
                                    "Bot bir müddət cavab verməyə bilər.")


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    m = update.message
    ph = m.photo[-1]
    f = await ph.get_file()
    tmp = config.WORK_DIR / f"ph_{ph.file_unique_id}.jpg"
    await f.download_to_drive(tmp)
    sites.add_inbox_image(tmp, "foto.jpg")
    if m.caption:
        return await handle_text(update, ctx, m.caption)
    await m.reply_text(f"🖼 Şəkil götürüldü ({sites.inbox_count()} ədəd gözləyir). "
                       "Sonra yazın: \"yeni sayt yığ…\" və ya \"saytda bu şəkilləri qalereyaya əlavə et\".")


def create_job(plan: dict, chat_id: int | None, origin: str, video_id: int | None = None) -> dict:
    v = None
    if video_id:
        v = db.get_video(video_id)
    elif str(plan.get("video", "latest")).isdigit():
        v = db.get_video(int(plan["video"]))
    if not v:
        v = db.latest_video()
    if not v:
        return {"ok": False, "message": "Hələ video yoxdur. Əvvəlcə videonu Telegram bota göndərin."}
    jid = db.add_job(v["id"], plan, chat_id, origin)
    pipeline.wake()
    what = []
    if plan.get("long_video"):
        what.append("tam montaj")
    if plan.get("reels"):
        what.append("Reels")
    if plan.get("upload_youtube"):
        what.append("YouTube")
    if plan.get("upload_instagram"):
        what.append("Instagram")
    msg = (plan.get("reply") or "Not alıram.") + \
        f"\nVideo: {v['name']} ({fmt_ts(v['duration'])}). İş: {', '.join(what) or 'montaj'}. Tapşırıq #{jid}."
    return {"ok": True, "job_id": jid, "message": msg}


# ---------- düymələr
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _allowed(update):
        return await q.answer("İcazə yoxdur")
    await q.answer()
    kind, *rest = q.data.split(":")
    if kind == "p":
        name, vid = rest[0], int(rest[1])
        plan = planner.preset(name)
        res = create_job(plan, q.message.chat_id, "telegram", vid)
        await q.edit_message_reply_markup(None)
        await q.message.reply_text(res["message"])
    elif kind == "pub":
        target, jid = rest[0], int(rest[1])
        await q.edit_message_reply_markup(None)
        m = await q.message.reply_text(f"📤 {target.capitalize()}-a yüklənir...")
        try:
            link = await asyncio.to_thread(pipeline.publish, jid, target)
            await m.edit_text(f"✅ {target.capitalize()}: {link}")
        except Exception as e:  # noqa: BLE001
            await m.edit_text(f"❌ {target}: {e}")
    elif kind == "sdel":
        await q.edit_message_reply_markup(None)
        sites.delete(rest[0])
        await q.message.reply_text(f"🗑 {rest[0]} silindi.")
    elif kind == "skip":
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("Yaxşı, paylaşmıram. Fayllar linkdə qalır.")


# ---------- pipeline bildirişləri
class TelegramSink:
    def __init__(self, app: Application, loop: asyncio.AbstractEventLoop):
        self.app, self.loop = app, loop

    def _chat(self, job) -> int | None:
        return job["chat_id"] or db.kv_get("owner_chat")

    def _run(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=900)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram: %s", e)

    def progress(self, job_id, text):
        self._run(self._progress(job_id, text))

    async def _progress(self, job_id, text):
        job = db.get_job(job_id)
        chat = self._chat(job)
        if not chat:
            return
        if job["msg_id"]:
            try:
                await self.app.bot.edit_message_text(text, chat_id=chat, message_id=job["msg_id"])
                return
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    return
        m = await self.app.bot.send_message(chat, text)
        db.update_job(job_id, msg_id=m.message_id)

    def finished(self, job_id):
        self._run(self._finished(job_id))

    async def _finished(self, job_id):
        job = db.get_job(job_id)
        chat = self._chat(job)
        if not chat:
            return
        r = job["result"]
        lines = [f"🎬 Tapşırıq #{job_id} hazırdır."]
        if r.get("duration_before"):
            lines.append(f"Müddət: {fmt_ts(r['duration_before'])} → {fmt_ts(r['duration_after'])}")
        if r.get("long"):
            lines.append(f"\n📼 Montaj: {r['long']['url']}")
            if r["long"].get("title"):
                lines.append(f"Başlıq: {r['long']['title']}")
        if r.get("reels"):
            lines.append(f"📱 Reels ({fmt_ts(r['reels']['duration'])}): {r['reels']['url']}")
        if r.get("youtube"):
            lines.append(f"\n▶️ YouTube linkin hazır: {r['youtube']}")
        if r.get("instagram"):
            lines.append(f"📸 Instagram: {r['instagram']}")
        for e in r.get("errors", []):
            lines.append(f"⚠️ {e}")
        await self.app.bot.send_message(chat, "\n".join(lines), disable_web_page_preview=True)
        rp = r.get("reels", {}).get("path")
        if rp and os.path.getsize(rp) < 1900 * 1024 * 1024:
            with open(rp, "rb") as f:
                await self.app.bot.send_video(chat, f, caption="Reels önizləmə", supports_streaming=True,
                                              read_timeout=1800, write_timeout=1800)

    def confirm(self, job_id, targets):
        self._run(self._confirm(job_id, targets))

    async def _confirm(self, job_id, targets):
        job = db.get_job(job_id)
        chat = self._chat(job)
        labels = {"instagram": "📤 Instagram-a paylaş", "youtube": "📤 YouTube-a (public) yüklə"}
        kb = [[InlineKeyboardButton(labels[t], callback_data=f"pub:{t}:{job_id}")] for t in targets]
        kb.append([InlineKeyboardButton("✖️ Paylaşma", callback_data=f"skip:{job_id}")])
        cap = (job["result"].get("reels") or {}).get("caption", "")
        await self.app.bot.send_message(
            chat, "Baxın, hər şey qaydasındadırsa paylaşım üçün təsdiq edin." + (f"\n\nMətn:\n{cap}" if cap else ""),
            reply_markup=InlineKeyboardMarkup(kb))

    def failed(self, job_id, err):
        job = db.get_job(job_id)
        chat = self._chat(job)
        if chat:
            self._run(self.app.bot.send_message(chat, f"❌ Tapşırıq #{job_id} alınmadı:\n{err[:800]}"))


def build_app() -> Application:
    b = Application.builder().token(config.TELEGRAM_BOT_TOKEN)
    if config.TELEGRAM_LOCAL_MODE:
        b = (b.base_url(f"{config.TELEGRAM_API_BASE}/bot")
             .base_file_url(f"{config.TELEGRAM_API_BASE}/file/bot").local_mode(True))
    b = b.read_timeout(120).write_timeout(1800).media_write_timeout(1800).connect_timeout(30)
    app = b.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["qaydalar", "rules"], cmd_rules))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("videolar", cmd_videos))
    app.add_handler(CommandHandler("saytlar", cmd_sites))
    app.add_handler(CommandHandler("domen", cmd_domain))
    app.add_handler(CommandHandler("saytsil", cmd_delete_site))
    app.add_handler(CommandHandler("yenile", cmd_update))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, on_video))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app
