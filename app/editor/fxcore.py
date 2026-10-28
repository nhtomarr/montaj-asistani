"""Effekt mühərrikinin əsası: kadr-kadr emal (numpy/OpenCV/PIL), insan maskası (MediaPipe), köməkçi funksiyalar."""
from __future__ import annotations

import math
import random
import subprocess
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
MODEL_DIR = config.DATA_DIR / "models"
SEG_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/"
           "float16/latest/selfie_segmenter.tflite")

ACCENT = (255, 196, 0)      # sarı
ACCENT2 = (255, 92, 56)     # narıncı-qırmızı
INK = (18, 18, 20)
PAPER = (243, 234, 215)


# ---------------------------------------------------------------- şriftlər və mətn
FONTS = {
    "anton": ("Anton.ttf", None),
    "inter": ("Inter.ttf", "Bold"),
    "inter_black": ("Inter.ttf", "Black"),
    "inter_reg": ("Inter.ttf", "Regular"),
    "mont": ("Montserrat.ttf", "ExtraBold"),
    "serif": ("PlayfairItalic.ttf", "Bold Italic"),
    "hand": ("Caveat.ttf", "Bold"),
    "mono": ("JetBrainsMono.ttf", "Bold"),
}


@lru_cache(maxsize=256)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    file, var = FONTS.get(name, FONTS["inter"])
    f = ImageFont.truetype(str(FONT_DIR / file), max(6, int(size)))
    if var:
        try:
            f.set_variation_by_name(var)
        except Exception:  # noqa: BLE001 — dəyişkən şrift deyilsə və ya ad tapılmadısa
            pass
    return f


def az_upper(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def text_size(txt: str, f) -> tuple[int, int, int, int]:
    return ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), txt, font=f)


def fit_font(txt: str, name: str, max_w: int, max_size: int, min_size: int = 10):
    size = max_size
    while size > min_size:
        b = text_size(txt, font(name, size))
        if b[2] - b[0] <= max_w:
            break
        size = int(size * 0.92)
    return font(name, size)


def text_rgba(txt: str, f, fill=(255, 255, 255), stroke: int = 0, stroke_fill=(0, 0, 0), pad: int = 4,
              bg=None, bg_pad=(0, 0), radius: int = 0) -> np.ndarray:
    b = text_size(txt, f)
    tw, th = b[2] - b[0], b[3] - b[1]
    px, py = bg_pad
    w, h = tw + 2 * (pad + stroke + px), th + 2 * (pad + stroke + py)
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if bg is not None:
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=tuple(bg) + ((255,) if len(bg) == 3 else ()))
    d.text((pad + stroke + px - b[0], pad + stroke + py - b[1]), txt, font=f, fill=tuple(fill) + (255,),
           stroke_width=stroke, stroke_fill=tuple(stroke_fill) + (255,))
    return np.array(im)


def wrap(txt: str, f, max_w: int) -> list[str]:
    words, lines, cur = txt.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        b = text_size(t, f)
        if b[2] - b[0] > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- kompozisiya
def over(dst: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha: float = 1.0) -> np.ndarray:
    """RGBA şəkli dst (RGB, uint8) üzərinə x,y nöqtəsinə qoyur (kənardan çıxan hissə kəsilir)."""
    if alpha <= 0.001:
        return dst
    H, W = dst.shape[:2]
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return dst
    src = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = src[..., 3:4].astype(np.float32) * (alpha / 255.0)
    roi = dst[y0:y1, x0:x1].astype(np.float32)
    rgb = roi[..., :3] * (1 - a) + src[..., :3].astype(np.float32) * a
    if dst.shape[2] == 4:
        da = roi[..., 3:4] * (1 - a) + 255 * a
        dst[y0:y1, x0:x1] = np.concatenate([rgb, da], 2).astype(np.uint8)
    else:
        dst[y0:y1, x0:x1] = rgb.astype(np.uint8)
    return dst


