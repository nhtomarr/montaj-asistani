"""Reklam kabinetləri: Meta (Facebook/Instagram), Google Ads, TikTok Ads.

Ödəniş API ilə mümkün deyil — Jarvis borcu/limiti aşkar edib ödəniş linkini göndərir.
Pul xərcləyən əməliyyatlar (aktivləşdirmə, büdcə, kopyalama) sahibin təsdiqi ilə icra olunur.
"""
import json
import os
from datetime import date, timedelta

import httpx

from jarvis import google_api as g

META_V = os.getenv("META_GRAPH_VERSION", "v23.0")
GADS_V = os.getenv("GOOGLE_ADS_API_VERSION", "v21")
TT = "https://business-api.tiktok.com/open_api/v1.3"

META_STATUS = {1: "AKTİV", 2: "BLOKLANIB", 3: "ÖDƏNİLMƏMİŞ BORC", 7: "RİSK YOXLAMASI", 8: "ÖDƏNİŞ GÖZLƏNİLİR",
               9: "GÜZƏŞT DÖVRÜ (borc var)", 100: "BAĞLANIR", 101: "BAĞLI"}


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def platforms() -> dict:
    return {"meta": bool(os.getenv("META_ADS_TOKEN")),
            "google": bool(os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") and _list("GOOGLE_ADS_CUSTOMER_IDS") and g.configured()),
            "tiktok": bool(os.getenv("TIKTOK_ACCESS_TOKEN") and _list("TIKTOK_ADVERTISER_IDS"))}


def _period(p: str) -> tuple[str, str]:
    t = date.today()
    return {"today": (t, t), "yesterday": (t - timedelta(1), t - timedelta(1)),
            "last_7d": (t - timedelta(7), t - timedelta(1)), "last_30d": (t - timedelta(30), t - timedelta(1))
            }.get(p, (t - timedelta(7), t - timedelta(1)))


# ======================= META
def _meta(method: str, path: str, **params):
    tok = os.getenv("META_ADS_TOKEN", "")
    if not tok:
        raise RuntimeError("Meta reklam tokeni qoşulmayıb (setup səhifəsi → META_ADS_TOKEN)")
    url = f"https://graph.facebook.com/{META_V}/{path}"
    params["access_token"] = tok
    r = httpx.request(method, url, params=params if method == "GET" else None,
                      data=params if method != "GET" else None, timeout=60)
    j = r.json()
    if r.status_code >= 400 or "error" in j:
        raise RuntimeError(f"Meta API: {j.get('error', {}).get('message', r.text[:300])}")
    return j


def meta_accounts() -> list[dict]:
    wanted = set(_list("META_AD_ACCOUNTS"))
    j = _meta("GET", "me/adaccounts", fields="id,name,account_status,disable_reason,currency,balance,amount_spent,"
                                              "spend_cap,business_name", limit=100)
    out = []
    for a in j.get("data", []):
        if wanted and a["id"] not in wanted and a["id"].replace("act_", "") not in wanted:
            continue
        cur = a.get("currency", "")
        st = int(a.get("account_status", 0))
        out.append({"platform": "meta", "id": a["id"], "name": a.get("name"), "status": META_STATUS.get(st, str(st)),
                    "status_code": st, "currency": cur,
                    "borc": round(int(a.get("balance") or 0) / 100, 2),
                    "ümumi_xərc": round(int(a.get("amount_spent") or 0) / 100, 2),
                    "limit": round(int(a["spend_cap"]) / 100, 2) if a.get("spend_cap") not in (None, "0") else None,
                    "ödəniş_linki": f"https://business.facebook.com/billing_hub/payment_settings/?asset_id={a['id'].replace('act_', '')}"})
    return out


def meta_report(account_id: str, period: str = "last_7d") -> list[dict]:
    since, until = _period(period)
    j = _meta("GET", f"{account_id}/insights", level="campaign", time_range=json.dumps(
        {"since": str(since), "until": str(until)}),
        fields="campaign_id,campaign_name,spend,impressions,reach,clicks,ctr,cpc,cpm,actions,cost_per_action_type", limit=100)
    out = []
    for r in j.get("data", []):
        acts = {a["action_type"]: a["value"] for a in r.get("actions", [])}
        cpa = {a["action_type"]: a["value"] for a in r.get("cost_per_action_type", [])}
        key = next((k for k in ("lead", "onsite_conversion.messaging_conversation_started_7d", "purchase",
                                "link_click") if k in acts), None)
        out.append({"campaign_id": r["campaign_id"], "ad": r["campaign_name"], "xərc": r.get("spend"),
                    "göstərilmə": r.get("impressions"), "əhatə": r.get("reach"), "klik": r.get("clicks"),
                    "ctr": r.get("ctr"), "cpc": r.get("cpc"), "nəticə_növü": key,
                    "nəticə": acts.get(key) if key else None, "nəticə_qiyməti": cpa.get(key) if key else None})
    return out


def meta_campaigns(account_id: str) -> list[dict]:
    j = _meta("GET", f"{account_id}/campaigns", fields="id,name,status,effective_status,objective,daily_budget,"
                                                      "lifetime_budget", limit=100)
    return [{"id": c["id"], "ad": c["name"], "status": c.get("effective_status"), "məqsəd": c.get("objective"),
             "gündəlik_büdcə": round(int(c["daily_budget"]) / 100, 2) if c.get("daily_budget") else None}
            for c in j.get("data", [])]


# ======================= GOOGLE ADS
def _gads(cid: str, path: str, body: dict):
    cid = cid.replace("-", "")
    h = {"Authorization": f"Bearer {g.access_token()}", "developer-token": os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
         "content-type": "application/json"}
    login = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", "")
    if login:
        h["login-customer-id"] = login
    r = httpx.post(f"https://googleads.googleapis.com/{GADS_V}/customers/{cid}/{path}", headers=h, json=body, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"Google Ads API {r.status_code}: {r.text[:400]}")
    return r.json()


def gads_report(cid: str, period: str = "last_7d") -> list[dict]:
    during = {"today": "TODAY", "yesterday": "YESTERDAY", "last_7d": "LAST_7_DAYS", "last_30d": "LAST_30_DAYS"}.get(
        period, "LAST_7_DAYS")
    q = ("SELECT campaign.id, campaign.name, campaign.status, campaign_budget.amount_micros, metrics.cost_micros, "
         "metrics.impressions, metrics.clicks, metrics.conversions, metrics.ctr, metrics.average_cpc "
         f"FROM campaign WHERE segments.date DURING {during} AND campaign.status != 'REMOVED'")
    rows = _gads(cid, "googleAds:search", {"query": q}).get("results", [])
    out = []
    for r in rows:
        m, c = r.get("metrics", {}), r.get("campaign", {})
        cost = int(m.get("costMicros", 0)) / 1e6
        conv = float(m.get("conversions", 0) or 0)
        out.append({"campaign_id": c.get("id"), "ad": c.get("name"), "status": c.get("status"),
                    "gündəlik_büdcə": int(r.get("campaignBudget", {}).get("amountMicros", 0)) / 1e6,
                    "xərc": round(cost, 2), "göstərilmə": m.get("impressions"), "klik": m.get("clicks"),
                    "konversiya": conv, "konversiya_qiyməti": round(cost / conv, 2) if conv else None})
    return out


def gads_accounts() -> list[dict]:
    out = []
    for cid in _list("GOOGLE_ADS_CUSTOMER_IDS"):
        try:
            r = _gads(cid, "googleAds:search", {"query": "SELECT customer.id, customer.descriptive_name, "
                                                         "customer.currency_code, customer.status FROM customer"})
            c = r.get("results", [{}])[0].get("customer", {})
            out.append({"platform": "google", "id": cid, "name": c.get("descriptiveName"), "status": c.get("status"),
                        "currency": c.get("currencyCode"),
                        "ödəniş_linki": f"https://ads.google.com/aw/billing/summary?ocid={cid.replace('-', '')}"})
        except Exception as e:  # noqa: BLE001
            out.append({"platform": "google", "id": cid, "xəta": str(e)[:200]})
    return out


# ======================= TIKTOK
def _tt(method: str, path: str, **kw):
    h = {"Access-Token": os.getenv("TIKTOK_ACCESS_TOKEN", ""), "content-type": "application/json"}
    if method == "GET":
        params = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in kw.items()}
        r = httpx.get(f"{TT}/{path}", headers=h, params=params, timeout=60)
    else:
        r = httpx.post(f"{TT}/{path}", headers=h, json=kw, timeout=60)
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"TikTok API: {j.get('message')}")
    return j.get("data", {})


