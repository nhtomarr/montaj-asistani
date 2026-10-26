"""Tapşırığın icrası: skan → mətn → kəsim → render → Reels → yükləmə."""
import json
import secrets
import shutil
import threading
import time
import traceback
from pathlib import Path

import config
import db
import notify
from editor import media, subtitles, transcript as tr
from editor.decisions import clean_decision, reels_decision
from uploaders import instagram, youtube
import stt

STAGES = {
    "scan": "Çəkiliş skan edilir",
    "stt": "Səs yazıya çevrilir",
    "cut": "Təkrarlar və boşluqlar atılır",
    "render": "Montaj render edilir",
    "reels": "Reels hazırlanır",
    "youtube": "YouTube-a yüklənir",
    "instagram": "Instagram-a yüklənir",
}


class Progress:
    def __init__(self, job_id: int, order: list[str]):
        self.job_id, self.order = job_id, order
        self.done: set[str] = set()
        self.cur: str | None = None
        self.pct: float | None = None
        self.notes: list[str] = []
        self._last = 0.0

    def start(self, key: str):
        if self.cur:
            self.done.add(self.cur)
        self.cur, self.pct = key, None
        db.update_job(self.job_id, stage=key)
        self.push(force=True)

    def percent(self, p: float):
        self.pct = p
        self.push()

    def note(self, text: str):
        self.notes.append(text)
        self.push(force=True)

    def finish(self):
        if self.cur:
            self.done.add(self.cur)
        self.cur = None
        self.push(force=True)

    def text(self) -> str:
        lines = ["✅ Tapşırıq alındı"]
        for i, k in enumerate(self.order, 1):
            if k in self.done:
                mark = "✔️"
            elif k == self.cur:
                mark = "⏳"
            else:
                mark = "▫️"
            extra = f" — {int(self.pct * 100)}%" if k == self.cur and self.pct is not None else ""
            lines.append(f"{mark} {i}. {STAGES[k]}{extra}")
        if self.notes:
            lines.append("")
            lines += [f"• {n}" for n in self.notes]
        return "\n".join(lines)

    def push(self, force: bool = False):
        now = time.time()
        if force or now - self._last > 4:
            self._last = now
            notify.sink.progress(self.job_id, self.text())


def _logo_path(name) -> str | None:
    if not name:
        return None
    files = sorted(config.LOGOS_DIR.glob("*.png"))
    if not files:
        return None
    for f in files:
        if f.stem.lower() == str(name).lower():
            return str(f)
    d = config.LOGOS_DIR / "default.png"
    return str(d if d.exists() else files[0])


def _public_path(name: str) -> tuple[Path, str]:
    tok = secrets.token_urlsafe(12)
    d = config.PUBLIC_DIR / tok
    d.mkdir(parents=True, exist_ok=True)
    return d / name, f"{config.PUBLIC_URL}/media/{tok}/{name}"


def _chapters_text(dec: dict, utts: list[dict], ranges) -> str:
    chs = []
    byid = {u["id"]: u for u in utts}
    for c in dec.get("chapters") or []:
        u = byid.get(c.get("id"))
        if u is None:
            continue
        t = tr.remap_time(max(u["start"] - tr.PAD_BEFORE, ranges[0][0]), ranges)
        if t is None:
            t = tr.remap_time(u["start"], ranges)
        if t is not None:
            chs.append((t, c.get("title", "")))
    chs.sort()
    if len(chs) < 3:
        return ""
    chs[0] = (0.0, chs[0][1])
    return "\n".join(f"{tr.fmt_ts(t)} {title}" for t, title in chs)


