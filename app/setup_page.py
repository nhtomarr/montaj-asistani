"""Telefondan quraşdırma: /setup səhifəsi açarları data/setup.env-ə yazır və konteynerləri yenidən başladır."""
import hmac
import html
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import config
import db
from jarvis.google_api import SCOPES as GOOGLE_SCOPES

router = APIRouter()
ENV_FILE = config.DATA_DIR / "setup.env"
RESTART_FLAG = config.DATA_DIR / ".restart"

FIELDS = [
    # (açar, etiket, gizli?, kömək)
    ("PANEL_PASSWORD", "Panel şifrəsi (qısa giriş üçün)", True, "Jarvis-ə /login ilə bu şifrə ilə girəcəksiniz"),
    ("HA_TOKEN", "Ağıllı ev — Home Assistant tokeni", True, "Home Assistant → Profil → Təhlükəsizlik → Long-lived access token"),
    ("OAUTH_BASE_URL", "Google girişi üçün ünvan (istəyə görə)", False, "boş qalsa server ünvanı; məs. https://jarvis.pilothayat.az"),
    ("JARVIS_EVENING_TIME", "Axşam yekunu saatı", False, "məs. 20:30 (boş = 20:30)"),
    ("JARVIS_MAC_BRIEF", "Səhər brifinqini Mac-də səslə de", False, "1 = bəli (default), 0 = xeyr"),
    ("PANEL_DOMAINS", "Panel üçün öz domen(lər)iniz", False, "məs. jarvis.pilothayat.az — DNS-də A qeydi serverin IP-sinə"),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", True, "Telegram → @BotFather → /newbot"),
    ("TELEGRAM_API_ID", "Telegram API ID", False, "my.telegram.org → API development tools"),
    ("TELEGRAM_API_HASH", "Telegram API HASH", True, "eyni səhifədən"),
    ("ANTHROPIC_API_KEY", "Anthropic (Claude) API key", True, "console.anthropic.com → API Keys"),
    ("ELEVENLABS_API_KEY", "ElevenLabs API key", True, "elevenlabs.io → Developers → API Keys"),
    ("ALLOWED_CALLER_IDS", "Zəng icazəli nömrə(lər)", False, "məs. +994501234567; vergüllə bir neçə"),
    ("YOUTUBE_CLIENT_ID", "Google OAuth Client ID", False, "Google Cloud → Credentials → OAuth client (Web application)"),
    ("YOUTUBE_CLIENT_SECRET", "Google OAuth Client Secret", True, ""),
    ("YOUTUBE_DEFAULT_PRIVACY", "YouTube default görünmə", False, "private | unlisted | public"),
    ("INSTAGRAM_USER_ID", "Instagram User ID", False, "developers.facebook.com → Instagram → API setup"),
    ("INSTAGRAM_ACCESS_TOKEN", "Instagram access token", True, "60 günlük token, server özü yeniləyir"),
    ("META_ADS_TOKEN", "Meta reklam tokeni (System User)", True, "business.facebook.com → Business ayarları → System users → Generate token (ads_management, ads_read, business_management)"),
    ("META_AD_ACCOUNTS", "Meta reklam hesabları (boş = hamısı)", False, "act_123..., act_456... — vergüllə"),
    ("GOOGLE_ADS_DEVELOPER_TOKEN", "Google Ads developer token", True, "ads.google.com → Tools → API Center"),
    ("GOOGLE_ADS_CUSTOMER_IDS", "Google Ads hesab ID-ləri", False, "123-456-7890, ... — vergüllə"),
    ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "Google Ads MCC (manager) ID", False, "MCC ilə idarə olunursa"),
    ("TIKTOK_ACCESS_TOKEN", "TikTok Ads access token", True, "business-api.tiktok.com → developer app"),
    ("TIKTOK_ADVERTISER_IDS", "TikTok advertiser ID-ləri", False, "vergüllə"),
    ("ADS_LOW_BALANCE", "Aşağı balans xəbərdarlığı həddi", False, "məs. 20"),
    ("PEXELS_API_KEY", "Pexels API key (B-roll stok videolar)", True, "pexels.com/api → pulsuz açar"),
    ("MONITOR_URLS", "Nəzarət olunan saytlar", False, "https://luxresidence.az, https://marox.az — vergüllə"),
    ("WHATSAPP_TOKEN", "WhatsApp Cloud API token", True, "developers.facebook.com → WhatsApp → API Setup (System User tokeni tövsiyə olunur)"),
    ("WHATSAPP_PHONE_ID", "WhatsApp Phone number ID", False, "eyni səhifədən"),
    ("WHATSAPP_VERIFY_TOKEN", "WhatsApp webhook verify token", False, "istədiyiniz söz, webhook ayarında da eyni yazılır"),
    ("WHATSAPP_APP_SECRET", "Meta App Secret", True, "App settings → Basic"),
    ("WHATSAPP_BUSINESS", "WhatsApp-da təqdim olunan şirkət", False, "məs. LUX Residence"),
    ("WHATSAPP_AUTO_REPLY", "WhatsApp avtomatik cavab", False, "true | false"),
    ("JARVIS_AUTO_EMAIL", "Jarvis rutin məktubları özü göndərsin", False, "true | false"),
    ("JARVIS_REPORT_TIME", "Səhər hesabatı saatı", False, "09:00"),
    ("STT_PROVIDER", "Səs → mətn xidməti", False, "elevenlabs | openai"),
    ("OPENAI_API_KEY", "OpenAI API key (Whisper, istəyə görə)", True, "platform.openai.com"),
]


