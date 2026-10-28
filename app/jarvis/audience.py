"""Sintetik auditoriya testi: reklamı/məhsulu/saytı yüzlərlə virtual müştəriyə göstərib reaksiyalarını toplayır.

Proqnoz deyil — erkən səhvləri, etirazları və zəif yerləri tez tutmaq üçün "fokus qrup" simulyasiyasıdır.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import json
import os
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import httpx

import config
from llm import ask_json

FAST = os.getenv("JARVIS_FAST_MODEL", "claude-haiku-5-5")

CITIES = [("Bakı — mərkəz", 22), ("Bakı — Nəsimi/Yasamal", 14), ("Bakı — Xətai/Nərimanov", 12),
          ("Bakı — qəsəbələr", 12), ("Sumqayıt", 8), ("Gəncə", 7), ("Xırdalan/Abşeron", 7), ("Lənkəran", 4),
          ("Şəki/Qəbələ", 4), ("Quba/Qusar", 4), ("Mingəçevir", 3), ("digər rayon", 3)]
JOBS = ["dövlət qulluqçusu", "bank işçisi", "müəllim", "həkim", "İT mütəxəssisi", "satış meneceri", "kiçik biznes sahibi",
        "taksi sürücüsü", "inşaat ustası", "tələbə", "evdar qadın", "mühasib", "marketoloq", "neft-qaz mühəndisi",
        "restoran işçisi", "sahibkar", "təqaüdçü", "freelancer dizayner", "polis/hərbçi", "tibb bacısı"]
INTERESTS = ["avtomobillər", "futbol", "ailə və uşaqlar", "səyahət", "moda", "texnologiya", "yemək", "idman/fitness",
             "daşınmaz əmlak", "dini/ənənəvi dəyərlər", "biznes", "musiqi", "gözəllik", "investisiya", "ev dizaynı",
             "oyunlar", "kitab", "bağçılıq"]
TRAITS = ["qiymətə çox həssas", "brendə önəm verir", "şübhəçi, rəyləri oxuyur", "impulsiv alıcı", "keyfiyyət axtarır",
          "dostların tövsiyəsinə inanır", "tələsir, mətn oxumur", "detalları sevir", "endirim ovçusu",
          "yerli məhsula üstünlük verir", "xarici brendlərə güvənir", "kredit/hissə-hissə ödənişlə alır"]


def _pick(rng, pairs):
    total = sum(w for _, w in pairs)
    r = rng.uniform(0, total)
    for v, w in pairs:
        r -= w
        if r <= 0:
            return v
    return pairs[-1][0]


def _target_spec(target: str) -> dict:
    if not target.strip():
        return {}
    try:
        return ask_json(
            "Hədəf auditoriya təsvirini JSON məhdudiyyətlərə çevir: age_min, age_max, gender ('qadın'|'kişi'|null), "
            "income_min, income_max (AZN/ay, şəxsi), cities (siyahı və ya null — 'Bakı', 'Sumqayıt', 'Gəncə', 'rayon'), "
            "interests (siyahı və ya null), notes (qısa mətn: digər xüsusiyyətlər). Bilinmirsə null.",
            target, max_tokens=400, retries=1, model=FAST) or {}
    except Exception:  # noqa: BLE001
        return {}


def personas(n: int, target: str = "", seed: int = 7) -> tuple[list[dict], dict]:
    spec = _target_spec(target)
    rng = random.Random(seed)
    a0, a1 = int(spec.get("age_min") or 18), int(spec.get("age_max") or 60)
    i0, i1 = int(spec.get("income_min") or 300), int(spec.get("income_max") or 4000)
    cities = [c for c in CITIES if not spec.get("cities") or any(
        str(x).lower()[:4] in c[0].lower() or (str(x).lower().startswith("ray") and "Bakı" not in c[0])
        for x in spec["cities"])] or CITIES
    out = []
    for i in range(n):
        age = rng.randint(a0, max(a0, a1))
        gender = spec.get("gender") or rng.choice(["qadın", "kişi"])
        inc = int(rng.triangular(i0, max(i0 + 1, i1), i0 + (i1 - i0) * .3) // 50 * 50)
        fam = rng.choice(["subay", "evli, uşaqsız", "evli, 1–2 uşaq", "evli, 3+ uşaq"] if age > 24
                         else ["subay", "subay", "nişanlı"])
        ints = list(spec.get("interests") or []) + rng.sample(INTERESTS, 2)
        out.append({"id": i + 1, "yaş": age, "cins": gender, "şəhər": _pick(rng, cities), "gəlir_AZN": inc,
                    "iş": rng.choice(JOBS if age < 63 else ["təqaüdçü"]), "ailə": fam, "maraq": ints[:3],
                    "xarakter": rng.sample(TRAITS, 2), "instagram_saat": round(rng.uniform(.3, 4), 1)})
    return out, spec


EVAL_SYSTEM = """Sən Azərbaycanda yaşayan real insanları simulyasiya edirsən. Sənə bir reklam/məhsul/sayt və virtual
müştərilərin siyahısı verilir. HƏR müştəri üçün ayrıca, ONUN gözü ilə (yaşı, gəliri, şəhəri, xarakteri) dürüst qərar ver —
hamını müsbət etmə; real həyatda insanların çoxu reklamı keçir. Gəlirə uyğun olmayan qiymət → almır.
Reaksiya: "keçdi" (sürüşdürüb keçdi), "baxdı" (diqqət etdi, amma klikləmədi), "klik" (ətraflı baxmaq istədi),
"alar" (yaxın vaxtda almağa/müraciətə hazırdır). maraq: 0–10. səbəb: 4–10 söz, onun dilində. etiraz: ən böyük maneə
(yoxdursa null).
JSON: {"r":[{"id":1,"variant":"A","reaksiya":"...","maraq":0,"səbəb":"...","etiraz":"..."}]}"""

LEVEL = {"keçdi": 0, "baxdı": 1, "klik": 2, "alar": 3}


def _content_blocks(variants: dict[str, str], images: list[Path], url_text: str) -> list[dict]:
    blocks = []
    for p in images[:4]:
        try:
            data = Path(p).read_bytes()
            mt = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                       "data": base64.b64encode(data).decode()}})
        except OSError:
            pass
    txt = ""
    for k, v in variants.items():
        txt += f"\n=== VARİANT {k} ===\n{v}\n"
    if url_text:
        txt += f"\n=== SAYT / MƏHSUL SƏHİFƏSİ ===\n{url_text[:6000]}\n"
    blocks.append({"type": "text", "text": txt or "(yalnız şəkil)"})
    return blocks


def _call(system: str, content: list[dict], max_tokens: int = 4000) -> dict:
    r = httpx.post("https://api.anthropic.com/v1/messages",
                   headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                            "content-type": "application/json"},
                   json={"model": FAST, "max_tokens": max_tokens,
                         "system": system + "\nCavabı YALNIZ etibarlı JSON kimi qaytar.",
                         "messages": [{"role": "user", "content": content}]}, timeout=180)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def _fetch(url: str) -> str:
    try:
        r = httpx.get(url, timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", r.text)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", t).strip()
    except Exception:  # noqa: BLE001
        return ""


def run(content: str = "", target: str = "", variants: dict | None = None, url: str = "",
        images: list[str] | None = None, n: int = 200) -> dict:
    n = max(20, min(int(n or 200), 400))
    variants = {k: str(v) for k, v in (variants or {}).items() if str(v).strip()} or ({"A": content} if content else {})
    imgs = [Path(p) for p in (images or []) if Path(p).exists()]
    url_text = _fetch(url) if url else ""
    if not variants and not imgs and not url_text:
        return {"ok": False, "xəta": "Test üçün mətn, şəkil və ya link lazımdır."}
    if not variants:
        variants = {"A": "(reklam yuxarıdakı şəkildədir)" if imgs else "(sayt)"}
    people, spec = personas(n, target)
    base = _content_blocks(variants, imgs, url_text)
    keys = list(variants)
    batches = [people[i:i + 20] for i in range(0, len(people), 20)]

    def job(batch):
        # A/B: hər insan bütün variantları görür (müqayisə ədalətli olsun)
        q = (f"Variantlar: {', '.join(keys)}. Hər müştəri hər variant üçün ayrıca qərar versin.\n"
             f"Müştərilər:\n{json.dumps(batch, ensure_ascii=False)}")
        try:
            return _call(EVAL_SYSTEM, base + [{"type": "text", "text": q}]).get("r", [])
        except Exception as e:  # noqa: BLE001
            print("audience batch:", e)
            return []

    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        rows = [r for part in ex.map(job, batches) for r in part]
    if not rows:
        return {"ok": False, "xəta": "Simulyasiya alınmadı (Claude cavab vermədi)."}
    byid = {p["id"]: p for p in people}
    stats = {}
    for k in keys:
        rs = [r for r in rows if str(r.get("variant", "A")).upper() == k and r.get("id") in byid]
        if not rs:
            continue
        cnt = Counter(r.get("reaksiya", "keçdi") for r in rs)
        seg = defaultdict(lambda: [0, 0])
        for r in rs:
            p = byid[r["id"]]
            hit = LEVEL.get(r.get("reaksiya"), 0) >= 2
            for key in (f"yaş {(p['yaş'] // 10) * 10}–{(p['yaş'] // 10) * 10 + 9}",
                        "gəlir <800" if p["gəlir_AZN"] < 800 else "gəlir 800–2000" if p["gəlir_AZN"] < 2000
                        else "gəlir 2000+", p["cins"], p["şəhər"].split(" — ")[0]):
                seg[key][0] += hit
                seg[key][1] += 1
        stats[k] = {
            "n": len(rs),
            "klik_%": round(100 * (cnt["klik"] + cnt["alar"]) / len(rs)),
            "alar_%": round(100 * cnt["alar"] / len(rs)),
            "keçdi_%": round(100 * cnt["keçdi"] / len(rs)),
            "orta_maraq": round(sum(float(r.get("maraq") or 0) for r in rs) / len(rs), 1),
            "seqmentlər": {s: f"{round(100 * a / b)}% ({b})" for s, (a, b) in sorted(seg.items()) if b >= 8},
            "etirazlar": [e for e, _ in Counter(str(r.get("etiraz")) for r in rs
                                                if r.get("etiraz") not in (None, "null", "")).most_common(12)],
            "müsbət": [r.get("səbəb") for r in rs if LEVEL.get(r.get("reaksiya"), 0) >= 2][:12],
        }
    summary = {}
    try:
        summary = ask_json(
            "Sən marketinq strateqisən. Sintetik auditoriya testinin nəticələrinə əsasən Azərbaycan dilində qısa "
            "hesabat ver: qalib variant (varsa) və niyə; 3 əsas etiraz (oxşarları birləşdir); hansı seqment ən çox / ən "
            "az maraqlandı; 3 konkret təkmilləşdirmə; düzəldilmiş reklam mətni (1 variant, platformaya uyğun qısa). "
            'JSON: {"qalib":"...","etirazlar":["..."],"seqmentlər":"...","tövsiyələr":["..."],"yeni_mətn":"..."}',
            json.dumps({"variantlar": variants, "hədəf": target, "statistika": stats}, ensure_ascii=False)[:12000],
            max_tokens=1500, retries=1)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "virtual_müştəri": n, "hədəf": target or "ümumi Azərbaycan auditoriyası",
            "nəticə": stats, "hesabat": summary,
            "qeyd": "Bu, simulyasiyadır — real satışı proqnozlaşdırmır; etirazları və zəif yerləri tez tapmaq üçündür."}


def report_text(res: dict) -> str:
    if not res.get("ok"):
        return "❌ " + res.get("xəta", "alınmadı")
    lines = [f"👥 Sintetik auditoriya testi — {res['virtual_müştəri']} virtual müştəri", f"Hədəf: {res['hədəf']}", ""]
    for k, s in res["nəticə"].items():
        lines.append(f"Variant {k}: klik {s['klik_%']}% · alar {s['alar_%']}% · keçdi {s['keçdi_%']}% · "
                     f"maraq {s['orta_maraq']}/10")
    h = res.get("hesabat") or {}
    if h.get("qalib"):
        lines += ["", f"🏆 {h['qalib']}"]
    if h.get("etirazlar"):
        lines += ["", "Əsas etirazlar:"] + [f"• {x}" for x in h["etirazlar"][:3]]
    if h.get("seqmentlər"):
        lines += ["", f"Seqmentlər: {h['seqmentlər']}"]
    if h.get("tövsiyələr"):
        lines += ["", "Tövsiyələr:"] + [f"{i + 1}. {x}" for i, x in enumerate(h["tövsiyələr"][:3])]
    if h.get("yeni_mətn"):
        lines += ["", "✍️ Təklif olunan mətn:", h["yeni_mətn"]]
    lines += ["", "ℹ️ " + res["qeyd"]]
    return "\n".join(lines)
