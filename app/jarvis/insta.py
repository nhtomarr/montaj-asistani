"""Instagram — qeyri-rəsmi giriş (login/parol, instagrapi). SINAQ ÜÇÜN.

Risk: Instagram qaydalarına ziddir, hesab "şübhəli giriş" yoxlamasına düşə və ya müvəqqəti bloklana bilər.
Qorunma: sessiya yadda saxlanır (hər dəfə yenidən login olunmur), sorğular arası təsadüfi fasilə, keş, gündəlik limit,
yayım və cavablar sahibin təsdiqi ilə.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import config
import db
import notify

IG_DIR = config.DATA_DIR / "instagram"
IG_DIR.mkdir(parents=True, exist_ok=True)
SESSION = IG_DIR / "session.json"
_lock = threading.Lock()
_client = {"cl": None, "at": 0.0}
_cache: dict[str, tuple[float, object]] = {}
DAILY_LIMIT = int(os.getenv("IG_DAILY_LIMIT", "150"))


class IGError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("IG_USERNAME") and os.getenv("IG_PASSWORD"))


def _count() -> None:
    day = time.strftime("%Y-%m-%d")
    st = db.kv_get("ig_calls", {}) or {}
    if st.get("day") != day:
        st = {"day": day, "n": 0}
    st["n"] += 1
    db.kv_set("ig_calls", st)
    if st["n"] > DAILY_LIMIT:
        raise IGError(f"Gündəlik Instagram sorğu limiti ({DAILY_LIMIT}) doldu — hesabı qorumaq üçün sabaha saxlayıram.")


def _wait_code(reason: str) -> str:
    """Instagram kod istəyəndə sahibdən Telegram-da soruşur (/igkod 123456) və 4 dəqiqə gözləyir."""
    db.kv_set("ig_code", None)
    notify.sink.send_text(f"🔐 Instagram təsdiq kodu istəyir ({reason}). SMS/e-poçta gələn kodu Telegram-da belə yazın:\n"
                          f"/igkod 123456")
    for _ in range(240):
        code = db.kv_get("ig_code")
        if code:
            db.kv_set("ig_code", None)
            return str(code).strip()
        time.sleep(1)
    raise IGError("Təsdiq kodu 4 dəqiqə ərzində gəlmədi.")


def client():
    with _lock:
        if _client["cl"] is not None and time.time() - _client["at"] < 6 * 3600:
            return _client["cl"]
        if not configured():
            raise IGError("Instagram qoşulmayıb: /setup səhifəsində IG_USERNAME və IG_PASSWORD yazın.")
        from instagrapi import Client
        from instagrapi.exceptions import ChallengeRequired, LoginRequired, TwoFactorRequired
        cl = Client()
        cl.delay_range = [2, 6]
        cl.set_locale("az_AZ")
        cl.set_country("AZ")
        cl.set_country_code(994)
        cl.set_timezone_offset(4 * 3600)
        if os.getenv("IG_PROXY"):
            cl.set_proxy(os.getenv("IG_PROXY"))
        cl.challenge_code_handler = lambda username, choice: _wait_code(f"təhlükəsizlik yoxlaması, {choice}")
        user, pw = os.getenv("IG_USERNAME"), os.getenv("IG_PASSWORD")
        if SESSION.exists():
            try:
                cl.load_settings(SESSION)
                cl.login(user, pw)
                cl.get_timeline_feed()  # sessiya hələ etibarlıdırmı
            except (LoginRequired, Exception):  # noqa: BLE001
                old = cl.get_settings()
                cl = Client()
                cl.delay_range = [2, 6]
                cl.set_uuids(old.get("uuids", {}))  # eyni "cihaz" kimi görünsün
                cl.challenge_code_handler = lambda username, choice: _wait_code(f"yoxlama, {choice}")
                if os.getenv("IG_PROXY"):
                    cl.set_proxy(os.getenv("IG_PROXY"))
                _login(cl, user, pw, TwoFactorRequired, ChallengeRequired)
        else:
            _login(cl, user, pw, TwoFactorRequired, ChallengeRequired)
        cl.dump_settings(SESSION)
        _client.update(cl=cl, at=time.time())
        return cl


def _login(cl, user, pw, TwoFactorRequired, ChallengeRequired):
    seed = os.getenv("IG_2FA_SEED", "").replace(" ", "")
    try:
        cl.login(user, pw, verification_code=cl.totp_generate_code(seed) if seed else "")
    except TwoFactorRequired:
        cl.login(user, pw, verification_code=_wait_code("iki addımlı giriş"))
    except ChallengeRequired as e:
        raise IGError(f"Instagram yoxlama tələb etdi və keçmədi: {e}. Telefonda Instagram-ı açın, 'Bu mən idim' deyin, "
                      "sonra yenidən cəhd edin.") from e


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    _count()
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _m(m) -> dict:
    return {"media_id": m.id, "kod": m.code, "link": f"https://www.instagram.com/p/{m.code}/",
            "növ": "reels" if getattr(m, "product_type", "") == "clips" else
            {1: "foto", 2: "video", 8: "karusel"}.get(m.media_type, str(m.media_type)),
            "tarix": m.taken_at.strftime("%Y-%m-%d") if m.taken_at else None,
            "bəyənmə": m.like_count, "şərh": m.comment_count,
            "baxış": getattr(m, "play_count", None) or getattr(m, "view_count", None) or None,
            "mətn": (m.caption_text or "")[:300]}


# ---------------------------------------------------------------- əməliyyatlar
def overview(n: int = 12) -> dict:
    cl = client()
    me = _cached("me", 900, lambda: cl.account_info())
    info = _cached("myinfo", 900, lambda: cl.user_info(cl.user_id))
    medias = _cached(f"my{n}", 900, lambda: cl.user_medias(cl.user_id, amount=n))
    posts = [_m(m) for m in medias]
    return {"hesab": me.username, "ad": me.full_name, "bio": me.biography, "izləyici": info.follower_count,
            "izlənən": info.following_count, "post_sayı": info.media_count, "son_postlar": posts}


def comments(posts: int = 5, per_post: int = 30) -> list[dict]:
    cl = client()
    medias = _cached(f"my{posts}", 600, lambda: cl.user_medias(cl.user_id, amount=posts))
    me = cl.username
    out = []
    for m in medias:
        cs = _cached(f"c{m.id}", 300, lambda m=m: cl.media_comments(m.id, amount=per_post))
        replied = {getattr(c, "replied_to_comment_id", None) for c in cs if c.user.username == me}
        for c in cs:
            if c.user.username == me:
                continue
            out.append({"media_id": m.id, "post": (m.caption_text or "")[:60], "comment_id": c.pk,
                        "kim": c.user.username, "mətn": c.text, "tarix": str(c.created_at_utc)[:16],
                        "cavablanıb": c.pk in replied})
    return out


def reply(media_id: str, comment_id: str, text: str) -> dict:
    cl = client()
    _count()
    c = cl.media_comment(media_id, text, replied_to_comment_id=int(comment_id))
    _cache.pop(f"c{media_id}", None)
    return {"ok": True, "cavab_id": c.pk}


def competitor(username: str, n: int = 24) -> dict:
    cl = client()
    username = username.strip().lstrip("@").split("/")[-1] or username
    uid = _cached(f"uid:{username}", 86400, lambda: cl.user_id_from_username(username))
    info = _cached(f"ui:{username}", 3600, lambda: cl.user_info(uid))
    medias = _cached(f"um:{username}:{n}", 3600, lambda: cl.user_medias(uid, amount=n))
    posts = [_m(m) for m in medias]
    key = lambda p: (p["baxış"] or 0) * 0.05 + (p["bəyənmə"] or 0) + 3 * (p["şərh"] or 0)  # noqa: E731
    top = sorted(posts, key=key, reverse=True)
    return {"hesab": username, "izləyici": info.follower_count, "post_sayı": info.media_count,
            "ən_yaxşı": top[:8], "ən_zəif": top[-3:], "analiz_edilən": len(posts)}


def publish(kind: str, path: str, caption: str) -> dict:
    cl = client()
    _count()
    p = Path(path)
    if not p.exists():
        raise IGError(f"Fayl tapılmadı: {path}")
    if kind == "reel":
        m = cl.clip_upload(p, caption)
    else:
        m = cl.photo_upload(p, caption)
    return {"ok": True, "link": f"https://www.instagram.com/p/{m.code}/"}


def status() -> dict:
    st = db.kv_get("ig_calls", {}) or {}
    return {"qoşulub": configured(), "sessiya": SESSION.exists(), "bu_gün_sorğu": st.get("n", 0),
            "limit": DAILY_LIMIT, "proxy": bool(os.getenv("IG_PROXY"))}