def _read_env() -> dict:
    out = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _write_env(d: dict) -> None:
    ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in d.items() if v != ""), encoding="utf-8")
    ENV_FILE.chmod(0o600)


def _restart() -> None:
    RESTART_FLAG.write_text("1")


def _check(t: str | None) -> str:
    if not config.PANEL_TOKEN or not t or not hmac.compare_digest(t, config.PANEL_TOKEN):
        raise HTTPException(403, "icazə yoxdur")
    return t


def _status(env: dict) -> list[tuple[bool, str]]:
    bot = db.kv_get("bot_username")
    return [
        (bool(bot), f"Telegram bot işləyir: @{bot}" if bot else "Telegram bot hələ qoşulmayıb"),
        (bool(env.get("ANTHROPIC_API_KEY")), "Claude API"),
        (bool(env.get("ELEVENLABS_API_KEY")), "ElevenLabs (səs → mətn və telefon)"),
        (bool(env.get("GOOGLE_REFRESH_TOKEN")), "Google: Gmail, Calendar, Drive, YouTube"),
        (bool(env.get("INSTAGRAM_ACCESS_TOKEN") and env.get("INSTAGRAM_USER_ID")), "Instagram"),
        (bool(env.get("META_ADS_TOKEN") or env.get("GOOGLE_ADS_DEVELOPER_TOKEN") or env.get("TIKTOK_ACCESS_TOKEN")), "Reklam kabinetləri"),
        (bool(env.get("WHATSAPP_TOKEN")), "WhatsApp"),
    ]


@router.get("/setup", response_class=HTMLResponse)
def setup_get(t: str | None = None, msg: str = ""):
    t = _check(t)
    env = _read_env()
    if not env.get("AGENT_TOOL_SECRET"):
        env["AGENT_TOOL_SECRET"] = secrets.token_hex(24)
        _write_env(env)
    rows = []
    for key, label, secret, hint in FIELDS:
        cur = env.get(key, "")
        if secret:
            ph = "•••••• saxlanıb (dəyişmək üçün yenisini yazın)" if cur else ""
            val = ""
        else:
            ph, val = "", cur
        rows.append(
            f'<label>{html.escape(label)}<input name="{key}" value="{html.escape(val)}" '
            f'placeholder="{html.escape(ph)}" autocomplete="off" autocapitalize="off" spellcheck="false">'
            f'<small>{html.escape(hint)}</small></label>'
        )
    st = "".join(f'<li class="{"ok" if ok else "no"}">{"✅" if ok else "▫️"} {html.escape(s)}</li>'
                 for ok, s in _status(env))
    yt_btn = ""
    if env.get("YOUTUBE_CLIENT_ID") and env.get("YOUTUBE_CLIENT_SECRET"):
        yt_btn = f'<a class="btn" href="/oauth/youtube/start?t={t}">🔗 Google hesabını qoş (Gmail, Calendar, Drive, YouTube)</a>'
    base = config.PUBLIC_URL
    page = SETUP_HTML.format(
        msg=f'<div class="msg">{html.escape(msg)}</div>' if msg else "",
        status=st, rows="".join(rows), t=t, yt_btn=yt_btn,
        redirect=html.escape(f"{_oauth_base()}/oauth/youtube/callback"),
        panel=html.escape(f"{base}/panel?t={t}"), jarvis=html.escape(f"{base}/jarvis?t={t}"),
        task_url=html.escape(f"{base}/api/agent/task"), status_url=html.escape(f"{base}/api/agent/status"),
        secret=html.escape(env["AGENT_TOOL_SECRET"]), wa_url=html.escape(f"{base}/api/whatsapp/webhook"),
    )
    return page


@router.post("/setup")
async def setup_post(request: Request, t: str | None = None):
    t = _check(t)
    form = await request.form()
    env = _read_env()
    old_tg = env.get("TELEGRAM_BOT_TOKEN")
    for key, _label, _secret, _hint in FIELDS:
        v = (form.get(key) or "").strip()
        if v == "-":
            env.pop(key, None)  # "-" yazmaq dəyəri silir
        elif v:
            env[key] = v
    _write_env(env)
    new_tg = env.get("TELEGRAM_BOT_TOKEN")
    if new_tg and new_tg != old_tg:
        # Botu rəsmi serverdən çıxarırıq ki, öz Bot API serverimizdə işləsin (2 GB fayl limiti)
        try:
            httpx.get(f"https://api.telegram.org/bot{new_tg}/logOut", timeout=20)
        except httpx.HTTPError:
            pass
        db.kv_set("bot_username", None)
    _restart()
    return RedirectResponse(f"/setup?t={t}&msg=" + urllib.parse.quote(
        "Yadda saxlanıldı. Sistem yenidən başlayır — 30–60 saniyə sonra səhifəni yeniləyin."), status_code=303)


