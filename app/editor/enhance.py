"""Dinamik montaj: danışığa uyğun animasiyalar.

Hazır (kəsilmiş) videonun üzərinə ikinci keçiddə əlavə edir:
  • canlı altyazı — deyilən söz rənglənir və böyüyür
  • açar söz animasiyaları — rəqəm/vacib ifadə ekrana iri, sıçrayan kartla çıxır
  • zoom vurğuları — vacib cümlələrdə yumşaq yaxınlaşma
  • səs effektləri — kart çıxanda "pop", B-roll-da "whoosh" (ElevenLabs ilə yaradılır, keşlənir)
  • B-roll — açar sözə uyğun stok video (Pexels, açar varsa)
Qərarları Claude verir (nə, harada, nə qədər).
"""
import json
import os
import subprocess
from pathlib import Path

import httpx

import config
from editor import media
from editor.transcript import build_utterances
from llm import ask_json

SFX_DIR = config.DATA_DIR / "sfx"
BROLL_DIR = config.DATA_DIR / "broll"
SFX_DIR.mkdir(parents=True, exist_ok=True)
BROLL_DIR.mkdir(parents=True, exist_ok=True)

SFX_PROMPTS = {
    "pop": ("short soft UI pop, bubble click, clean, modern", 0.5),
    "whoosh": ("fast airy whoosh transition, short swoosh", 0.8),
    "ding": ("bright short notification ding, positive chime", 0.7),
}

PLAN_SYSTEM = """Sən sosial media video redaktorusan (Reels/Shorts/YouTube). Sənə montaj olunmuş videonun nitqi verilir:
seqmentlər [id] (başlanğıc–son saniyə) mətn. Videonu canlı və diqqət çəkən etmək üçün effekt planı hazırla.

Qaydalar:
- highlights: ekrana iri animasiyalı yazı. Rəqəmlər, qiymətlər, faizlər, əsas fayda, güclü ifadələr. 2–5 söz, qısa və
  vurucu (məs. "1850 AZN / m²", "20% İLKİN ÖDƏNİŞ", "FAİZSİZ 36 AY"). Hər 5–8 saniyədə ən çox biri. Müddət 1.2–2.5 s.
  Vaxtı ifadənin deyildiyi ana uyğun olsun. "style": "accent" (əsas) və ya "number" (rəqəm/qiymət).
- zooms: vurğulu cümlələrdə yumşaq yaxınlaşma, 1.5–4 s, scale 1.06–1.15. Ümumi müddətin ~20%-i. Üst-üstə düşməsin.
- broll: yalnız konkret vizual təsvir olunan anlar (məkan, obyekt, hadisə) — ingiliscə stok video axtarış sorğusu
  (məs. "family apartment living room", "city skyline night"). 2.5–4 s. Ən çox {max_broll} ədəd. Danışanın üzü vacib
  olan anlarda (giriş, nəticə, çağırış) qoyma.
- sfx: "pop" highlight çıxanda, "whoosh" broll başlayanda, "ding" əsas nəticədə (ən çox 1). Vaxt saniyə ilə.
- Dil: highlight mətnləri videonun dilində.
JSON: {{"highlights":[{{"start":s,"end":s,"text":"...","style":"accent|number"}}],"zooms":[{{"start":s,"end":s,"scale":1.1}}],
"broll":[{{"start":s,"end":s,"query":"..."}}],"sfx":[{{"t":s,"type":"pop|whoosh|ding"}}]}}"""


def plan(words: list[dict], duration: float, extra: str = "", max_broll: int = 4) -> dict:
    utts = build_utterances(words, max_len=8)
    lines = "\n".join(f"[{u['id']}] ({u['start']:.1f}–{u['end']:.1f}) {u['text']}" for u in utts)
    user = f"Videonun müddəti: {duration:.1f} s\nİstifadəçinin istəyi: {extra or '-'}\n\nSeqmentlər:\n{lines}"
    p = ask_json(PLAN_SYSTEM.format(max_broll=max_broll), user, max_tokens=3000)

    def clamp(items, keys=("start", "end")):
        out = []
        for it in items or []:
            try:
                a, b = float(it[keys[0]]), float(it[keys[1]])
            except (KeyError, TypeError, ValueError):
                continue
            a, b = max(0.0, a), min(duration, b)
            if b - a >= 0.4:
                out.append({**it, keys[0]: a, keys[1]: b})
        return out

    p["highlights"] = clamp(p.get("highlights"))[:20]
    p["zooms"] = clamp(p.get("zooms"))[:20]
    p["broll"] = clamp(p.get("broll"))[:max_broll]
    p["sfx"] = [s for s in p.get("sfx") or [] if isinstance(s, dict) and 0 <= float(s.get("t", -1)) < duration][:30]
    return p