def run_job(job_id: int) -> None:
    job = db.get_job(job_id)
    plan = job["plan"]
    video = db.get_video(job["video_id"])
    order = ["scan", "stt", "cut"]
    if plan.get("long_video"):
        order.append("render")
    if plan.get("reels"):
        order.append("reels")
    if plan.get("upload_youtube"):
        order.append("youtube")
    if plan.get("upload_instagram"):
        order.append("instagram")
    pr = Progress(job_id, order)
    result: dict = {}
    db.update_job(job_id, status="running")
    try:
        src = video["path"]
        wdir = config.WORK_DIR / f"video_{video['id']}"
        wdir.mkdir(parents=True, exist_ok=True)

        pr.start("scan")
        info = media.probe(src)

        pr.start("stt")
        tfile = wdir / "transcript.json"
        if tfile.exists():
            tj = json.loads(tfile.read_text())
        elif info["has_audio"]:
            audio = media.extract_audio(src, wdir / "audio.mp3",
                                        bitrate="32k" if config.STT_PROVIDER == "openai" else "48k")
            tj = stt.transcribe(audio)
            tfile.write_text(json.dumps(tj, ensure_ascii=False))
        else:
            tj = {"words": [], "language": ""}
        words = tj["words"]
        lang = (tj.get("language") or "az")[:2]
        utts = tr.build_utterances(words)

        pr.start("cut")
        dec: dict = {}
        if plan.get("clean_edit") and len(words) >= 8:
            dec = clean_decision(utts, config.rules(), plan.get("extra", ""), info["duration"])
            removed = set(dec["remove"])
            keep = [u for u in utts if u["id"] not in removed]
            ranges = tr.utts_to_ranges(keep, info["duration"]) or [(0, info["duration"])]
        else:
            keep = utts
            ranges = [(0.0, info["duration"])]
        new_dur = sum(b - a for a, b in ranges)
        pr.note(f"{tr.fmt_ts(info['duration'])} → {tr.fmt_ts(new_dur)} "
                f"({len(utts) - len(keep)} təkrar/artıq hissə atıldı)")
        result["removed"] = len(utts) - len(keep)
        result["duration_before"], result["duration_after"] = info["duration"], new_dur

        loud = media.measure_loudness(src, config.LONG_LOUDNESS) if (plan.get("loudnorm") and info["has_audio"]) else None

        if plan.get("long_video"):
            pr.start("render")
            out, url = _public_path(f"montaj_{video['id']}_{job_id}.mp4")
            ass = None
            if plan.get("subtitles_long") and words:
                ass = subtitles.write_long(tr.remap_words(words, ranges), info["width"], info["height"],
                                           wdir / f"long_{job_id}.ass")
            media.render(src, ranges, out, info=info, mode="orig", loud=loud, loud_target=config.LONG_LOUDNESS,
                         ass_path=str(ass) if ass else None, progress=pr.percent)
            desc = dec.get("description", "")
            ch = _chapters_text(dec, keep, ranges)
            if ch:
                desc = f"{desc}\n\n{ch}"
            result["long"] = {"path": str(out), "url": url, "title": plan.get("youtube_title") or dec.get("title"),
                              "description": desc, "tags": dec.get("tags", [])}

        if plan.get("reels"):
            pr.start("reels")
            max_s = int(plan.get("reels_max_seconds") or config.REELS_MAX_SECONDS)
            if new_dur <= max_s + 5 or not keep:
                r_ranges, rdec = ranges, {}
            else:
                rdec = reels_decision(keep, config.rules(), plan.get("extra", ""), min(20, max_s), max_s)
                sel = [u for u in keep if u["id"] in set(rdec["keep"])] or keep
                r_ranges = tr.utts_to_ranges(sel, info["duration"])
            out, url = _public_path(f"reels_{video['id']}_{job_id}.mp4")
            ass = None
            if plan.get("subtitles_reels", True) and words:
                ass = subtitles.write_reels(tr.remap_words(words, r_ranges), wdir / f"reels_{job_id}.ass")
            media.render(src, r_ranges, out, info=info, mode="reels", loud=loud, loud_target=config.LONG_LOUDNESS,
                         ass_path=str(ass) if ass else None, logo_path=_logo_path(plan.get("logo")),
                         framing=config.REELS_FRAMING, progress=pr.percent)
            result["reels"] = {"path": str(out), "url": url,
                               "caption": plan.get("instagram_caption") or rdec.get("caption") or dec.get("description", ""),
                               "title": rdec.get("title") or dec.get("title"),
                               "duration": sum(b - a for a, b in r_ranges)}

        result["language"] = lang
        db.update_job(job_id, result=result)

        # Yükləmə
        pending, errors = [], []
        privacy = plan.get("youtube_privacy") or config.YOUTUBE_DEFAULT_PRIVACY
        for target, need_confirm in (
            ("youtube", config.REQUIRE_CONFIRM_PUBLIC and privacy == "public"),
            ("instagram", config.REQUIRE_CONFIRM_PUBLIC),
        ):
            if not plan.get(f"upload_{target}"):
                continue
            if need_confirm:
                pending.append(target)
                continue
            pr.start(target)
            try:
                publish(job_id, target, pr)
            except Exception as e:  # noqa: BLE001 — render hazırdır, yükləmə xətası işi pozmasın
                traceback.print_exc()
                errors.append(f"{target}: {e}")
        pr.finish()
        result = db.get_job(job_id)["result"]
        result["pending"] = pending
        result["errors"] = errors
        db.update_job(job_id, status="done", stage="", result=result)
        notify.sink.finished(job_id)
        if pending:
            notify.sink.confirm(job_id, pending)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.update_job(job_id, status="error", error=str(e)[:2000])
        notify.sink.failed(job_id, str(e))