def tt_accounts() -> list[dict]:
    ids = _list("TIKTOK_ADVERTISER_IDS")
    if not ids:
        return []
    d = _tt("GET", "advertiser/info/", advertiser_ids=ids, fields=["advertiser_id", "name", "status", "balance", "currency"])
    return [{"platform": "tiktok", "id": a.get("advertiser_id"), "name": a.get("name"), "status": a.get("status"),
             "balans": a.get("balance"), "currency": a.get("currency"),
             "ödəniş_linki": f"https://ads.tiktok.com/i18n/account/payment?aadvid={a.get('advertiser_id')}"}
            for a in d.get("list", [])]


def tt_report(adv: str, period: str = "last_7d") -> list[dict]:
    since, until = _period(period)
    d = _tt("GET", "report/integrated/get/", advertiser_id=adv, report_type="BASIC", data_level="AUCTION_CAMPAIGN",
            dimensions=["campaign_id"], metrics=["campaign_name", "spend", "impressions", "clicks", "ctr", "cpc",
                                                 "conversion", "cost_per_conversion"],
            start_date=str(since), end_date=str(until), page_size=100)
    return [{"campaign_id": r["dimensions"]["campaign_id"], **r["metrics"]} for r in d.get("list", [])]


# ======================= ümumi
def accounts(platform: str | None = None) -> list[dict]:
    out, on = [], platforms()
    for p, fn in (("meta", meta_accounts), ("google", gads_accounts), ("tiktok", tt_accounts)):
        if (platform in (None, "", p)) and on[p]:
            try:
                out += fn()
            except Exception as e:  # noqa: BLE001
                out.append({"platform": p, "xəta": str(e)[:300]})
    if not any(on.values()):
        return [{"qeyd": "Heç bir reklam kabineti qoşulmayıb. Setup səhifəsində Meta/Google Ads/TikTok açarlarını daxil edin."}]
    return out


