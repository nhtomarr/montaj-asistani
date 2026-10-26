"""ffmpeg əməliyyatları: probe, səs çıxarma, səs ölçmə, kəsib birləşdirmə + render."""
import json
import re
import subprocess
from pathlib import Path

import config


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} xətası:\n{r.stderr[-2000:]}")
    return r


def probe(path: str | Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        raise ValueError("Faylda video axını yoxdur")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(v.get("tags", {}).get("rotate", rot))
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    if not 10 <= fps <= 120:
        fps = 30.0
    return {
        "duration": float(j["format"].get("duration") or v.get("duration") or 0),
        "width": w,
        "height": h,
        "fps": fps,
        "has_audio": a is not None,
        "vertical": h > w,
    }


def extract_audio(src, out, bitrate: str = "48k") -> Path:
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "libmp3lame", "-b:a", bitrate, str(out)])
    return Path(out)


def measure_loudness(src, target: float) -> dict | None:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(src), "-vn", "-af",
         f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return None
    d = json.loads(m.group(0))
    if d.get("input_i") in ("-inf", None):
        return None
    return d


def _loudnorm_filter(target: float, m: dict | None) -> str:
    base = f"loudnorm=I={target}:TP=-1.5:LRA=11"
    if m:
        base += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                 f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    # Kanal düzümünü açıq göstəririk — köhnə ffmpeg (Debian 5.1) loudnorm-dan sonra onu itirir
    return base + ",aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def _esc(path: str) -> str:
    """ffmpeg filtr arqumenti üçün yolu qoruyur."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def render(
    src,
    ranges: list[tuple[float, float]],
    out,
    *,
    info: dict,
    mode: str = "orig",            # orig | reels
    loud: dict | None = None,
    loud_target: float = -14,
    ass_path: str | None = None,
    logo_path: str | None = None,
    framing: str = "crop",
    progress=None,
) -> Path:
    """Verilən aralıqları kəsib birləşdirir və bir keçiddə kodlaşdırır."""
    out = Path(out)
    ranges = [(max(0.0, a), min(b, info["duration"])) for a, b in ranges if b - a > 0.05]
    if not ranges:
        raise ValueError("Saxlanacaq hissə qalmadı")
    has_a = info["has_audio"]
    parts, labels = [], []
    for i, (a, b) in enumerate(ranges):
        d = b - a
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        lab = f"[v{i}]"
        if has_a:
            fo = max(0.0, d - 0.012)
            parts.append(
                f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"afade=t=in:st=0:d=0.012,afade=t=out:st={fo:.3f}:d=0.012[a{i}]"
            )
            lab += f"[a{i}]"
        labels.append(lab)
    n = len(ranges)
    parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_a else 0}[vc]" + ("[ac]" if has_a else ""))

    vchain = "[vc]"
    if mode == "reels":
        W, H, fps = 1080, 1920, 30
        if framing == "blur" and not info["vertical"]:
            parts.append(
                f"{vchain}split[bg0][fg0];"
                f"[bg0]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3[bg];"
                f"[fg0]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vf0]"
            )
        else:
            parts.append(
                f"{vchain}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[vf0]"
            )
    else:
        fps = round(info["fps"]) if info["fps"] else 30
        W, H = info["width"] // 2 * 2, info["height"] // 2 * 2
        parts.append(f"{vchain}scale={W}:{H},setsar=1[vf0]")
    cur = "[vf0]"
    if ass_path:
        parts.append(f"{cur}subtitles=filename='{_esc(str(ass_path))}':fontsdir='{_esc(config.FONTS_DIR)}'[vf1]")
        cur = "[vf1]"
    inputs = ["-i", str(src)]
    if logo_path:
        inputs += ["-i", str(logo_path)]
        lw = int(W * (0.34 if mode == "reels" else 0.14)) // 2 * 2
        y = int(H * 0.055) if mode == "reels" else int(H * 0.04)
        x = "(W-w)/2" if mode == "reels" else f"W-w-{int(W * 0.03)}"
        parts.append(f"[1:v]scale={lw}:-1[lg];{cur}[lg]overlay=x={x}:y={y}:format=auto[vf2]")
        cur = "[vf2]"
    parts.append(f"{cur}fps={fps},format=yuv420p[vout]")
    if has_a:
        parts.append(f"[ac]{_loudnorm_filter(loud_target, loud)}[aout]")

    script = out.with_suffix(".filter.txt")
    script.write_text(";\n".join(parts), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats_period", "2", "-progress", "pipe:1", *inputs,
           "-filter_complex_script", str(script), "-map", "[vout]"]
    if has_a:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", config.X264_CRF,
            "-profile:v", "high", "-movflags", "+faststart", str(out)]

    total = sum(b - a for a, b in ranges)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if progress and line.startswith("out_time_us="):
            try:
                t = int(line.split("=")[1]) / 1e6
                progress(min(1.0, t / total) if total else 0)
            except ValueError:
                pass
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg render xətası:\n{err[-2500:]}")
    return out
