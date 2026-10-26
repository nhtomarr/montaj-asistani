"""Google: Gmail, Calendar, Drive — tək OAuth qoşulması (setup səhifəsindən)."""
import base64
import html as htmlmod
import os
import re
import time
from email.mime.text import MIMEText
from email.utils import parseaddr

import httpx

TZ = "Asia/Baku"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]
_tok = {"value": None, "exp": 0.0}


def _client():
    return os.getenv("YOUTUBE_CLIENT_ID", ""), os.getenv("YOUTUBE_CLIENT_SECRET", "")


def refresh_token() -> str:
    return os.getenv("GOOGLE_REFRESH_TOKEN", "")


def configured() -> bool:
    cid, sec = _client()
    return bool(cid and sec and refresh_token())


def access_token() -> str:
    if _tok["value"] and time.time() < _tok["exp"] - 60:
        return _tok["value"]
    cid, sec = _client()
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": sec, "refresh_token": refresh_token(),
        "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Google token xətası: {r.text[:200]}")
    j = r.json()
    _tok["value"], _tok["exp"] = j["access_token"], time.time() + j.get("expires_in", 3600)
    return _tok["value"]


def _req(method: str, url: str, **kw):
    if not configured():
        raise RuntimeError("Google hesabı qoşulmayıb (setup səhifəsində 'Google hesabını qoş').")
    h = kw.pop("headers", {})
    h["Authorization"] = f"Bearer {access_token()}"
    r = httpx.request(method, url, headers=h, timeout=60, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"Google API {r.status_code}: {r.text[:300]}")
    return r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text


# ================= Gmail
G = "https://gmail.googleapis.com/gmail/v1/users/me"


def _hdr(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def gmail_search(query: str = "in:inbox", max_results: int = 10) -> list[dict]:
    ids = _req("GET", f"{G}/messages", params={"q": query, "maxResults": min(max_results, 25)}).get("messages", [])
    out = []
    for m in ids:
        d = _req("GET", f"{G}/messages/{m['id']}", params={
            "format": "metadata", "metadataHeaders": ["From", "Subject", "Date", "To"]})
        out.append({"id": d["id"], "thread_id": d.get("threadId"), "from": _hdr(d, "From"),
                    "to": _hdr(d, "To"), "subject": _hdr(d, "Subject"), "date": _hdr(d, "Date"),
                    "snippet": htmlmod.unescape(d.get("snippet", "")), "labels": d.get("labelIds", [])})
    return out


def _body(part) -> tuple[str, str]:
    plain, html = "", ""
    mt = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data:
        txt = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
        if mt == "text/plain":
            plain += txt
        elif mt == "text/html":
            html += txt
    for p in part.get("parts", []) or []:
        a, b = _body(p)
        plain += a
        html += b
    return plain, html


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"\n\s*\n+", "\n\n", htmlmod.unescape(h)).strip()


def gmail_read(msg_id: str) -> dict:
    d = _req("GET", f"{G}/messages/{msg_id}", params={"format": "full"})
    plain, html = _body(d.get("payload", {}))
    text = plain.strip() or _strip_html(html)
    atts = []

    def walk(p):
        if p.get("filename"):
            atts.append(p["filename"])
        for c in p.get("parts", []) or []:
            walk(c)
    walk(d.get("payload", {}))
    return {"id": d["id"], "thread_id": d.get("threadId"), "from": _hdr(d, "From"), "to": _hdr(d, "To"),
            "cc": _hdr(d, "Cc"), "subject": _hdr(d, "Subject"), "date": _hdr(d, "Date"),
            "message_id": _hdr(d, "Message-ID"), "body": text[:12000], "attachments": atts,
            "labels": d.get("labelIds", [])}


def gmail_send(to: str, subject: str, body: str, reply_to_id: str | None = None, cc: str | None = None) -> dict:
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    payload = {}
    if reply_to_id:
        orig = gmail_read(reply_to_id)
        if not subject:
            subject = orig["subject"]
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        if orig.get("message_id"):
            msg["In-Reply-To"] = orig["message_id"]
            msg["References"] = orig["message_id"]
        payload["threadId"] = orig["thread_id"]
    msg["Subject"] = subject
    payload["raw"] = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    r = _req("POST", f"{G}/messages/send", json=payload)
    return {"id": r.get("id"), "thread_id": r.get("threadId")}


