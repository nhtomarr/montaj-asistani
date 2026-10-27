"""Veb server: telefon agenti üçün API, fayl paylaşımı (/media), sadə panel və yükləmə."""
import asyncio
import os
import contextlib
import hmac
import logging
import re
import shutil
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import db
import notify
import pipeline
import planner
import videos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")


_tg = {"app": None}


async def _start_bot():
    """Bot API serveri hazır olana qədər təkrar cəhd edir; uğursuz olsa belə veb (setup) işləməyə davam edir."""
    import bot

    for attempt in range(40):
        tg = bot.build_app()
        try:
            await tg.initialize()
            await tg.start()
            await tg.updater.start_polling(drop_pending_updates=False)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram bot qoşulmadı (%s/40): %s", attempt + 1, e)
            await asyncio.sleep(15)
            continue
        _tg["app"] = tg
        db.kv_set("bot_username", tg.bot.username)
        notify.set_sink(bot.TelegramSink(tg, asyncio.get_running_loop()))
        log.info("Telegram bot işə düşdü: @%s", tg.bot.username)
        return


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline.start_worker()
    from jarvis import scheduler as jsched

    jsched.start()
    task = asyncio.create_task(_start_bot()) if config.TELEGRAM_BOT_TOKEN else None
    yield
    if task:
        task.cancel()
    tg = _tg["app"]
    if tg:
        await tg.updater.stop()
        await tg.stop()
        await tg.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/media", StaticFiles(directory=config.PUBLIC_DIR), name="media")
from setup_page import router as setup_router  # noqa: E402
import sites  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402

app.include_router(setup_router)
import auth  # noqa: E402

app.include_router(auth.router)
app.middleware("http")(auth.middleware)
from jarvis.web import router as jarvis_router  # noqa: E402

app.include_router(jarvis_router)
from jarvis.mac import router as mac_router  # noqa: E402

app.include_router(mac_router)


@app.middleware("http")
async def serve_sites(request: Request, call_next):
    """slug.<DOMAIN> və ya bağlanmış domen gəlirsə, saytın statik fayllarını verir."""
    slug = sites.resolve_host(request.headers.get("host", ""))
    if not slug:
        return await call_next(request)
    base = (sites.SITES_DIR / slug).resolve()
    rel = request.url.path.lstrip("/") or "index.html"
    if rel.startswith("history"):
        return Response(status_code=404)
    p = (base / rel).resolve()
    if p.is_dir():
        p = p / "index.html"
    if not str(p).startswith(str(base)) or not p.is_file():
        p = base / "index.html"
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/api/tls-ask")
def tls_ask(domain: str = ""):
    """Caddy on-demand TLS: yalnız tanınan saytlar üçün sertifikat alınsın."""
    panel = {d.strip().lower() for d in os.getenv("PANEL_DOMAINS", "").split(",") if d.strip()}
    return Response(status_code=200 if (sites.resolve_host(domain) or domain.lower() in panel) else 404)




def _check_panel(t: str | None):
    if not config.PANEL_TOKEN or not t or not hmac.compare_digest(t, config.PANEL_TOKEN):
        raise HTTPException(403, "icazə yoxdur")


def _check_agent(req: Request):
    secret = req.headers.get("x-agent-secret") or req.query_params.get("secret") or ""
    if not config.AGENT_TOOL_SECRET or not hmac.compare_digest(secret, config.AGENT_TOOL_SECRET):
        raise HTTPException(403, "icazə yoxdur")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")[-9:]


@app.get("/health")
def health():
    return {"ok": True}


# ---------- Telefon agenti (ElevenLabs server tools)
@app.post("/api/agent/task")
async def agent_task(req: Request):
    _check_agent(req)
    body = await req.json()
    instruction = (body.get("instruction") or "").strip()
    caller = body.get("caller_id") or ""
    if config.ALLOWED_CALLER_IDS and _digits(caller) not in {_digits(x) for x in config.ALLOWED_CALLER_IDS}:
        return {"ok": False, "message": "Bu nömrədən tapşırıq qəbul edə bilmərəm."}
    if not instruction:
        return {"ok": False, "message": "Tapşırığı eşitmədim, bir daha deyin."}
    import bot  # create_job burada

    latest = db.latest_video()
    plan = await asyncio.to_thread(planner.plan_from_text, instruction, latest,
                                   [p.stem for p in config.LOGOS_DIR.glob("*.png")])
    if plan.get("action") == "status":
        return {"ok": True, "message": bot.status_text()}
    if plan.get("action") == "chat":
        return {"ok": True, "message": plan.get("reply") or "Başa düşmədim, tapşırığı bir daha deyin."}
    res = bot.create_job(plan, db.kv_get("owner_chat"), "phone")
    if res["ok"]:
        res["message"] += " Bitəndə Telegram-a link göndərəcəyəm."
    return res


@app.post("/api/agent/status")
@app.get("/api/agent/status")
def agent_status(req: Request):
    _check_agent(req)
    import bot

    return {"ok": True, "message": bot.status_text()}


@app.get("/api/agent/videos")
def agent_videos(req: Request):
    _check_agent(req)
    vs = db.list_videos(5)
    return {"videos": [{"id": v["id"], "name": v["name"], "minutes": round(v["duration"] / 60, 1)} for v in vs]}


