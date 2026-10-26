"""Sözlərdən cümlələr (seqmentlər), saxlanacaq aralıqlar və yeni zaman xəttinə köçürmə."""

PAUSE_SPLIT = 0.55      # bu qədər fasilə yeni seqment başladır
PAD_BEFORE = 0.12
PAD_AFTER = 0.22
MERGE_GAP = 0.30        # aralıqlar arasında bundan az boşluq varsa birləşdir


def build_utterances(words: list[dict], max_len: float = 14.0) -> list[dict]:
    utts, cur = [], []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            sentence_end = cur[-1]["text"][-1:] in ".?!…"
            too_long = w["end"] - cur[0]["start"] > max_len
            if gap > PAUSE_SPLIT or (sentence_end and gap > 0.2) or too_long:
                utts.append(cur)
                cur = []
        cur.append(w)
    if cur:
        utts.append(cur)
    return [
        {"id": i, "start": u[0]["start"], "end": u[-1]["end"], "text": " ".join(x["text"] for x in u), "words": u}
        for i, u in enumerate(utts)
    ]


def utts_to_ranges(utts: list[dict], duration: float) -> list[tuple[float, float]]:
    rs = []
    for u in sorted(utts, key=lambda x: x["start"]):
        a = max(0.0, u["start"] - PAD_BEFORE)
        b = min(duration, u["end"] + PAD_AFTER)
        if rs and a - rs[-1][1] < MERGE_GAP:
            rs[-1] = (rs[-1][0], max(rs[-1][1], b))
        else:
            rs.append((a, b))
    # üst-üstə düşmələri təmizlə
    clean = []
    for a, b in rs:
        if clean and a < clean[-1][1]:
            a = clean[-1][1]
        if b - a > 0.05:
            clean.append((a, b))
    return clean


def remap_time(t: float, ranges: list[tuple[float, float]]) -> float | None:
    """Mənbə vaxtını montaj edilmiş videodakı vaxta çevirir (kəsilibsə None)."""
    off = 0.0
    for a, b in ranges:
        if a <= t <= b:
            return off + (t - a)
        off += b - a
    return None


def remap_words(words: list[dict], ranges) -> list[dict]:
    out = []
    for w in words:
        s = remap_time(w["start"], ranges)
        e = remap_time(w["end"], ranges)
        if s is None and e is None:
            continue
        if s is None:
            s = max(0.0, e - (w["end"] - w["start"]))
        if e is None:
            e = s + (w["end"] - w["start"])
        out.append({"text": w["text"], "start": s, "end": max(e, s + 0.05)})
    return out


def fmt_ts(t: float) -> str:
    t = int(t)
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def numbered(utts: list[dict]) -> str:
    return "\n".join(f"[{u['id']}] ({u['start']:.1f}–{u['end']:.1f}) {u['text']}" for u in utts)
