"""ASS altyazı faylları: uzun video (aşağıda, 2 sətir) və Reels (iri, ağ qutuda, 2–3 söz)."""
from pathlib import Path

import config


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(txt: str) -> str:
    return txt.replace("\\", "").replace("{", "(").replace("}", ")")


def _groups(words, max_chars: int, max_words: int, max_dur: float):
    cur, groups = [], []
    for w in words:
        if cur:
            text_len = len(" ".join(x["text"] for x in cur + [w]))
            gap = w["start"] - cur[-1]["end"]
            if (text_len > max_chars or len(cur) >= max_words or gap > 0.6
                    or w["end"] - cur[0]["start"] > max_dur or cur[-1]["text"][-1:] in ".?!"):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _header(w: int, h: int, style: str) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {w}\nPlayResY: {h}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def write_long(words, w: int, h: int, out: Path) -> Path:
    fs = max(28, int(h * 0.048))
    style = (f"Style: Default,{config.SUB_FONT},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
             f"-1,0,0,0,100,100,0,0,1,{max(2, fs // 16)},0,2,{int(w*0.08)},{int(w*0.08)},{int(h*0.06)},1")
    lines = []
    for g in _groups(words, max_chars=42, max_words=9, max_dur=4.0):
        lines.append(f"Dialogue: 0,{_ts(g[0]['start'])},{_ts(g[-1]['end'] + 0.15)},Default,,0,0,0,,"
                     + _clean(" ".join(x["text"] for x in g)))
    out.write_text(_header(w, h, style) + "\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_reels(words, out: Path, w: int = 1080, h: int = 1920) -> Path:
    # Ağ qutu, tünd yazı — videodakı üsluba bənzər
    style = (f"Style: Default,{config.SUB_FONT},74,&H00222222,&H00222222,&H00FFFFFF,&H50000000,"
             f"-1,0,0,0,100,100,0,0,3,14,0,2,90,90,{int(h*0.27)},1")
    lines = []
    groups = _groups(words, max_chars=18, max_words=3, max_dur=1.6)
    for i, g in enumerate(groups):
        end = g[-1]["end"] + 0.08
        if i + 1 < len(groups):
            end = min(end + 0.25, groups[i + 1][0]["start"])
        lines.append(f"Dialogue: 0,{_ts(g[0]['start'])},{_ts(end)},Default,,0,0,0,,"
                     + _clean(" ".join(x["text"] for x in g)))
    out.write_text(_header(w, h, style) + "\n".join(lines) + "\n", encoding="utf-8")
    return out