# ---------- Panel
@app.get("/panel", response_class=HTMLResponse)
def panel(t: str | None = None):
    _check_panel(t)
    return PANEL_HTML.replace("__TOKEN__", t)


@app.get("/api/jobs")
def api_jobs(t: str | None = None):
    _check_panel(t)
    out = []
    for j in db.list_jobs(25):
        v = db.get_video(j["video_id"]) if j["video_id"] else None
        r = j["result"] or {}
        out.append({
            "id": j["id"], "status": j["status"], "stage": pipeline.STAGES.get(j["stage"], ""),
            "origin": j["origin"], "video": v["name"] if v else "", "error": j["error"],
            "long": (r.get("long") or {}).get("url"), "reels": (r.get("reels") or {}).get("url"),
            "youtube": r.get("youtube"), "instagram": r.get("instagram"),
            "text": j["plan"].get("source_text", ""),
        })
    return JSONResponse(out)


@app.post("/upload")
async def upload(t: str | None = None, file: UploadFile = File(...)):
    _check_panel(t)
    tmp = config.WORK_DIR / f"up_{uuid.uuid4().hex}"
    with open(tmp, "wb") as f:
        await asyncio.to_thread(shutil.copyfileobj, file.file, f, 8 * 1024 * 1024)
    v = await asyncio.to_thread(videos.ingest, tmp, file.filename or "video.mp4", "web")
    return {"ok": True, "video": v}


@app.post("/api/task")
async def web_task(req: Request, t: str | None = None):
    _check_panel(t)
    body = await req.json()
    import bot

    plan = await asyncio.to_thread(planner.plan_from_text, body.get("instruction", ""), db.latest_video(),
                                   [p.stem for p in config.LOGOS_DIR.glob("*.png")])
    if plan.get("action") != "process":
        return {"ok": True, "message": plan.get("reply") or bot.status_text()}
    return bot.create_job(plan, db.kv_get("owner_chat"), "web")


PANEL_HTML = """<!doctype html><html lang="az"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Montaj Asistanı</title>
<style>
:root{--bg:#0b1020;--card:#131a2e;--line:#243052;--tx:#e8ecf7;--mut:#8b95b3;--acc:#38bdf8;--ok:#34d399;--err:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
input[type=text]{width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:#0e1426;color:var(--tx)}
button{background:var(--acc);color:#04121d;border:0;border-radius:8px;padding:9px 14px;font-weight:600;cursor:pointer}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.mut{color:var(--mut);font-size:13px}
.job{border-top:1px solid var(--line);padding:10px 0}.job:first-child{border:0}
.st-done{color:var(--ok)}.st-error{color:var(--err)}.st-running{color:var(--acc)}a{color:var(--acc)}
progress{width:100%}
</style></head><body><main>
<h1>🎬 Montaj Asistanı</h1>
<div class="card"><div class="row"><input type="file" id="f" accept="video/*"><button onclick="up()">Yüklə</button></div>
<progress id="pg" value="0" max="1" hidden></progress><div class="mut" id="um"></div></div>
<div class="card"><div class="row"><input type="text" id="ins" placeholder="Məs: son videonu montaj et, reels hazırla, YouTube-a yüklə">
<button onclick="task()">Göndər</button></div><div class="mut" id="tm"></div></div>
<div class="card" id="jobs"></div>
</main><script>
const T="__TOKEN__";
function up(){const f=document.getElementById('f').files[0];if(!f)return;const x=new XMLHttpRequest(),fd=new FormData();
fd.append('file',f);const pg=document.getElementById('pg');pg.hidden=false;x.upload.onprogress=e=>pg.value=e.loaded/e.total;
x.onload=()=>{document.getElementById('um').textContent=x.status==200?'✅ Video qəbul edildi':'Xəta: '+x.responseText;pg.hidden=true};
x.open('POST','/upload?t='+T);x.send(fd)}
async function task(){const v=document.getElementById('ins').value;if(!v)return;
const r=await fetch('/api/task?t='+T,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({instruction:v})});
document.getElementById('tm').textContent=(await r.json()).message;load()}
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){const r=await fetch('/api/jobs?t='+T);const js=await r.json();
document.getElementById('jobs').innerHTML=js.map(j=>`<div class="job"><b>#${j.id}</b> <span class="st-${j.status}">${j.status}</span>
${j.stage?'— '+esc(j.stage):''} <span class="mut">${esc(j.origin)} · ${esc(j.video)}</span><div class="mut">${esc(j.text)}</div>
<div class="row">${j.long?`<a href="${j.long}" target="_blank">Montaj</a>`:''}${j.reels?`<a href="${j.reels}" target="_blank">Reels</a>`:''}
${j.youtube?`<a href="${j.youtube}" target="_blank">YouTube</a>`:''}${j.instagram?`<a href="${j.instagram}" target="_blank">Instagram</a>`:''}</div>
${j.error?`<div class="st-error">${esc(j.error.slice(0,300))}</div>`:''}</div>`).join('')||'<span class="mut">Hələ tapşırıq yoxdur</span>'}
load();setInterval(load,4000);
</script></body></html>"""
