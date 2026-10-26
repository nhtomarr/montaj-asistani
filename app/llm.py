"""Claude API ilə JSON cavab alan kiçik köməkçi."""
import json
import re

import httpx

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL


class LLMError(RuntimeError):
    pass


def _extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise LLMError(f"JSON tapılmadı: {text[:200]}")
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(text[start:])
    return obj


def ask_json(system: str, user: str, max_tokens: int = 4000, retries: int = 2):
    if not ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY təyin edilməyib")
    last = None
    for _ in range(retries + 1):
        try:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": max_tokens,
                    "system": system + "\n\nCavabı YALNIZ etibarlı JSON kimi qaytar, başqa mətn yazma.",
                    "messages": [{"role": "user", "content": user}],
                },
                timeout=300,
            )
            if r.status_code >= 400:
                raise LLMError(f"Claude API {r.status_code}: {r.text[:300]}")
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return _extract_json(text)
        except (LLMError, httpx.HTTPError, ValueError) as e:
            last = e
    raise LLMError(str(last))
