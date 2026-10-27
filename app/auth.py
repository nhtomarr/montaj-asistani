"""Sadə giriş: link bir dəfə açılanda (və ya şifrə ilə) brauzer 1 il yadda saxlanır, sonra qısa ünvan kifayətdir."""
import asyncio
import hashlib
import hmac
import html
import os
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import config

COOKIE = "jv_auth"
router = APIRouter()


def cookie_value() -> str:
    return hmac.new(config.PANEL_TOKEN.encode(), b"jarvis-cookie-v1", hashlib.sha256).hexdigest() \
        if config.PANEL_TOKEN else ""


def has_cookie(request: Request) -> bool:
    v = cookie_value()
    return bool(v) and hmac.compare_digest(request.cookies.get(COOKIE, ""), v)


async def middleware(request: Request, call_next):
    qs = parse_qs(request.scope.get("query_string", b"").decode())
    t = (qs.get("t") or [""])[0]
    valid_t = bool(config.PANEL_TOKEN) and bool(t) and hmac.compare_digest(t, config.PANEL_TOKEN)
    if not t and not has_cookie(request) and request.url.path in ("/jarvis", "/panel", "/zeng"):
        return RedirectResponse("/login", 303)
    if not t and has_cookie(request):
        # Kukisi olan istifadəçi üçün tokeni gizlicə əlavə edirik — bütün səhifələr və API-lər işləyir
        qs["t"] = [config.PANEL_TOKEN]
        request.scope["query_string"] = urlencode(qs, doseq=True).encode()
    response = await call_next(request)
    if valid_t and not has_cookie(request):
        response.set_cookie(COOKIE, cookie_value(), max_age=365 * 86400, httponly=True, secure=True, samesite="lax")
    return response


def _password_ok(p: str) -> bool:
    pw = os.getenv("PANEL_PASSWORD", "")
    return (bool(pw) and hmac.compare_digest(p, pw)) or (bool(config.PANEL_TOKEN) and hmac.compare_digest(p, config.PANEL_TOKEN))


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse("/jarvis" if has_cookie(request) else "/login", 303)


@router.get("/j")
def short(request: Request):
    return RedirectResponse("/jarvis" if has_cookie(request) else "/login", 303)


@router.get("/login", response_class=HTMLResponse)
def login_page(err: str = ""):
    return LOGIN_HTML.replace("__ERR__", html.escape(err))


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    if _password_ok(str(form.get("p", "")).strip()):
        r = RedirectResponse("/jarvis", 303)
        r.set_cookie(COOKIE, cookie_value(), max_age=365 * 86400, httponly=True, secure=True, samesite="lax")
        return r
    await asyncio.sleep(1.5)
    return RedirectResponse("/login?err=Şifrə+səhvdir", 303)


@router.get("/logout")
def logout():
    r = RedirectResponse("/login", 303)
    r.delete_cookie(COOKIE)
    return r


LOGIN_HTML = """<!doctype html><html lang="az"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#020a1a"><title>JARVIS</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 30%,#0b2545,#020a1a 70%);
color:#e3f4ff;font:16px system-ui,-apple-system,sans-serif;padding:16px}
form{width:min(360px,100%);text-align:center}h1{letter-spacing:.4em;color:#5cc8ff;text-shadow:0 0 18px #5cc8ff88;font-size:24px;margin:0 0 24px}
input{width:100%;padding:14px;border-radius:12px;border:1px solid #5cc8ff44;background:#06142b;color:#fff;font-size:18px;text-align:center}
button{margin-top:12px;width:100%;padding:14px;border:0;border-radius:12px;background:linear-gradient(135deg,#5cc8ff,#2d7dff);color:#021;font-weight:700;font-size:16px}
p{color:#ff6b81;min-height:20px}</style></head><body><form method="post" action="/login">
<h1>J.A.R.V.I.S</h1><input name="p" type="password" placeholder="Şifrə" autofocus autocomplete="current-password">
<button>Daxil ol</button><p>__ERR__</p></form></body></html>"""
