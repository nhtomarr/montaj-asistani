"""Ağıllı ev: Home Assistant REST API (Xiaomi Home və digər cihazlar)."""
import os

import httpx

DOMAINS = ("vacuum", "light", "switch", "climate", "fan", "cover", "media_player", "lock", "humidifier",
           "sensor", "binary_sensor", "button", "select", "number", "scene", "script")


def _base() -> str:
    return os.getenv("HA_URL", "http://homeassistant:8123").rstrip("/")


def _h() -> dict:
    tok = os.getenv("HA_TOKEN", "")
    if not tok:
        raise RuntimeError("Ağıllı ev qoşulmayıb: /setup səhifəsində HA_TOKEN yazılmalıdır")
    return {"Authorization": f"Bearer {tok}", "content-type": "application/json"}


def configured() -> bool:
    return bool(os.getenv("HA_TOKEN"))


def devices(query: str = "", domain: str = "") -> list[dict]:
    r = httpx.get(f"{_base()}/api/states", headers=_h(), timeout=20)
    r.raise_for_status()
    out = []
    q = query.lower().strip()
    for s in r.json():
        eid = s["entity_id"]
        dom = eid.split(".")[0]
        if dom not in DOMAINS or (domain and dom != domain):
            continue
        name = s.get("attributes", {}).get("friendly_name", eid)
        if q and q not in name.lower() and q not in eid.lower():
            continue
        attrs = {k: v for k, v in s.get("attributes", {}).items()
                 if k in ("battery_level", "status", "fan_speed", "brightness", "temperature", "current_temperature",
                          "hvac_mode", "unit_of_measurement", "device_class", "options")}
        out.append({"entity_id": eid, "ad": name, "vəziyyət": s["state"], **attrs})
    # sensorlar çox olur — əvvəlcə idarə olunan cihazlar
    out.sort(key=lambda d: (d["entity_id"].split(".")[0] in ("sensor", "binary_sensor"), d["entity_id"]))
    return out[:80]


def action(entity_id: str, service: str, data: dict | None = None) -> dict:
    dom = entity_id.split(".")[0]
    if "." in service:
        dom, service = service.split(".", 1)
    body = {"entity_id": entity_id, **(data or {})}
    r = httpx.post(f"{_base()}/api/services/{dom}/{service}", headers=_h(), json=body, timeout=30)
    if r.status_code >= 400:
        return {"ok": False, "xəta": r.text[:300]}
    st = httpx.get(f"{_base()}/api/states/{entity_id}", headers=_h(), timeout=15)
    return {"ok": True, "yeni_vəziyyət": st.json().get("state") if st.status_code == 200 else None}
