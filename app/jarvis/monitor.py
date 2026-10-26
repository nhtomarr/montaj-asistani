"""Sayt və server nəzarəti: əlçatanlıq, cavab vaxtı, SSL və domen bitmə tarixi."""
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

import db


def targets() -> list[str]:
    env = [x.strip() for x in os.getenv("MONITOR_URLS", "").split(",") if x.strip()]
    return sorted(set(env + (db.kv_get("monitor_urls", []) or [])))


def add(url: str) -> list[str]:
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    lst = db.kv_get("monitor_urls", []) or []
    if url not in lst:
        lst.append(url)
    db.kv_set("monitor_urls", lst)
    return targets()


def remove(url: str) -> list[str]:
    lst = [u for u in (db.kv_get("monitor_urls", []) or []) if url not in u]
    db.kv_set("monitor_urls", lst)
    return targets()


def ssl_days(host: str) -> int | None:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host) as ss:
            exp = datetime.strptime(ss.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return (exp - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001
        return None


def domain_days(host: str) -> int | None:
    parts = host.split(".")
    dom = ".".join(parts[-3:] if parts[-2] in ("com", "net", "org", "gov", "edu") and parts[-1] == "az" else parts[-2:])
    try:
        r = httpx.get(f"https://rdap.org/domain/{dom}", timeout=20, follow_redirects=True)
        for ev in r.json().get("events", []):
            if ev.get("eventAction") == "expiration":
                exp = datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
                return (exp - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001
        pass
    return None


def check(url: str, deep: bool = False) -> dict:
    t0 = time.time()
    res = {"url": url}
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "JarvisMonitor/1.0"})
        res.update(up=r.status_code < 500, code=r.status_code, ms=int((time.time() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        res.update(up=False, code=None, error=str(e)[:120], ms=None)
    if deep:
        host = urlparse(url).hostname or ""
        res["ssl_gün"] = ssl_days(host)
        res["domen_gün"] = domain_days(host)
    return res


def run_checks(deep: bool = False) -> list[str]:
    """Bütün hədəfləri yoxlayır; YALNIZ vəziyyət dəyişəndə xəbərdarlıq mətnləri qaytarır."""
    state = db.kv_get("monitor_state", {}) or {}
    alerts = []
    for url in targets():
        r = check(url, deep)
        prev = state.get(url, {})
        if prev.get("up", True) and not r["up"]:
            alerts.append(f"🔴 SAYT ÇÖKÜB: {url}\n{r.get('code') or r.get('error')}")
        elif not prev.get("up", True) and r["up"]:
            alerts.append(f"🟢 Sayt bərpa olundu: {url} ({r['ms']} ms)")
        elif r["up"] and r.get("ms") and r["ms"] > 8000 and (prev.get("ms") or 0) <= 8000:
            alerts.append(f"🟡 Sayt çox yavaşdır: {url} — {r['ms']} ms")
        if deep:
            for key, lim, name in (("ssl_gün", 14, "SSL sertifikatı"), ("domen_gün", 30, "Domen")):
                d = r.get(key)
                if d is not None and d <= lim and prev.get(f"warned_{key}") != d // 7:
                    alerts.append(f"⚠️ {name} {d} gün sonra bitir: {url}")
                    r[f"warned_{key}"] = d // 7
                elif prev.get(f"warned_{key}") is not None and (d is None or d > lim):
                    r[f"warned_{key}"] = None
                else:
                    r[f"warned_{key}"] = prev.get(f"warned_{key}")
        r["checked"] = time.time()
        state[url] = {**prev, **r}
    db.kv_set("monitor_state", state)
    return alerts


def status() -> list[dict]:
    st = db.kv_get("monitor_state", {}) or {}
    return [{"url": u, **{k: v for k, v in st.get(u, {}).items() if not k.startswith("warned")}} for u in targets()]