def gmail_modify(msg_id: str, action: str) -> str:
    acts = {"read": {"removeLabelIds": ["UNREAD"]}, "unread": {"addLabelIds": ["UNREAD"]},
            "archive": {"removeLabelIds": ["INBOX"]}, "star": {"addLabelIds": ["STARRED"]},
            "important": {"addLabelIds": ["IMPORTANT"]}}
    _req("POST", f"{G}/messages/{msg_id}/modify", json=acts[action])
    return "ok"


def is_automated_sender(sender: str) -> bool:
    addr = parseaddr(sender)[1].lower()
    return bool(re.search(r"no-?reply|noreply|notification|mailer-daemon|newsletter|bounce|donotreply", addr))


# ================= Calendar
C = "https://www.googleapis.com/calendar/v3/calendars/primary"


def calendar_list(time_min: str, time_max: str) -> list[dict]:
    j = _req("GET", f"{C}/events", params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
                                            "orderBy": "startTime", "maxResults": 50, "timeZone": TZ})
    out = []
    for e in j.get("items", []):
        out.append({"id": e["id"], "title": e.get("summary", "(adsız)"),
                    "start": e["start"].get("dateTime") or e["start"].get("date"),
                    "end": e["end"].get("dateTime") or e["end"].get("date"),
                    "location": e.get("location", ""), "link": e.get("hangoutLink") or e.get("htmlLink"),
                    "attendees": [a.get("email") for a in e.get("attendees", [])]})
    return out


def calendar_create(title: str, start: str, end: str, attendees: list[str] | None = None,
                    description: str = "", location: str = "", meet: bool = False) -> dict:
    body = {"summary": title, "description": description, "location": location,
            "start": {"dateTime": start, "timeZone": TZ}, "end": {"dateTime": end, "timeZone": TZ},
            "attendees": [{"email": a} for a in (attendees or [])]}
    params = {"sendUpdates": "all" if attendees else "none"}
    if meet:
        body["conferenceData"] = {"createRequest": {"requestId": f"j{int(time.time())}",
                                                    "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
        params["conferenceDataVersion"] = 1
    e = _req("POST", f"{C}/events", params=params, json=body)
    return {"id": e["id"], "link": e.get("htmlLink"), "meet": e.get("hangoutLink")}


def calendar_delete(event_id: str) -> str:
    _req("DELETE", f"{C}/events/{event_id}", params={"sendUpdates": "all"})
    return "ok"


# ================= Drive
D = "https://www.googleapis.com/drive/v3"


def drive_search(query: str, max_results: int = 10) -> list[dict]:
    q = query.replace("'", "\\'")
    j = _req("GET", f"{D}/files", params={
        "q": f"(name contains '{q}' or fullText contains '{q}') and trashed=false",
        "pageSize": max_results, "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
        "orderBy": "modifiedTime desc"})
    return j.get("files", [])


def drive_read(file_id: str) -> str:
    meta = _req("GET", f"{D}/files/{file_id}", params={"fields": "id,name,mimeType"})
    mt = meta["mimeType"]
    if mt == "application/vnd.google-apps.document":
        txt = _req("GET", f"{D}/files/{file_id}/export", params={"mimeType": "text/plain"})
    elif mt == "application/vnd.google-apps.spreadsheet":
        txt = _req("GET", f"{D}/files/{file_id}/export", params={"mimeType": "text/csv"})
    elif mt.startswith("text/") or mt in ("application/json",):
        txt = _req("GET", f"{D}/files/{file_id}", params={"alt": "media"})
    else:
        return f"{meta['name']} ({mt}) — bu fayl növünü mətn kimi oxuya bilmirəm."
    return str(txt)[:15000]


def doc_create(title: str, html_content: str) -> dict:
    boundary = "jarvisboundary"
    meta = '{"name": %s, "mimeType": "application/vnd.google-apps.document"}' % _json_str(title)
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n{html_content}\r\n--{boundary}--")
    j = _req("POST", "https://www.googleapis.com/upload/drive/v3/files",
             params={"uploadType": "multipart", "fields": "id,webViewLink"},
             headers={"Content-Type": f"multipart/related; boundary={boundary}"},
             content=body.encode("utf-8"))
    return {"id": j["id"], "link": j.get("webViewLink")}


def _json_str(s: str) -> str:
    import json
    return json.dumps(s, ensure_ascii=False)
