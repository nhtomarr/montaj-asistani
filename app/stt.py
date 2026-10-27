"""Səs → mətn (söz-söz zaman damğaları ilə).

Qaytarır: [{"text": str, "start": float, "end": float}, ...]
"""
from pathlib import Path

import httpx

import config


def transcribe(audio_path: str | Path, with_words: bool = True, language: str | None = None) -> dict:
    p = config.STT_PROVIDER.lower()
    lang = language if language is not None else config.STT_LANGUAGE
    if p == "elevenlabs":
        return _elevenlabs(audio_path, lang)
    if p == "openai":
        return _openai(audio_path, lang)
    if p == "local":
        return _local(audio_path, lang)
    raise ValueError(f"Naməlum STT_PROVIDER: {p}")


def _elevenlabs(path, lang: str = "") -> dict:
    data = {
        "model_id": config.ELEVENLABS_STT_MODEL,
        "timestamps_granularity": "word",
        "tag_audio_events": "false",
        "diarize": "false",
    }
    if lang:
        data["language_code"] = lang
    with open(path, "rb") as f:
        r = httpx.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
            data=data,
            files={"file": (Path(path).name, f, "audio/mpeg")},
            timeout=1800,
        )
    r.raise_for_status()
    j = r.json()
    words = [
        {"text": w["text"].strip(), "start": float(w["start"]), "end": float(w["end"])}
        for w in j.get("words", [])
        if w.get("type", "word") == "word" and w.get("text", "").strip()
    ]
    return {"language": j.get("language_code", ""), "text": j.get("text", ""), "words": words}


def _openai(path, lang: str = "") -> dict:
    # Fayl 25 MB-dan kiçik olmalıdır (32 kbps mp3 ≈ 100 dəqiqə)
    data = [
        ("model", "whisper-1"),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
    ]
    if lang:
        data.append(("language", lang[:2]))
    with open(path, "rb") as f:
        r = httpx.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            data=data,
            files={"file": (Path(path).name, f, "audio/mpeg")},
            timeout=1800,
        )
    r.raise_for_status()
    j = r.json()
    words = [
        {"text": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])}
        for w in j.get("words", [])
        if w.get("word", "").strip()
    ]
    # Whisper sözlərdə durğu işarəsini atır; seqmentlərdən bərpa edirik
    _restore_punct(words, j.get("segments", []))
    return {"language": j.get("language", ""), "text": j.get("text", ""), "words": words}


def _restore_punct(words: list, segments: list) -> None:
    for s in segments:
        txt = s.get("text", "").strip()
        if not txt or txt[-1] not in ".?!":
            continue
        cand = [w for w in words if s["start"] - 0.05 <= w["start"] <= s["end"] + 0.05]
        if cand and cand[-1]["text"][-1:] not in ".?!":
            cand[-1]["text"] += txt[-1]


_local_model = None


def _local(path, lang_code: str = "") -> dict:
    """Serverdə yerli Whisper (API açarı lazım deyil, amma CPU-da yavaşdır)."""
    global _local_model
    from faster_whisper import WhisperModel

    if _local_model is None:
        _local_model = WhisperModel(config.LOCAL_WHISPER_MODEL, device="cpu", compute_type="int8")
    import subprocess
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    lang = lang_code[:2] if lang_code else None
    segs, info = _local_model.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True)
    words, texts = [], []
    for s in segs:
        texts.append(s.text)
        for w in s.words or []:
            if w.word.strip():
                words.append({"text": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return {"language": info.language, "text": " ".join(texts).strip(), "words": words}


def transcribe_short(audio_path) -> str:
    """Səsli əmr üçün mətn. Qısa yazılarda dili avtomatik tanımaq səhv olur (məs. norveç), ona görə
    COMMAND_LANGUAGE (default: Azərbaycan dili) ilə tanıyırıq."""
    import os
    return transcribe(audio_path, language=os.getenv("COMMAND_LANGUAGE", "aze")).get("text", "").strip()