# ---------------- səs effektləri
def _sfx_file(kind: str) -> Path | None:
    f = SFX_DIR / f"{kind}.mp3"
    if f.exists():
        return f
    key = config.ELEVENLABS_API_KEY
    text, dur = SFX_PROMPTS.get(kind, SFX_PROMPTS["pop"])
    if key:
        try:
            r = httpx.post("https://api.elevenlabs.io/v1/sound-generation", headers={"xi-api-key": key},
                           json={"text": text, "duration_seconds": dur, "prompt_influence": 0.6}, timeout=60)
            if r.status_code == 200 and len(r.content) > 1000:
                f.write_bytes(r.content)
                return f
        except httpx.HTTPError:
            pass
    # ehtiyat: ffmpeg ilə sintez
    synth = {"pop": "sine=f=900:d=0.12,afade=t=out:st=0.03:d=0.09",
             "whoosh": "anoisesrc=d=0.6:c=pink,highpass=f=500,afade=t=in:d=0.25,afade=t=out:st=0.3:d=0.3",
             "ding": "sine=f=1320:d=0.5,afade=t=out:st=0.05:d=0.45"}[kind if kind in SFX_PROMPTS else "pop"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", synth, "-ac", "2", str(f)], check=False)
    return f if f.exists() else None


# ---------------- B-roll (Pexels)
def _broll(query: str, vertical: bool, need: float) -> Path | None:
    key = os.getenv("PEXELS_API_KEY", "")
    if not key:
        return None
    safe = "".join(c if c.isalnum() else "_" for c in query.lower())[:50]
    f = BROLL_DIR / f"{safe}_{'v' if vertical else 'h'}.mp4"
    if f.exists():
        return f
    try:
        r = httpx.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                      params={"query": query, "per_page": 5, "orientation": "portrait" if vertical else "landscape",
                              "size": "medium"}, timeout=30)
        for v in r.json().get("videos", []):
            if v.get("duration", 0) < need:
                continue
            files = sorted([x for x in v.get("video_files", []) if x.get("file_type") == "video/mp4" and x.get("width")],
                           key=lambda x: abs((x["height"] if vertical else x["width"]) - 1920))
            if files:
                with httpx.stream("GET", files[0]["link"], timeout=120, follow_redirects=True) as s:
                    with open(f, "wb") as out:
                        for chunk in s.iter_bytes(1 << 20):
                            out.write(chunk)
                return f
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------- ASS: canlı altyazı + açar söz kartları
def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def az_upper(s: str) -> str:
    """Azərbaycan/türk qaydası: i → İ, ı → I."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def write_ass(words: list[dict], highlights: list[dict], w: int, h: int, out: Path) -> Path:
    vertical = h > w
    cap = int(h * (0.052 if vertical else 0.06))
    pop = int(h * (0.062 if vertical else 0.085))
    font = config.SUB_FONT
    mv = int(h * (0.24 if vertical else 0.08))
    head = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{cap},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(3, cap // 11)},{max(2, cap // 18)},2,60,60,{mv},1\n"
        f"Style: Pop,{font},{pop},&H00FFFFFF,&H00FFFFFF,&H00FF8A2B,&H00000000,-1,0,0,0,100,100,1,0,3,"
        f"{max(10, pop // 4)},0,8,60,60,{int(h * 0.16)},1\n"
        f"Style: Num,{font},{int(pop * 1.15)},&H00202020,&H00202020,&H0000D7FF,&H00000000,-1,0,0,0,100,100,1,0,3,"
        f"{max(10, pop // 4)},0,8,60,60,{int(h * 0.16)},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    ev = []
    # canlı altyazı: 2–4 sözlük qruplar, cari söz sarı və bir az böyük
    groups, cur = [], []
    for wd in words:
        if cur and (len(cur) >= (3 if vertical else 5) or wd["start"] - cur[-1]["end"] > 0.5
                    or len(" ".join(x["text"] for x in cur + [wd])) > (22 if vertical else 40)):
            groups.append(cur)
            cur = []
        cur.append(wd)
    if cur:
        groups.append(cur)
    for g in groups:
        for i, wd in enumerate(g):
            st = wd["start"]
            en = g[i + 1]["start"] if i + 1 < len(g) else wd["end"] + 0.15
            parts = []
            for j, x in enumerate(g):
                t = _esc(az_upper(x["text"]) if vertical else x["text"])
                parts.append("{\\c&H00D7FF&\\fscx112\\fscy112}" + t + "{\\r}" if j == i else t)
            ev.append(f"Dialogue: 1,{_ts(st)},{_ts(max(en, st + 0.08))},Cap,,0,0,0,,{' '.join(parts)}")
    # açar söz kartları: sıçrayaraq çıxır, yumşaq itir
    for hl in highlights:
        style = "Num" if hl.get("style") == "number" else "Pop"
        anim = "{\\fad(80,180)\\fscx30\\fscy30\\t(0,170,\\fscx118\\fscy118)\\t(170,300,\\fscx100\\fscy100)}"
        ev.append(f"Dialogue: 2,{_ts(hl['start'])},{_ts(hl['end'])},{style},,0,0,0,,{anim}{_esc(str(hl['text']))}")
    out.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return out


# ---------------- render
def _zoom_expr(zooms: list[dict]) -> str:
    terms = [f"between(it,{z['start']:.2f},{z['end']:.2f})*{float(z.get('scale', 1.1)) - 1:.3f}"
             f"*sin(PI*(it-{z['start']:.2f})/{max(.3, z['end'] - z['start']):.2f})" for z in zooms]
    return "1+" + "+".join(terms) if terms else "1"


def apply(src: str | Path, words: list[dict], out: str | Path, *, extra: str = "", progress=None,
          logo_path: str | None = None) -> dict:
    """src — artıq kəsilmiş video; words — HƏMİN videonun zaman xəttində sözlər."""
    src, out = Path(src), Path(out)
    info = media.probe(src)
    W, H = info["width"] // 2 * 2, info["height"] // 2 * 2
    fps = round(info["fps"]) or 30
    p = plan(words, info["duration"], extra, max_broll=4 if os.getenv("PEXELS_API_KEY") else 0)
    work = out.with_suffix(".fx")
    work.mkdir(exist_ok=True)
    ass = write_ass(words, p["highlights"], W, H, work / "fx.ass")

    inputs = ["-i", str(src)]
    parts = [f"[0:v]zoompan=z='{_zoom_expr(p['zooms'])}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
             f"s={W}x{H}:fps={fps},setsar=1[z0]"]
    cur, idx = "[z0]", 1
    used_broll = []
    for b in p["broll"]:
        dur = b["end"] - b["start"]
        f = _broll(b["query"], H > W, dur)
        if not f:
            continue
        inputs += ["-i", str(f)]
        lab = f"b{idx}"
        parts.append(f"[{idx}:v]trim=0:{dur:.2f},setpts=PTS-STARTPTS+{b['start']:.2f}/TB,"
                     f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=yuva420p,"
                     f"fade=t=in:st={b['start']:.2f}:d=0.25:alpha=1,fade=t=out:st={b['end'] - 0.25:.2f}:d=0.25:alpha=1[{lab}]")
        parts.append(f"{cur}[{lab}]overlay=eof_action=pass:enable='between(t,{b['start']:.2f},{b['end']:.2f})'[o{idx}]")
        cur = f"[o{idx}]"
        idx += 1
        used_broll.append(b)
    parts.append(f"{cur}subtitles=filename='{media._esc(str(ass))}':fontsdir='{media._esc(config.FONTS_DIR)}'[sv]")
    cur = "[sv]"
    if logo_path:
        inputs += ["-i", str(logo_path)]
        lw = int(W * (0.34 if H > W else 0.14)) // 2 * 2
        y = int(H * 0.055) if H > W else int(H * 0.04)
        x = "(W-w)/2" if H > W else f"W-w-{int(W * 0.03)}"
        parts.append(f"[{idx}:v]scale={lw}:-1[lg];{cur}[lg]overlay=x={x}:y={y}[lv]")
        cur = "[lv]"
        idx += 1
    parts.append(f"{cur}format=yuv420p[vout]")

    # səs: əsas + effektlər
    sfx_list = list(p["sfx"]) + [{"t": b["start"], "type": "whoosh"} for b in used_broll
                                 if not any(abs(float(s["t"]) - b["start"]) < .3 for s in p["sfx"])]
    amix_in = []
    if info["has_audio"]:
        amix_in.append("[0:a]")
    for s in sfx_list[:30]:
        f = _sfx_file(s.get("type", "pop"))
        if not f:
            continue
        inputs += ["-i", str(f)]
        ms = int(float(s["t"]) * 1000)
        parts.append(f"[{idx}:a]volume=0.35,adelay={ms}|{ms},aformat=sample_rates=48000:channel_layouts=stereo[s{idx}]")
        amix_in.append(f"[s{idx}]")
        idx += 1
    if amix_in:
        if len(amix_in) == 1:
            parts.append(f"{amix_in[0]}aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]")
        else:
            parts.append("".join(amix_in) + f"amix=inputs={len(amix_in)}:duration=first:normalize=0,"
                         "alimiter=limit=0.95,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]")

    script = work / "filter.txt"
    script.write_text(";\n".join(parts), encoding="utf-8")
    tmp = out.with_name(out.stem + "_fx.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-progress", "pipe:1", *inputs, "-filter_complex_script", str(script),
           "-map", "[vout]"]
    if amix_in:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", config.X264_CRF, "-movflags", "+faststart",
            "-t", f"{info['duration']:.3f}", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if progress and line.startswith("out_time_us="):
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / max(1, info["duration"])))
            except ValueError:
                pass
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError(f"Dinamik montaj xətası:\n{err[-2000:]}")
    tmp.replace(out)
    return {"highlights": len(p["highlights"]), "zooms": len(p["zooms"]), "broll": len(used_broll),
            "sfx": len(sfx_list), "plan": p}