def _oauth_base() -> str:
    """Google sslip.io ünvanını qəbul etməsə, öz domeninizi (məs. https://jarvis.pilothayat.az) yazmaq olar."""
    import os
    return (os.getenv("OAUTH_BASE_URL") or config.PUBLIC_URL).rstrip("/")


# ---- YouTube OAuth (brauzerdən, kompüter lazım deyil)
@router.get("/oauth/youtube/start")
def yt_start(t: str | None = None):
    t = _check(t)
    env = _read_env()
    q = urllib.parse.urlencode({
        "client_id": env.get("YOUTUBE_CLIENT_ID", ""),
        "redirect_uri": f"{_oauth_base()}/oauth/youtube/callback",
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": t,
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{q}")


@router.get("/oauth/youtube/callback")
def yt_callback(code: str = "", state: str = "", error: str = ""):
    t = _check(state)
    if error or not code:
        return RedirectResponse(f"/setup?t={t}&msg=" + urllib.parse.quote(f"YouTube icazəsi alınmadı: {error}"), 303)
    env = _read_env()
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": env.get("YOUTUBE_CLIENT_ID"), "client_secret": env.get("YOUTUBE_CLIENT_SECRET"),
        "redirect_uri": f"{_oauth_base()}/oauth/youtube/callback", "grant_type": "authorization_code",
    }, timeout=30)
    rt = r.json().get("refresh_token") if r.status_code == 200 else None
    if not rt:
        return RedirectResponse(f"/setup?t={t}&msg=" + urllib.parse.quote(f"YouTube xətası: {r.text[:200]}"), 303)
    env["YOUTUBE_REFRESH_TOKEN"] = rt
    env["GOOGLE_REFRESH_TOKEN"] = rt
    _write_env(env)
    _restart()
    return RedirectResponse(f"/setup?t={t}&msg=" + urllib.parse.quote("✅ Google qoşuldu (Gmail, Calendar, Drive, YouTube). Sistem yenidən başlayır."), 303)


SETUP_HTML = """<!doctype html><html lang="az"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Asistan quraşdırma</title>
<style>
:root{{--bg:#0b1020;--card:#131a2e;--line:#243052;--tx:#e8ecf7;--mut:#8b95b3;--acc:#38bdf8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:16px/1.5 system-ui,-apple-system,sans-serif}}
main{{max-width:640px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:21px;margin:0 0 14px}}h2{{font-size:16px;margin:0 0 10px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}}
label{{display:block;margin-bottom:14px;font-weight:600;font-size:14px}}
input{{display:block;width:100%;margin-top:5px;padding:11px 12px;border-radius:8px;border:1px solid var(--line);
background:#0e1426;color:var(--tx);font-size:16px}}small{{color:var(--mut);font-weight:400}}
button,.btn{{display:inline-block;background:var(--acc);color:#04121d;border:0;border-radius:8px;padding:12px 18px;
font-weight:700;font-size:16px;text-decoration:none;cursor:pointer}}button{{width:100%}}
ul{{list-style:none;padding:0;margin:0}}li{{padding:3px 0}}li.no{{color:var(--mut)}}
.msg{{background:#123524;border:1px solid #1f6f4a;border-radius:10px;padding:12px;margin-bottom:14px}}
code{{word-break:break-all;background:#0e1426;padding:2px 5px;border-radius:5px;font-size:13px}}a{{color:var(--acc)}}
</style></head><body><main>
<h1>🎬 Montaj Asistanı — quraşdırma</h1>{msg}
<div class="card"><h2>Vəziyyət</h2><ul>{status}</ul></div>
<form method="post" action="/setup?t={t}" class="card"><h2>Açarlar</h2><p><small>Boş xana = dəyişmir. Dəyəri silmək üçün <b>-</b> yazın.</small></p>{rows}<button>Yadda saxla</button></form>
<div class="card"><h2>Google (Gmail, Calendar, Drive, YouTube)</h2>
<p><small>Google Cloud-da OAuth client (<b>Web application</b>) yaradanda <i>Authorized redirect URI</i> kimi bunu yazın:</small><br><code>{redirect}</code></p>
{yt_btn}</div>
<div class="card"><h2>WhatsApp webhook</h2>
<p><small>Callback URL:</small><br><code>{wa_url}</code></p><p><small>Verify token: setup-da yazdığınız WHATSAPP_VERIFY_TOKEN. Abunə: <b>messages</b>.</small></p></div>
<div class="card"><h2>ElevenLabs telefon agenti</h2>
<p><small>Tool URL (POST):</small><br><code>{task_url}</code></p>
<p><small>Status URL (GET):</small><br><code>{status_url}</code></p>
<p><small>Header <b>X-Agent-Secret</b>:</small><br><code>{secret}</code></p></div>
<div class="card"><h2>Panel</h2><p><a href="{jarvis}">🔵 JARVIS paneli →</a></p><a href="{panel}">Video tapşırıqları və yükləmə →</a></div>
</main></body></html>"""