def publish(job_id: int, target: str, pr: Progress | None = None) -> str:
    job = db.get_job(job_id)
    plan, res = job["plan"], job["result"]
    cb = pr.percent if pr else None
    if target == "youtube":
        if not youtube.configured():
            raise RuntimeError("YouTube açarları .env-də təyin edilməyib")
        privacy = plan.get("youtube_privacy") or config.YOUTUBE_DEFAULT_PRIVACY
        if "long" in res:
            v = res["long"]
            link = youtube.upload(v["path"], v.get("title") or "Video", v.get("description", ""), v.get("tags"),
                                  privacy, res.get("language", "az"), cb)
        elif "reels" in res:
            v = res["reels"]
            link = youtube.upload(v["path"], ((v.get("title") or "Video") + " #Shorts")[:100], v.get("caption", ""),
                                  None, privacy, res.get("language", "az"), cb)
        else:
            raise RuntimeError("Yükləmək üçün video yoxdur")
        res["youtube"] = link
    elif target == "instagram":
        if not instagram.configured():
            raise RuntimeError("Instagram açarları .env-də təyin edilməyib")
        if "reels" not in res:
            raise RuntimeError("Instagram üçün Reels versiyası hazırlanmayıb")
        link = instagram.publish_reel(res["reels"]["url"], res["reels"].get("caption", ""), cb)
        res["instagram"] = link
    else:
        raise ValueError(target)
    res["pending"] = [p for p in res.get("pending", []) if p != target]
    db.update_job(job_id, result=res)
    return link


# ---- növbə işçisi
_wake = threading.Event()


def wake() -> None:
    _wake.set()


def worker_loop() -> None:
    db.reset_running_jobs()
    last_cleanup = 0.0
    while True:
        job = db.next_queued_job()
        if job:
            run_job(job["id"])
            continue
        if time.time() - last_cleanup > 6 * 3600:
            last_cleanup = time.time()
            cleanup()
            instagram.refresh_token_if_needed()
        _wake.wait(timeout=30)
        _wake.clear()


def cleanup() -> None:
    limit = time.time() - config.KEEP_DAYS * 86400
    for base in (config.PUBLIC_DIR, config.WORK_DIR):
        for d in base.iterdir():
            if d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
    for f in config.VIDEOS_DIR.iterdir():
        if f.stat().st_mtime < limit:
            f.unlink(missing_ok=True)


def start_worker() -> None:
    threading.Thread(target=worker_loop, daemon=True, name="worker").start()
