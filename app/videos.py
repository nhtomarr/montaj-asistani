"""Gələn videoları qəbul edib qeydə alır."""
import re
import shutil
import time
from pathlib import Path

import config
import db
from editor import media


def _safe(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.U).strip("._") or "video.mp4"
    return name[-80:]


def ingest(path: str | Path, name: str, source: str, move: bool = True) -> dict:
    path = Path(path)
    dest = config.VIDEOS_DIR / f"{int(time.time())}_{_safe(name)}"
    if not dest.suffix:
        dest = dest.with_suffix(".mp4")
    if move:
        try:
            shutil.move(str(path), dest)
        except PermissionError:
            shutil.copy2(path, dest)
    else:
        shutil.copy2(path, dest)
    info = media.probe(dest)
    vid = db.add_video(str(dest), name, info["duration"], info["width"], info["height"], source)
    return db.get_video(vid)