def report(platform: str, account_id: str, period: str) -> list[dict]:
    return {"meta": meta_report, "google": gads_report, "tiktok": tt_report}[platform](account_id, period)


def campaigns(platform: str, account_id: str) -> list[dict]:
    if platform == "meta":
        return meta_campaigns(account_id)
    if platform == "google":
        return gads_report(account_id, "last_7d")
    d = _tt("GET", "campaign/get/", advertiser_id=account_id, page_size=100)
    return [{"id": c["campaign_id"], "ad": c["campaign_name"], "status": c.get("operation_status"),
             "büdcə": c.get("budget")} for c in d.get("list", [])]


def execute(kind: str, a: dict) -> str:
    """Təsdiqlənmiş reklam əməliyyatını icra edir."""
    p, acc, cid = a["platform"], a.get("account_id", ""), a["campaign_id"]
    if kind == "ads_status":
        on = a["status"] == "activate"
        if p == "meta":
            _meta("POST", cid, status="ACTIVE" if on else "PAUSED")
        elif p == "google":
            c = acc.replace("-", "")
            _gads(acc, "campaigns:mutate", {"operations": [{"update": {
                "resourceName": f"customers/{c}/campaigns/{cid}", "status": "ENABLED" if on else "PAUSED"},
                "updateMask": "status"}]})
        else:
            _tt("POST", "campaign/status/update/", advertiser_id=acc, campaign_ids=[cid],
                operation_status="ENABLE" if on else "DISABLE")
        return f"Kampaniya {'aktivləşdirildi' if on else 'dayandırıldı'} ({p} {cid})"
    if kind == "ads_budget":
        amt = float(a["daily_budget"])
        if p == "meta":
            _meta("POST", cid, daily_budget=int(round(amt * 100)))
        elif p == "google":
            c = acc.replace("-", "")
            r = _gads(acc, "googleAds:search", {"query": f"SELECT campaign.campaign_budget FROM campaign WHERE campaign.id = {cid}"})
            res = r["results"][0]["campaign"]["campaignBudget"]
            _gads(acc, "campaignBudgets:mutate", {"operations": [{"update": {"resourceName": res,
                  "amountMicros": str(int(amt * 1e6))}, "updateMask": "amount_micros"}]})
            _ = c
        else:
            _tt("POST", "campaign/update/", advertiser_id=acc, campaign_id=cid, budget=amt)
        return f"Gündəlik büdcə {amt} edildi ({p} {cid})"
    if kind == "ads_copy":
        if p != "meta":
            raise RuntimeError("Kopyalama hələlik yalnız Meta üçün")
        r = _meta("POST", f"{cid}/copies", deep_copy="true", status_option="PAUSED")
        return f"Kampaniya kopyalandı (dayandırılmış vəziyyətdə): {r.get('copied_campaign_id')}"
    raise ValueError(kind)


def check_alerts() -> list[str]:
    """Borc, blok, aşağı balans — vəziyyət dəyişəndə xəbərdarlıq üçün."""
    msgs = []
    low = float(os.getenv("ADS_LOW_BALANCE", "20"))
    for a in accounts():
        if "xəta" in a or "qeyd" in a:
            continue
        if a["platform"] == "meta" and a.get("status_code") != 1:
            msgs.append(f"⚠️ Meta «{a['name']}»: {a['status']}. Borc: {a['borc']} {a['currency']}\nÖdəniş: {a['ödəniş_linki']}")
        if a["platform"] == "meta" and a.get("borc", 0) > 0 and a.get("status_code") == 1 and a.get("limit") \
                and a["ümumi_xərc"] >= a["limit"] * 0.9:
            msgs.append(f"⚠️ Meta «{a['name']}»: xərc limitinin 90%-nə çatıb ({a['ümumi_xərc']}/{a['limit']}).")
        if a["platform"] == "tiktok" and a.get("balans") is not None and float(a["balans"]) < low:
            msgs.append(f"⚠️ TikTok «{a['name']}»: balans azdır — {a['balans']} {a['currency']}\nArtır: {a['ödəniş_linki']}")
    return msgs
