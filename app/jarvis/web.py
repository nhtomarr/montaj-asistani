"""Jarvis paneli və API: /jarvis, /api/jarvis/*, telefon agenti üçün /api/agent/jarvis."""
import asyncio
import base64
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
    return JARVIS_HTML.replace("__TOKEN__", t)


JARVIS_HTML = r"""<!doctype html><html lang="az"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>JARVIS</title>
<style>
:root{--bg:#030712;--panel:rgba(10,20,40,.72);--line:rgba(56,189,248,.22);--tx:#e0f2fe;--mut:#7dd3fc99;--acc:#38bdf8;--acc2:#818cf8;--ok:#34d399;--warn:#fbbf24;--err:#f87171}
*{box-sizing:border-box}html{background:#030712}body{margin:0;background:radial-gradient(ellipse 120% 60% at 50% 0%,#0b2545 0%,#030712 70%) no-repeat,#030712;color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100%}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.logo{letter-spacing:.35em;font-weight:800;font-size:18px;color:var(--acc);text-shadow:0 0 14px #38bdf8aa}
.clock{color:var(--mut);font-variant-numeric:tabular-nums}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;backdrop-filter:blur(8px)}
.stat b{display:block;font-size:26px;color:#fff}.stat span{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.grid{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px;backdrop-filter:blur(8px);min-height:120px}
.card h3{margin:0 0 10px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--acc)}
.item{padding:7px 0;border-top:1px solid #38bdf81a}.item:first-of-type{border:0}
.mut{color:var(--mut);font-size:12px}
.core{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:430px}
.orb{position:relative;width:min(300px,70vw);aspect-ratio:1;cursor:pointer;user-select:none;-webkit-user-select:none;touch-action:none}
.orb canvas{width:100%;height:100%}
.state{margin-top:8px;color:var(--mut);letter-spacing:.12em;text-transform:uppercase;font-size:12px}
.convo{width:100%;margin-top:12px;max-height:210px;overflow:auto;display:flex;flex-direction:column;gap:8px}
.msg{padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.msg.u{align-self:flex-end;background:#1e3a5f;max-width:85%}.msg.j{align-self:flex-start;background:#0b1730;border:1px solid var(--line);max-width:95%}
form{display:flex;gap:8px;width:100%;margin-top:10px}
input{flex:1;min-width:0;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:#06101f;color:var(--tx);font-size:16px}
button{background:linear-gradient(135deg,var(--acc),var(--acc2));border:0;color:#02101d;font-weight:700;border-radius:12px;padding:0 16px;cursor:pointer}
.btns{display:flex;gap:6px;margin-top:6px}.btns button{padding:6px 10px;font-size:12px}.btns .no{background:#334155;color:#e2e8f0}
a{color:var(--acc)}
@media(max-width:900px){.grid{grid-template-columns:1fr}.core{order:-1;min-height:auto}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<header><div class="logo">J.A.R.V.I.S</div><div class="clock" id="clock"></div></header>
<div class="stats">
<div class="stat"><b id="sUnread">–</b><span>Oxunmamış məktub</span></div>
<div class="stat"><b id="sEvents">–</b><span>Görüş (bu gün)</span></div>
<div class="stat"><b id="sTasks">–</b><span>Açıq tapşırıq</span></div>
<div class="stat"><b id="sActs">–</b><span>Bu gün görülən iş</span></div>
</div>
<div class="grid">
<div>
<div class="card"><h3>Təqvim</h3><div id="events"><span class="mut">Yüklənir…</span></div></div>
<div class="card" style="margin-top:14px"><h3>Təsdiq gözləyir</h3><div id="pending"></div></div>
</div>
<div class="card core">
<div class="orb" id="orb"><canvas id="cv" width="600" height="600"></canvas></div>
<div class="state" id="state">Danışmaq üçün kürəni basıb saxlayın</div>
<div class="convo" id="convo"></div>
<form id="f"><input id="q" placeholder="Jarvis-ə yazın…" autocomplete="off"><button>➤</button></form>
</div>
<div>
<div class="card"><h3>Fəaliyyət</h3><div id="activity"></div></div>
<div class="card" style="margin-top:14px"><h3>Tapşırıqlar · Rutinlər</h3><div id="tasks"></div></div>
</div>
</div></div>
<script>
const T="__TOKEN__",$=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
setInterval(()=>$('clock').textContent=new Date().toLocaleString('az-AZ',{timeZone:'Asia/Baku'}),1000);
let mode='idle';
// --- kürə animasiyası
const cv=$('cv'),cx=cv.getContext('2d');let ph=0,amp=0;
function draw(){ph+=.012;const W=600,c=W/2;cx.clearRect(0,0,W,W);
const target=mode==='listen'?1:mode==='think'?.55:mode==='speak'?.85:.18;amp+=(target-amp)*.06;
const g=cx.createRadialGradient(c,c,10,c,c,250);g.addColorStop(0,`rgba(56,189,248,${.55+amp*.3})`);g.addColorStop(.35,'rgba(37,99,235,.25)');g.addColorStop(1,'rgba(3,7,18,0)');
cx.fillStyle=g;cx.beginPath();cx.arc(c,c,250,0,7);cx.fill();
for(let r=0;r<4;r++){cx.strokeStyle=`rgba(125,211,252,${.25+.15*r})`;cx.lineWidth=1.2;cx.beginPath();
const R=110+r*34;for(let i=0;i<=180;i++){const a=i/180*Math.PI*2;const w=Math.sin(a*(3+r)+ph*(2+r))*amp*14;
const x=c+(R+w)*Math.cos(a+ph*(r%2?1:-1)*.3),y=c+(R+w)*Math.sin(a+ph*(r%2?1:-1)*.3);i?cx.lineTo(x,y):cx.moveTo(x,y)}cx.stroke()}
cx.save();cx.translate(c,c);for(let i=0;i<72;i++){cx.rotate(Math.PI*2/72);cx.fillStyle=i%6?'rgba(56,189,248,.35)':'rgba(186,230,253,.9)';
cx.fillRect(246,-1,i%6?8:14,2)}cx.restore();
cx.fillStyle='#e0f2fe';cx.beginPath();cx.arc(c,c,26+amp*10+Math.sin(ph*4)*2,0,7);cx.shadowColor='#38bdf8';cx.shadowBlur=40;cx.fill();cx.shadowBlur=0;
requestAnimationFrame(draw)}draw();
function setMode(m,t){mode=m;$('state').textContent=t}
function add(role,text){const d=document.createElement('div');d.className='msg '+role;d.textContent=text;$('convo').appendChild(d);$('convo').scrollTop=1e9}
function play(b64){if(!b64){setMode('idle','Hazıram');return}const a=new Audio('data:audio/mpeg;base64,'+b64);setMode('speak','Danışıram…');
a.onended=()=>setMode('idle','Hazıram');a.play().catch(()=>setMode('idle','Hazıram'))}
// --- mətn
$('f').onsubmit=async e=>{e.preventDefault();const v=$('q').value.trim();if(!v)return;$('q').value='';add('u',v);setMode('think','Düşünürəm…');
try{const r=await fetch('/api/jarvis/chat?t='+T,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:v,speak:false})});
const j=await r.json();add('j',j.reply);setMode('idle','Hazıram');load()}catch(err){add('j','Xəta: '+err);setMode('idle','Hazıram')}};
// --- səs (basıb saxla)
let rec,chunks=[];
async function startRec(){try{const s=await navigator.mediaDevices.getUserMedia({audio:true});
const type=MediaRecorder.isTypeSupported('audio/webm')?'audio/webm':'audio/mp4';rec=new MediaRecorder(s,{mimeType:type});chunks=[];
rec.ondataavailable=e=>chunks.push(e.data);rec.onstop=async()=>{s.getTracks().forEach(t=>t.stop());setMode('think','Düşünürəm…');
const fd=new FormData();fd.append('file',new Blob(chunks,{type}),'voice.'+(type.includes('webm')?'webm':'m4a'));
try{const r=await fetch('/api/jarvis/voice?t='+T,{method:'POST',body:fd});const j=await r.json();if(j.heard)add('u',j.heard);add('j',j.reply);play(j.audio);load()}
catch(err){add('j','Xəta: '+err);setMode('idle','Hazıram')}};rec.start();setMode('listen','Dinləyirəm…')}catch(e){setMode('idle','Mikrofona icazə verin')}}
function stopRec(){if(rec&&rec.state==='recording')rec.stop()}
const orb=$('orb');orb.addEventListener('pointerdown',e=>{e.preventDefault();startRec()});
['pointerup','pointerleave','pointercancel'].forEach(ev=>orb.addEventListener(ev,stopRec));
// --- məlumatlar
async function decide(id,ok){const r=await fetch('/api/jarvis/approve?t='+T,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({id,ok})});
add('j',(await r.json()).message);load()}
async function load(){const r=await fetch('/api/jarvis/overview?t='+T);if(!r.ok)return;const d=await r.json();
$('sUnread').textContent=d.unread??'–';$('sTasks').textContent=d.tasks.length;$('sActs').textContent=d.today_actions;
const today=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Baku'});const evT=d.events.filter(e=>String(e.start).startsWith(today));
$('sEvents').textContent=d.google?evT.length:'–';
$('events').innerHTML=!d.google?'<span class="mut">Google hələ qoşulmayıb (setup səhifəsi)</span>':
(d.events.map(e=>`<div class="item"><b>${esc(String(e.start).slice(11,16)||'Bütün gün')}</b> ${esc(e.title)}<div class="mut">${esc(String(e.start).slice(0,10))}${e.link?` · <a href="${esc(e.link)}" target="_blank">link</a>`:''}</div></div>`).join('')||'<span class="mut">Yaxın 2 gündə görüş yoxdur</span>');
$('pending').innerHTML=d.pending.map(p=>`<div class="item"><div style="white-space:pre-wrap">${esc(p.summary.slice(0,400))}</div><div class="btns"><button onclick="decide(${p.id},true)">Təsdiqlə</button><button class="no" onclick="decide(${p.id},false)">Ləğv</button></div></div>`).join('')||'<span class="mut">Yoxdur</span>';
$('activity').innerHTML=d.activity.map(a=>`<div class="item">${esc(a.summary)}<div class="mut">${esc(a.t)} · ${esc(a.kind)}</div></div>`).join('')||'<span class="mut">Hələ fəaliyyət yoxdur</span>';
$('tasks').innerHTML=(d.tasks.map(t=>`<div class="item">☐ ${esc(t.title)}<div class="mut">${esc(t.due||'')} ${esc(t.owner||'')} ${t.priority==='yüksək'?'· 🔴':''}</div></div>`).join('')||'<span class="mut">Açıq tapşırıq yoxdur</span>')
+d.routines.map(r=>`<div class="item">🔁 ${esc(r.name)}<div class="mut">${esc(r.schedule)}</div></div>`).join('')
+d.reminders.map(r=>`<div class="item">⏰ ${esc(r.text)}<div class="mut">${esc(r.at)}</div></div>`).join('')}
load();setInterval(load,30000);
</script></body></html>"""
