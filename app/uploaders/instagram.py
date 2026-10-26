"""Instagram Reels paylaşımı (Instagram API with Instagram Login, Facebook səhifəsi lazım deyil).

Video ictimai HTTPS linkdən götürülür (bizim serverin /media ünvanı).
"""
import time

import httpx

import config
import db


def _token() -> str:
    return db.kv_get("ig_token") or config.INSTAGRAM_ACCESS_TOKEN


def configured() -> bool:
    return bool(config.INSTAGRAM_USER_ID and _token())


def _url(path: str) -> str:
    return f"{config.INSTAGRAM_GRAPH_HOST}/{config.INSTAGRAM_GRAPH_VERSION}/{path}"


def refresh_token_if_needed() -> None:
    """Uzunmüddətli token 60 gün yaşayır; hər 7 gündən bir yeniləyirik."""
    tok = _token()
    if not tok:
        return
    last = db.kv_get("ig_token_refreshed", 0)
    if time.time() - last < 7 * 86400:
        return
    try:
        r = httpx.get(f"{config.INSTAGRAM_GRAPH_HOST}/refresh_access_token",
                      params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=30)
        if r.status_code == 200 and r.json().get("access_token"):
            db.kv_set("ig_token", r.json()["access_token"])
            db.kv_set("ig_token_refreshed", time.time())
    except httpx.HTTPError:
        pass


def publish_reel(video_url: str, caption: str, progress=None) -> str:
    tok = _token()
    uid = config.INSTAGRAM_USER_ID
    r = httpx.post(_url(f"{uid}/media"), data={
        "media_type": "REELS", "video_url": video_url, "caption": caption or "",
        "share_to_feed": "true", "access_token": tok,
    }, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram konteyner xətası: {r.text[:400]}")
    cid = r.json()["id"]
    # Instagram videonu özü yükləyib emal edir — hazır olana qədər gözləyirik
    for i in range(90):
        time.sleep(10)
        s = httpx.get(_url(cid), params={"fields": "status_code,status", "access_token": tok}, timeout=30).json()
        code = s.get("status_code")
        if progress:
            progress(min(0.95, (i + 1) / 30))
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram videonu emal edə bilmədi: {s.get('status')}")
    else:
        raise RuntimeError("Instagram emalı 15 dəqiqədə bitmədi")
    p = httpx.post(_url(f"{uid}/media_publish"), data={"creation_id": cid, "access_token": tok}, timeout=60)
    if p.status_code >= 400:
        raise RuntimeError(f"Instagram paylaşım xətası: {p.text[:400]}")
    mid = p.json()["id"]
    link = httpx.get(_url(mid), params={"fields": "permalink", "access_token": tok}, timeout=30).json()
    return link.get("permalink") or f"https://www.instagram.com/ (media id {mid})"