def over_mask(dst: np.ndarray, src: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """mask (float 0..1, HxW) ilə src-ni dst üzərinə qoyur."""
    m = mask[..., None].astype(np.float32)
    return (dst.astype(np.float32) * (1 - m) + src.astype(np.float32) * m).astype(np.uint8)


def rgba_from(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return np.dstack([rgb, np.clip(mask * 255, 0, 255).astype(np.uint8)])


def rounded_mask(w: int, h: int, r: int) -> np.ndarray:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return np.array(m)


def shadow(rgba: np.ndarray, blur: int = 25, opacity: float = 0.55, grow: int = 0) -> np.ndarray:
    a = rgba[..., 3].astype(np.float32) * opacity
    pad = blur * 2 + grow
    a = np.pad(a, pad)
    if grow:
        a = cv2.dilate(a, np.ones((grow, grow), np.uint8))
    a = cv2.GaussianBlur(a, (0, 0), blur)
    out = np.zeros(a.shape + (4,), np.uint8)
    out[..., 3] = np.clip(a, 0, 255).astype(np.uint8)
    return out


def drop(dst: np.ndarray, rgba: np.ndarray, x: int, y: int, blur_px: int = 25, opacity: float = .5,
         dx: int = 0, dy: int = 12, alpha: float = 1.0) -> np.ndarray:
    """Kölgə + şəkil birlikdə."""
    sh = shadow(rgba, blur_px, opacity)
    pad = blur_px * 2
    over(dst, sh, x - pad + dx, y - pad + dy, alpha)
    return over(dst, rgba, x, y, alpha)


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Sürətli güclü bulanıqlıq: kiçildib bulandır, geri böyüt."""
    h, w = img.shape[:2]
    k = 4
    small = cv2.resize(img, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), max(0.5, sigma / k))
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def darken(img: np.ndarray, f: float) -> np.ndarray:
    return (img.astype(np.float32) * f).astype(np.uint8)


def zoom(img: np.ndarray, s: float, cx: float | None = None, cy: float | None = None, angle: float = 0.0,
         border=cv2.BORDER_REFLECT) -> np.ndarray:
    """cx,cy (piksel) mərkəzli s dəfə yaxınlaşma (+ dönmə)."""
    h, w = img.shape[:2]
    cx = w / 2 if cx is None else cx
    cy = h / 2 if cy is None else cy
    M = cv2.getRotationMatrix2D((cx, cy), angle, s)
    M[0, 2] += w / 2 - cx
    M[1, 2] += h / 2 - cy
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=border)


def resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    inter = cv2.INTER_AREA if w < img.shape[1] else cv2.INTER_LINEAR
    return cv2.resize(img, (max(1, int(w)), max(1, int(h))), interpolation=inter)


def warp_quad(src: np.ndarray, quad: np.ndarray, W: int, H: int) -> tuple[np.ndarray, np.ndarray]:
    """src-ni (RGB və ya RGBA) ekrandakı 4 nöqtəyə (TL,TR,BR,BL) perspektivlə qoyur → (rgb, alpha)."""
    h, w = src.shape[:2]
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32(quad))
    rgb = cv2.warpPerspective(src[..., :3], M, (W, H), flags=cv2.INTER_LINEAR)
    a_src = src[..., 3] if src.shape[2] == 4 else np.full((h, w), 255, np.uint8)
    a = cv2.warpPerspective(a_src, M, (W, H), flags=cv2.INTER_LINEAR)
    return rgb, a.astype(np.float32) / 255.0


def rot_quad(cx, cy, w, h, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0, focal=1800.0, z=0.0):
    """3D fırlanmış düzbucaqlının ekrandakı 4 küncü (sadə perspektiv proyeksiya)."""
    ya, pa, ra = (math.radians(a) for a in (yaw_deg, pitch_deg, roll_deg))
    pts = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]], np.float64)
    Ry = np.array([[math.cos(ya), 0, math.sin(ya)], [0, 1, 0], [-math.sin(ya), 0, math.cos(ya)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(pa), -math.sin(pa)], [0, math.sin(pa), math.cos(pa)]])
    Rz = np.array([[math.cos(ra), -math.sin(ra), 0], [math.sin(ra), math.cos(ra), 0], [0, 0, 1]])
    p = pts @ (Rz @ Rx @ Ry).T
    p[:, 2] += focal + z
    return np.stack([cx + p[:, 0] * focal / p[:, 2], cy + p[:, 1] * focal / p[:, 2]], 1)


def gradient(W: int, H: int, c1, c2, vertical=True) -> np.ndarray:
    t = np.linspace(0, 1, H if vertical else W, dtype=np.float32)
    g = np.array(c1, np.float32)[None] * (1 - t[:, None]) + np.array(c2, np.float32)[None] * t[:, None]
    g = g.astype(np.uint8)
    return np.repeat(g[:, None, :], W, 1) if vertical else np.repeat(g[None, :, :], H, 0)


def vignette(img: np.ndarray, strength: float = 0.55) -> np.ndarray:
    h, w = img.shape[:2]
    y, x = np.ogrid[:h, :w]
    d = ((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2
    v = 1 - strength * np.clip(d / 2, 0, 1)
    return (img.astype(np.float32) * v[..., None]).astype(np.uint8)


def noise_tex(W: int, H: int, base, amp: int = 10, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = rng.normal(0, amp, (H // 2, W // 2, 1)).astype(np.float32)
    n = cv2.resize(n, (W, H))[..., None]
    return np.clip(np.array(base, np.float32)[None, None] + n, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- animasiya
def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(p: float) -> float:
    p = clamp01(p)
    return 1 - (1 - p) ** 3


def ease_in(p: float) -> float:
    p = clamp01(p)
    return p ** 3


def ease_io(p: float) -> float:
    p = clamp01(p)
    return 4 * p ** 3 if p < .5 else 1 - (-2 * p + 2) ** 3 / 2


def back_out(p: float, s: float = 1.7) -> float:
    p = clamp01(p) - 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def env(t: float, dur: float, fin: float = .3, fout: float = .25) -> tuple[float, float]:
    """(girişin irəliləyişi, çıxışın irəliləyişi) — hər biri 0..1."""
    return clamp01(t / fin) if fin else 1.0, clamp01((t - (dur - fout)) / fout) if fout else 0.0


# ---------------------------------------------------------------- insan maskası
class Segmenter:
    """MediaPipe selfie seqmentasiyası; kiçik ölçüdə işləyir, maskanı hamarlayıb böyüdür."""

    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.prev = None
        self.ts = 0
        self._seg = None
        self.ok = True

    def _load(self):
        if self._seg is not None or not self.ok:
            return
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions
            from mediapipe.tasks.python import vision
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            mpath = MODEL_DIR / "selfie_segmenter.tflite"
            if not mpath.exists():
                import httpx
                r = httpx.get(SEG_URL, timeout=60, follow_redirects=True)
                r.raise_for_status()
                mpath.write_bytes(r.content)
            opts = vision.ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=str(mpath)),
                                                running_mode=vision.RunningMode.VIDEO,
                                                output_confidence_masks=True)
            self._mp = mp
            self._seg = vision.ImageSegmenter.create_from_options(opts)
        except Exception as e:  # noqa: BLE001
            print("segmenter unavailable:", e)
            self.ok = False

    def mask(self, frame: np.ndarray, t: float) -> np.ndarray | None:
        self._load()
        if not self.ok:
            return None
        small = cv2.resize(frame, (256, int(256 * self.H / self.W)) if self.H >= self.W else
                           (int(256 * self.W / self.H), 256), interpolation=cv2.INTER_AREA)
        self.ts = max(self.ts + 1, int(t * 1000))
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(small))
        res = self._seg.segment_for_video(img, self.ts)
        m = res.confidence_masks[0].numpy_view().astype(np.float32)
        if m.ndim == 3:
            m = m[..., 0]
        if self.prev is not None and self.prev.shape == m.shape:
            m = 0.6 * m + 0.4 * self.prev
        self.prev = m
        m = cv2.resize(m, (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        m = np.clip((m - 0.35) / 0.3, 0, 1)
        return cv2.GaussianBlur(m, (0, 0), 2.0)


def mask_box(m: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(m > 0.5)
    if len(xs) < 50:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def clean_plate(frame: np.ndarray, m: np.ndarray) -> np.ndarray:
    """İnsanı silinmiş fon (təxmini): kiçik ölçüdə inpaint + bulanıqlıq."""
    h, w = frame.shape[:2]
    sw, sh = w // 4, h // 4
    small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
    mm = (cv2.resize(m, (sw, sh)) > 0.2).astype(np.uint8) * 255
    mm = cv2.dilate(mm, np.ones((9, 9), np.uint8))
    fill = cv2.inpaint(small, mm, 7, cv2.INPAINT_TELEA)
    fill = cv2.GaussianBlur(fill, (0, 0), 3)
    return cv2.resize(fill, (w, h), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------- video giriş/çıxış
class Reader:
    def __init__(self, path, W, H, fps, start: float = 0.0, dur: float | None = None):
        cmd = ["ffmpeg", "-v", "error"]
        if start:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", str(path)]
        if dur:
            cmd += ["-t", f"{dur:.3f}"]
        cmd += ["-vf", f"scale={W}:{H},fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * H * 3 * 2)
        self.n = W * H * 3
        self.W, self.H = W, H

    def read(self) -> np.ndarray | None:
        buf = self.p.stdout.read(self.n)
        if not buf or len(buf) < self.n:
            return None
        return np.frombuffer(buf, np.uint8).reshape(self.H, self.W, 3).copy()

    def close(self):
        try:
            self.p.stdout.close()
            self.p.kill()
        except Exception:  # noqa: BLE001
            pass


class Writer:
    def __init__(self, out, W, H, fps, audio_src=None, preset="veryfast", crf="20"):
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
               "-i", "-"]
        if audio_src:
            cmd += ["-i", str(audio_src), "-map", "0:v", "-map", "1:a?", "-c:a", "copy"]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-shortest", str(out)]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame: np.ndarray):
        self.p.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self):
        self.p.stdin.close()
        err = self.p.stderr.read().decode(errors="ignore")
        if self.p.wait() != 0:
            raise RuntimeError(f"effekt render xətası: {err[-800:]}")


def rng_for(*key) -> random.Random:
    return random.Random(hash(key) & 0xFFFFFFFF)
