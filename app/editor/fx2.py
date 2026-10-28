"""Dinamik montaj effektləri (20+): bir çəkilişdən "çox kameralı", qrafikli, jurnal üslublu video.

Hər effekt: fn(S, f, t, e) -> kadr.  S — vəziyyət (ölçü, maska), f — cari kadr (RGB uint8), t — effektin
başlanğıcından keçən vaxt, e — {"type","start","end","dur","params","cache"}.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from editor.fxcore import (drop, ACCENT, ACCENT2, INK, PAPER, Reader, Segmenter, Writer, az_upper, back_out, blur,
                           clamp01, clean_plate, darken, ease_in, ease_io, ease_out, env, fit_font, font, gradient,
                           mask_box, noise_tex, over, over_mask, resize, rgba_from, rng_for, rot_quad, rounded_mask,
                           shadow, text_rgba, text_size, vignette, warp_quad, wrap, zoom)

EFFECTS: dict[str, callable] = {}
NEEDS_MASK = {"headline_wall", "paper_cut", "doodle_boil", "vertigo", "frame_break", "green_screen",
              "cover_story", "digital_zoom", "pull_back"}


def fx(name, mask=False):
    def deco(fn):
        EFFECTS[name] = fn
        if mask:
            NEEDS_MASK.add(name)
        return fn
    return deco


class State:
    def __init__(self, W, H, fps):
        self.W, self.H, self.fps = W, H, fps
        self.vertical = H > W
        self.seg = Segmenter(W, H)
        self.u = min(W, H) / 1080  # ölçü vahidi
        self._mask = None
        self.t = 0.0

    def mask(self, f):
        if self._mask is None:
            m = self.seg.mask(f, self.t)
            self._mask = m if m is not None else self._fallback_mask()
        return self._mask

    def _fallback_mask(self):
        # seqmentasiya yoxdursa — mərkəzdə ellips (təxmini "insan")
        m = np.zeros((self.H, self.W), np.float32)
        cv2.ellipse(m, (self.W // 2, int(self.H * .62)), (int(self.W * .28), int(self.H * .42)), 0, 0, 360, 1, -1)
        return cv2.GaussianBlur(m, (0, 0), 25)


def P(e, key, default=None):
    v = (e.get("params") or {}).get(key)
    return default if v in (None, "", []) else v


def person_center(S, f, e):
    """Effekt boyu sabit qalan insan mərkəzi və üz nöqtəsi (ilk kadrdan)."""
    c = e["cache"]
    if "pc" not in c:
        b = mask_box(S.mask(f))
        if b:
            x0, y0, x1, y1 = b
            c["pc"] = ((x0 + x1) / 2, (y0 + y1) / 2)
            c["face"] = ((x0 + x1) / 2, y0 + (y1 - y0) * (0.18 if S.vertical else 0.22))
            c["box"] = b
        else:
            c["pc"] = (S.W / 2, S.H * .55)
            c["face"] = (S.W / 2, S.H * .4)
            c["box"] = (int(S.W * .25), int(S.H * .25), int(S.W * .75), S.H)
    return c["pc"], c["face"], c["box"]


# ================================================================ 1. digital zoom
@fx("digital_zoom")
def digital_zoom(S, f, t, e):
    _, face, _ = person_center(S, f, e)
    s = float(P(e, "scale", 1.45))
    # anında "ikinci kamera" + çox yüngül sürüşmə
    out = zoom(f, s + 0.02 * t, face[0], face[1] + S.H * .08)
    u = S.u
    c = (255, 255, 255)
    L, m = int(70 * u), int(46 * u)
    for (x, y, dx, dy) in ((m, m, 1, 1), (S.W - m, m, -1, 1), (m, S.H - m, 1, -1), (S.W - m, S.H - m, -1, -1)):
        cv2.line(out, (x, y), (x + dx * L, y), c, max(2, int(4 * u)))
        cv2.line(out, (x, y), (x, y + dy * L), c, max(2, int(4 * u)))
    if int(t * 2) % 2 == 0:
        cv2.circle(out, (int(m + 40 * u), int(m + 60 * u)), int(10 * u), (255, 40, 40), -1)
    lab = text_rgba(P(e, "label", "KAMERA B"), font("mono", 34 * u), fill=(255, 255, 255))
    return over(out, lab, int(m + 62 * u), int(m + 38 * u))


# ================================================================ 2. speed ramp
@fx("speed_ramp")
def speed_ramp(S, f, t, e):
    buf = e["cache"].get("buf")
    if not buf:
        return f
    p = clamp01(t / e["dur"])
    # əvvəl sürətli, sonra yavaş (zaman əyrisi)
    q = 1 - (1 - p) ** 2.2
    i = min(len(buf) - 1, int(q * (len(buf) - 1)))
    out = buf[i]
    speed = 2.2 * (1 - p) ** 1.2
    if speed > 1.2 and i > 0:
        out = cv2.addWeighted(out, 0.55, buf[i - 1], 0.45, 0)
    if p < .15:
        out = zoom(out, 1 + .06 * (1 - p / .15))
    return out


# ================================================================ 3. freeze frame
@fx("freeze_frame")
def freeze_frame(S, f, t, e):
    c = e["cache"]
    if "frozen" not in c:
        c["frozen"] = f.copy()
        c["bg"] = darken(blur(f, 30), .45)
    fr, u = c["frozen"], S.u
    p = ease_out(t / .35)
    if p >= 1 and "final" in c:
        out = c["final"].copy()
        return _freeze_text(S, out, t, e, c["final_y"], c["final_big"])
    s = 1 - .2 * p
    ang = -3.5 * p
    cw, ch = int(S.W * s), int(S.H * s)
    bw = int(16 * u)
    card = np.full((ch + 2 * bw, cw + 2 * bw, 3), 250, np.uint8)
    card[bw:bw + ch, bw:bw + cw] = resize(cv2.cvtColor(cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
                                          if p > .5 else fr, cw, ch)
    rgba = np.dstack([card, np.full(card.shape[:2], 255, np.uint8)])
    M = cv2.getRotationMatrix2D((rgba.shape[1] / 2, rgba.shape[0] / 2), ang, 1)
    big = int(max(rgba.shape[:2]) * 1.1)
    M[0, 2] += (big - rgba.shape[1]) / 2
    M[1, 2] += (big - rgba.shape[0]) / 2
    rot = cv2.warpAffine(rgba, M, (big, big))
    out = c["bg"].copy() if p > .02 else fr.copy()
    x, y = (S.W - big) // 2, int((S.H - big) / 2 - S.H * .05 * p)
    out = drop(out, rot, x, y, int(22 * u), .55, int(10 * u), int(20 * u), 1.0) if p > .02 else over(out, rot, x, y)
    if p >= 1:
        c["final"], c["final_y"], c["final_big"] = out.copy(), y, big
    return _freeze_text(S, out, t, e, y, big) if p > .6 else out


def _freeze_text(S, out, t, e, y, big):
    u = S.u
    if True:
        badge = text_rgba("|| DONDURULDU", font("mono", 30 * u), fill=(255, 255, 255), bg=(230, 40, 40),
                          bg_pad=(14, 8), radius=6)
        out = over(out, badge, int(S.W * .1), int(y + big * .08), clamp01((t - .35) / .2))
        note = P(e, "text", "")
        if note:
            fn = font("hand", 74 * u)
            lines = wrap(note, fn, int(S.W * .8))[:3]
            yy = int(y + big * (.92 if S.vertical else .86))
            for i, ln in enumerate(lines):
                im = text_rgba(ln, fn, fill=ACCENT, stroke=int(3 * u), stroke_fill=(0, 0, 0))
                out = over(out, im, (S.W - im.shape[1]) // 2, yy + i * int(80 * u), clamp01((t - .5 - i * .2) / .25))
    return out


# ================================================================ 4. checklist
@fx("checklist")
def checklist(S, f, t, e):
    u = S.u
    items = [str(x) for x in (P(e, "items") or ["Kurgu", "Effektlər", "Altyazı", "Render"])][:6]
    title = str(P(e, "title", "Plan"))
    w = int(S.W * (.74 if S.vertical else .4))
    fi, ft = font("inter", 46 * u), font("inter_black", 56 * u)
    rows = []
    for it in items:
        rows += [(it, ln) for ln in wrap(it, fi, w - int(150 * u))[:2]]
    lh = int(72 * u)
    h = int(150 * u) + lh * len(items) + int(40 * u)
    pin, pout = env(t, e["dur"], .35, .25)
    a = ease_out(pin) * (1 - ease_in(pout))
    out = darken(f, 1 - .25 * a)
    card = np.zeros((h, w, 4), np.uint8)
    card[..., :3] = 255
    card[..., 3] = rounded_mask(w, h, int(34 * u))
    card = over(card, text_rgba(title, ft, fill=INK), int(44 * u), int(36 * u))
    n = len(items)
    for i, it in enumerate(items):
        y = int(140 * u) + i * lh
        when = .35 + (e["dur"] - .8) * (i + 1) / (n + 1)
        done = clamp01((t - when) / .18)
        bx, bs = int(44 * u), int(44 * u)
        cv2.rectangle(card, (bx, y), (bx + bs, y + bs), (200, 200, 205, 255) if done < 1 else ACCENT2 + (255,),
                      -1 if done >= 1 else max(2, int(4 * u)))
        if done > 0:
            box = card[y:y + bs, bx:bx + bs]
            box[..., :3] = (np.array(ACCENT2) * done + 255 * (1 - done)).astype(np.uint8)
            if done > .5:
                pts = np.array([[bx + bs * .22, y + bs * .52], [bx + bs * .43, y + bs * .74],
                                [bx + bs * .8, y + bs * .28]], np.int32)
                cv2.polylines(card, [pts], False, (255, 255, 255, 255), max(3, int(6 * u)), cv2.LINE_AA)
        col = INK if done < 1 else (150, 150, 155)
        im = text_rgba(it, fi, fill=col)
        card = over(card, im, bx + bs + int(26 * u), y - int(4 * u))
        if done >= 1:
            cv2.line(card, (bx + bs + int(30 * u), y + bs // 2), (bx + bs + int(30 * u) + im.shape[1] - 10, y + bs // 2),
                     (150, 150, 155, 255), max(2, int(3 * u)))
    x = (S.W - w) // 2
    y = int(S.H * (.2 if S.vertical else .2) + (1 - ease_out(pin)) * S.H * .5 - ease_in(pout) * S.H * .6)
    return drop(out, card, x, y, int(30 * u), .5, 0, int(10 * u), a)


# ================================================================ 5. headline wall
@fx("headline_wall")
def headline_wall(S, f, t, e):
    u = S.u
    words = P(e, "words") or str(P(e, "text", "BU GÜN ÖYRƏNİRİK")).split()
    words = [az_upper(str(w)) for w in words][:6]
    pin, pout = env(t, e["dur"], .5, .3)
    m = S.mask(f)
    out = darken(f, .55 - .2 * ease_out(pin))
    layer = np.zeros((S.H, S.W, 4), np.uint8)
    n = len(words)
    rows_h = S.H * (.75 if S.vertical else .85) / max(1, n)
    y0 = S.H * (.12 if S.vertical else .07)
    for i, w in enumerate(words):
        fn = fit_font(w, "anton", int(S.W * .95), int(rows_h * 1.1))
        im = text_rgba(w, fn, fill=ACCENT if i % 2 else (255, 255, 255))
        delay = i * .07
        k = ease_out((t - delay) / .45) * (1 - ease_in(pout))
        d = 1 if i % 2 else -1
        x = int((S.W - im.shape[1]) / 2 + d * (1 - k) * S.W)
        y = int(y0 + i * rows_h + (rows_h - im.shape[0]) / 2)
        layer = over(layer, im, x, y)
    out = over(out, layer, 0, 0)
    # insan yazının önündə
    return over_mask(out, f, m * (1 - .0))


# ================================================================ 6. chart grow
@fx("chart_grow")
def chart_grow(S, f, t, e):
    u = S.u
    vals = [float(v) for v in (P(e, "values") or [20, 45, 80])][:6]
    labels = [str(x) for x in (P(e, "labels") or [str(i + 1) for i in range(len(vals))])][:len(vals)]
    while len(labels) < len(vals):
        labels.append("")
    unit, title = str(P(e, "unit", "")), str(P(e, "title", ""))
    pin, pout = env(t, e["dur"], .3, .25)
    a = ease_out(pin) * (1 - ease_in(pout))
    w = int(S.W * (.86 if S.vertical else .5))
    h = int(w * .82)
    card = np.zeros((h, w, 4), np.uint8)
    card[..., :3] = (16, 18, 24)
    card[..., 3] = (rounded_mask(w, h, int(36 * u)) * .92).astype(np.uint8)
    pad = int(48 * u)
    if title:
        card = over(card, text_rgba(title, fit_font(title, "inter_black", w - 2 * pad, int(52 * u)),
                                    fill=(255, 255, 255)), pad, int(30 * u))
    top, base = int(140 * u), h - int(90 * u)
    n = len(vals)
    gap = int(28 * u)
    bw = (w - 2 * pad - gap * (n - 1)) // n
    vmax = max(vals) or 1
    grow = ease_out((t - .25) / .9)
    for i, v in enumerate(vals):
        k = clamp01(grow * 1.15 - i * .08)
        bh = int((base - top) * (v / vmax) * ease_out(k))
        x = pad + i * (bw + gap)
        col = ACCENT if v == vmax else (110, 120, 140)
        if bh > 2:
            cv2.rectangle(card, (x, base - bh), (x + bw, base), col + (255,), -1)
        num = v * ease_out(k)
        txt = (f"{num:.0f}" if vmax >= 10 else f"{num:.1f}") + unit
        im = text_rgba(txt, fit_font(txt, "inter_black", bw + 20, int(44 * u)), fill=(255, 255, 255))
        card = over(card, im, x + (bw - im.shape[1]) // 2, base - bh - im.shape[0] - int(6 * u), clamp01(k * 3))
        lb = text_rgba(labels[i], fit_font(labels[i], "inter", bw + 10, int(34 * u)), fill=(170, 175, 190))
        card = over(card, lb, x + (bw - lb.shape[1]) // 2, base + int(14 * u))
    out = darken(f, 1 - .35 * a)
    x = (S.W - w) // 2
    y = int(S.H * (.5 if S.vertical else .3) + (1 - ease_out(pin)) * S.H * .3)
    return drop(out, card, x, y, int(30 * u), .5, 0, int(10 * u), a)


# ================================================================ 7. glass type
@fx("glass_type")
def glass_type(S, f, t, e):
    txt = az_upper(str(P(e, "text", "VİDEO")))
    c = e["cache"]
    lines = c.get("lines")
    if lines is None:
        words = txt.split()
        lines = words[:3] if S.vertical else [txt]
        c["lines"] = lines
    if "tm" not in c:
        tm = np.zeros((S.H, S.W), np.uint8)
        lh = min(S.H * .26, S.H * .8 / max(1, len(lines))) if S.vertical else S.H * .5
        y = (S.H - lh * len(lines)) / 2
        for ln in lines:
            fn = fit_font(ln, "anton", int(S.W * .92), int(lh * 1.05))
            im = text_rgba(ln, fn)
            a = im[..., 3]
            x0 = (S.W - a.shape[1]) // 2
            y0 = int(y + (lh - a.shape[0]) / 2)
            sub = tm[max(0, y0):y0 + a.shape[0], max(0, x0):x0 + a.shape[1]]
            sub[:] = np.maximum(sub, a[:sub.shape[0], :sub.shape[1]])
            y += lh
        c["tm"] = tm.astype(np.float32) / 255
    pin, pout = env(t, e["dur"], .4, .3)
    s = 1.18 - .18 * ease_out(pin) + .5 * ease_in(pout)
    tm = zoom(c["tm"], s, border=cv2.BORDER_CONSTANT)
    bg = darken(blur(f, 25), .35)
    inside = cv2.convertScaleAbs(zoom(f, 1.08), alpha=1.15, beta=10)
    out = over_mask(bg, inside, tm)
    edge = cv2.morphologyEx((tm > .5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    out[edge > 0] = (255, 255, 255)
    k = ease_out(pin) * (1 - ease_in(pout))
    return cv2.addWeighted(out, k, f, 1 - k, 0) if k < 1 else out


# ================================================================ 8. phone mockup
@fx("phone_mockup")
def phone_mockup(S, f, t, e):
    u = S.u
    c = e["cache"]
    if "bg" not in c:
        c["bg"] = vignette(gradient(S.W, S.H, (28, 30, 40), (8, 8, 12)), .5)
    pin, pout = env(t, e["dur"], .4, .3)
    k = ease_out(pin) * (1 - ease_in(pout))
    ph = int(S.H * (.78 if S.vertical else .86))
    pw = int(ph * 9 / 19.5)
    sw, sh = pw - int(26 * u), ph - int(26 * u)
    # ekran: kadrı doldur
    src_ar = S.W / S.H
    if sw / sh > src_ar:
        scr = resize(f, sw, int(sw / src_ar))
        yo = (scr.shape[0] - sh) // 2
        scr = scr[yo:yo + sh]
    else:
        scr = resize(f, int(sh * src_ar), sh)
        xo = (scr.shape[1] - sw) // 2
        scr = scr[:, xo:xo + sw]
    body = np.zeros((ph, pw, 4), np.uint8)
    body[..., :3] = (12, 12, 14)
    body[..., 3] = rounded_mask(pw, ph, int(86 * u))
    inner = np.dstack([scr, rounded_mask(sw, sh, int(70 * u))])
    body = over(body, inner, int(13 * u), int(13 * u))
    isl_w, isl_h = int(pw * .3), int(34 * u)
    cv2.rectangle(body, (pw // 2 - isl_w // 2 + isl_h // 2, int(34 * u)),
                  (pw // 2 + isl_w // 2 - isl_h // 2, int(34 * u) + isl_h), (0, 0, 0, 255), -1)
    cv2.circle(body, (pw // 2 - isl_w // 2 + isl_h // 2, int(34 * u) + isl_h // 2), isl_h // 2, (0, 0, 0, 255), -1)
    cv2.circle(body, (pw // 2 + isl_w // 2 - isl_h // 2, int(34 * u) + isl_h // 2), isl_h // 2, (0, 0, 0, 255), -1)
    yaw = 14 * math.sin(t * 1.3) * k
    sc = 1.35 - .35 * ease_out(pin)
    quad = rot_quad(S.W / 2, S.H / 2, pw * sc, ph * sc, yaw_deg=yaw, pitch_deg=4 * k)
    rgb, a = warp_quad(body, quad, S.W, S.H)
    bg = cv2.addWeighted(c["bg"], k, darken(blur(f, 30), .5), 1 - k, 0)
    sh_a = cv2.GaussianBlur(a, (0, 0), 30 * u) * .6
    bg = over_mask(bg, np.zeros_like(bg), np.roll(sh_a, int(30 * u), 0))
    out = over_mask(bg, rgb, a)
    return cv2.addWeighted(out, k, f, 1 - k, 0) if k < .999 else out


# ================================================================ 9. screen wall
@fx("screen_wall")
def screen_wall(S, f, t, e):
    cols, rows = (3, 4) if S.vertical else (4, 3)
    gap = int(14 * S.u)
    tw, th = (S.W - gap * (cols + 1)) // cols, (S.H - gap * (rows + 1)) // rows
    tile = resize(f, tw, th)
    wall = np.full((S.H, S.W, 3), 10, np.uint8)
    rng = rng_for("wall", e["start"])
    cc, cr = cols // 2, rows // 2
    for r in range(rows):
        for ci in range(cols):
            x, y = gap + ci * (tw + gap), gap + r * (th + gap)
            tl = tile
            if (r, ci) != (cr, cc):
                b = .45 + .35 * rng.random()
                tl = darken(tile, b)
                if rng.random() < .3:
                    tl = cv2.cvtColor(cv2.cvtColor(tl, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
            wall[y:y + th, x:x + tw] = tl
    # mərkəz plitəyə yaxınlaşmadan uzaqlaşıb yenə yaxınlaş
    cx, cy = gap + cc * (tw + gap) + tw / 2, gap + cr * (th + gap) + th / 2
    zmax = S.W / tw
    d = e["dur"]
    p_out = ease_io(t / (d * .4))
    p_in = ease_io((t - d * .7) / (d * .3))
    z = zmax - (zmax - 1) * p_out + (zmax - 1) * p_in
    k = 1 - (z - 1) / (zmax - 1)  # 0 = tam yaxın, 1 = tam uzaq
    ox = cx + (S.W / 2 - cx) * k
    oy = cy + (S.H / 2 - cy) * k
    return zoom(wall, z, ox, oy, border=cv2.BORDER_CONSTANT)


# ================================================================ 10. zoom-through cut (keçid)
@fx("zoom_through")
def zoom_through(S, f, t, e):
    d = e["dur"]
    mid = d / 2
    if t < mid:
        p = ease_in(t / mid)
        s = 1 + 3 * p
    else:
        p = 1 - ease_out((t - mid) / mid)
        s = 1 + 3 * p
    out = zoom(f, s)
    if p > .05:  # radial bulanıqlıq imitasiyası
        acc = out.astype(np.float32)
        for i in range(1, 4):
            acc += zoom(out, 1 + .04 * i * p).astype(np.float32)
        out = (acc / 4).astype(np.uint8)
    if abs(t - mid) < .06:
        out = cv2.addWeighted(out, .4, np.full_like(out, 255), .6, 0)
    return out


# ================================================================ 11. kinetic text
@fx("kinetic_text")
def kinetic_text(S, f, t, e):
    words = [az_upper(w) for w in str(P(e, "text", "DİQQƏT")).split()][:8]
    if not words:
        return f
    d = e["dur"]
    step = d / len(words)
    i = min(len(words) - 1, int(t / step))
    p = (t - i * step) / step
    out = darken(f, .45)
    w = words[i]
    fn = fit_font(w, "anton", int(S.W * .88), int(S.H * (.2 if S.vertical else .35)))
    col = ACCENT if i % 2 == 0 else (255, 255, 255)
    im = text_rgba(w, fn, fill=col)
    s = back_out(p / .35) * (1 + .04 * p)
    im = resize(im, im.shape[1] * s, im.shape[0] * s) if s > .05 else im
    return over(out, im, (S.W - im.shape[1]) // 2, (S.H - im.shape[0]) // 2)


# ================================================================ 12. paper cut
@fx("paper_cut")
def paper_cut(S, f, t, e):
    u = S.u
    c = e["cache"]
    if "paper" not in c:
        paper = noise_tex(S.W, S.H, PAPER, 9, 3)
        rng = rng_for("paper", e["start"])
        for col, yy in ((ACCENT, .18), ((255, 120, 170), .74)):
            tape = np.zeros((int(S.H * .12), int(S.W * 1.4), 4), np.uint8)
            tape[..., :3] = col
            tape[..., 3] = 235
            M = cv2.getRotationMatrix2D((tape.shape[1] / 2, tape.shape[0] / 2), rng.uniform(-6, 6), 1)
            tape = cv2.warpAffine(tape, M, (tape.shape[1], tape.shape[0]))
            paper = over(paper, tape, -int(S.W * .2), int(S.H * yy))
        c["paper"] = paper
    m = S.mask(f)
    step = int(t * 8)  # stop-motion: saniyədə 8 dəfə titrəyiş
    rng = rng_for("pc", e["start"], step)
    ang = -2 + rng.uniform(-1.2, 1.2)
    s = .9 + rng.uniform(-.008, .008)
    _, _, box = person_center(S, f, e)
    cx = (box[0] + box[2]) / 2
    person = rgba_from(f, m)
    border = cv2.dilate((m > .5).astype(np.uint8), np.ones((int(26 * u) | 1, int(26 * u) | 1), np.uint8))
    cut = np.zeros((S.H, S.W, 4), np.uint8)
    cut[..., :3] = 255
    cut[..., 3] = border * 255
    cut = over(cut, person, 0, 0)
    M = cv2.getRotationMatrix2D((cx, S.H * .6), ang, s)
    M[0, 2] += S.W / 2 - cx
    cut = cv2.warpAffine(cut, M, (S.W, S.H))
    out = c["paper"].copy()
    out = drop(out, cut, 0, 0, int(14 * u), .45, int(12 * u), int(20 * u))
    # "jurnaldan kəsilmiş" hərflər
    txt = az_upper(str(P(e, "text", "DƏRGİDƏN")))[:14]
    fonts = ["anton", "serif", "mont", "mono", "inter_black"]
    cols = [((255, 255, 255), INK), (INK, (255, 255, 255)), (ACCENT2, (255, 255, 255)), (ACCENT, INK),
            ((255, 120, 170), INK)]
    lsz = int(min(110 * u, S.W * .9 / max(4, len(txt)) * 1.25))
    tiles = []
    for i, ch in enumerate(txt):
        if ch == " ":
            tiles.append(None)
            continue
        r2 = rng_for("ch", e["start"], i, step // 2)
        bgc, fgc = cols[r2.randrange(len(cols))]
        im = text_rgba(ch, font(fonts[r2.randrange(len(fonts))], lsz * r2.uniform(.85, 1.1)), fill=fgc, bg=bgc,
                       bg_pad=(8, 4))
        M2 = cv2.getRotationMatrix2D((im.shape[1] / 2, im.shape[0] / 2), r2.uniform(-9, 9), 1)
        tiles.append(cv2.warpAffine(im, M2, (im.shape[1], im.shape[0])))
    tw = sum(x.shape[1] if x is not None else lsz // 2 for x in tiles)
    x = (S.W - tw) // 2
    y = int(S.H * (.78 if S.vertical else .75))
    k = ease_out(t / .3)
    for im in tiles:
        if im is None:
            x += lsz // 2
            continue
        out = over(out, im, x, y + int((1 - k) * 200 * u), k)
        x += im.shape[1] - int(4 * u)
    return out


# ================================================================ 13. doodle boil
@fx("doodle_boil")
def doodle_boil(S, f, t, e):
    u = S.u
    m = S.mask(f)
    step = int(t * 10)
    rng = rng_for("doodle", e["start"], step)
    out = f.copy()
    bm = cv2.dilate((m > .5).astype(np.uint8), np.ones((int(30 * u) | 1,) * 2, np.uint8))
    cnts, _ = cv2.findContours(bm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    k = ease_out(t / .4)
    col = tuple(int(x) for x in P(e, "color", (255, 255, 255)))
    for cnt in sorted(cnts, key=cv2.contourArea, reverse=True)[:1]:
        pts = cv2.approxPolyDP(cnt, 6 * u, True)[:, 0, :].astype(np.float32)
        n = len(pts)
        show = max(2, int(n * k))
        pts = pts[:show] + np.array([[rng.uniform(-6, 6) * u, rng.uniform(-6, 6) * u] for _ in range(show)], np.float32)
        cv2.polylines(out, [pts.astype(np.int32)], k >= 1, col, max(3, int(8 * u)), cv2.LINE_AA)
    # ulduzlar / qığılcımlar
    _, face, box = person_center(S, f, e)
    for j, (dx, dy) in enumerate(((-.35, -.12), (.33, -.05), (.3, .25))):
        if t < .25 + j * .15:
            continue
        cx = int(face[0] + dx * S.W + rng.uniform(-4, 4) * u)
        cy = int(face[1] + dy * S.H * .5 + rng.uniform(-4, 4) * u)
        r = int((38 + 8 * math.sin(t * 6 + j)) * u)
        for a in range(4):
            ang = a * math.pi / 4 + rng.uniform(-.1, .1)
            p1 = (int(cx + r * math.cos(ang)), int(cy + r * math.sin(ang)))
            p2 = (int(cx - r * math.cos(ang)), int(cy - r * math.sin(ang)))
            cv2.line(out, p1, p2, ACCENT, max(3, int(6 * u)), cv2.LINE_AA)
    note = P(e, "text", "")
    if note:
        im = text_rgba(str(note), font("hand", 90 * u), fill=(255, 255, 255), stroke=int(4 * u), stroke_fill=INK)
        M = cv2.getRotationMatrix2D((im.shape[1] / 2, im.shape[0] / 2), rng.uniform(-4, 4) - 4, 1)
        im = cv2.warpAffine(im, M, (im.shape[1], im.shape[0]))
        out = over(out, im, (S.W - im.shape[1]) // 2, int(max(20 * u, box[1] - im.shape[0] - 30 * u)), k)
    return out


# ================================================================ 14. vertigo (dolly zoom)
@fx("vertigo")
def vertigo(S, f, t, e):
    m = S.mask(f)
    fill = clean_plate(f, m)
    hole = cv2.GaussianBlur(cv2.dilate((m > .2).astype(np.float32), np.ones((41, 41), np.uint8)), (0, 0), 8)
    plate = over_mask(f, fill, hole)
    p = ease_io(t / e["dur"])
    _, face, _ = person_center(S, f, e)
    bg = zoom(plate, 1 + .45 * p, face[0], face[1])
    return over_mask(bg, f, m)


# ================================================================ 15. frame break
@fx("frame_break")
def frame_break(S, f, t, e):
    u = S.u
    c = e["cache"]
    m = S.mask(f)
    _, _, box = person_center(S, f, e)
    pin, pout = env(t, e["dur"], .45, .3)
    k = ease_out(pin) * (1 - ease_in(pout))
    s = 1 - .3 * k
    fw, fh = int(S.W * s), int(S.H * s)
    # çərçivə aşağı sürüşür ki, baş yuxarı haşiyədən çölə çıxsın
    head_y = box[1] + (box[3] - box[1]) * .12
    target_top = head_y + (box[3] - box[1]) * .1
    x0 = (S.W - fw) // 2
    y0 = int((S.H - fh) / 2 * (1 - k) + max((S.H - fh) / 2, min(S.H - fh - 20 * u, target_top)) * k)
    if "bg" not in c:
        c["bg"] = vignette(darken(blur(f, 40), .35), .6)
    out = cv2.addWeighted(c["bg"], k, f, 1 - k, 0)
    bw = max(3, int(12 * u))
    cv2.rectangle(out, (x0 - bw, y0 - bw), (x0 + fw + bw, y0 + fh + bw), (255, 255, 255), -1)
    # çərçivənin içi: kadr yerində (kəsilmiş pəncərə)
    out[y0:y0 + fh, x0:x0 + fw] = f[y0:y0 + fh, x0:x0 + fw]
    # insan tam ölçüdə — çərçivədən çölə çıxır
    return over_mask(out, f, m * k + (1 - k) * 0)


# ================================================================ 16. pull-back reveal
@fx("pull_back")
def pull_back(S, f, t, e):
    _, face, _ = person_center(S, f, e)
    p = ease_io(t / e["dur"])
    s = 2.4 - 1.4 * p
    ang = 5 * (1 - p)
    out = zoom(f, s, face[0] + (S.W / 2 - face[0]) * p, face[1] + (S.H / 2 - face[1]) * p, ang)
    if p < .5:
        out = cv2.addWeighted(out, .7, zoom(out, 1.03), .3, 0)
    return vignette(out, .4 * (1 - p))


# ================================================================ 17. green screen (fon dəyişimi)
@fx("green_screen")
def green_screen(S, f, t, e):
    u = S.u
    c = e["cache"]
    if "bg" not in c:
        bg = gradient(S.W, S.H, (10, 14, 40), (2, 4, 12))
        hz = int(S.H * .72)
        for i in range(-12, 13):  # perspektiv döşəmə tor
            cv2.line(bg, (S.W // 2 + i * int(S.W * .08), hz), (S.W // 2 + i * int(S.W * .6), S.H), (40, 70, 160), 2,
                     cv2.LINE_AA)
        for j in range(1, 10):
            yy = hz + int((S.H - hz) * (j / 10) ** 1.8)
            cv2.line(bg, (0, yy), (S.W, yy), (40, 70, 160), 2, cv2.LINE_AA)
        word = az_upper(str(P(e, "text", "")))
        if word:
            fn = fit_font(word, "anton", int(S.W * .96), int(S.H * .25))
            im = text_rgba(word, fn, fill=(40, 60, 120))
            bg = over(bg, im, (S.W - im.shape[1]) // 2, int(S.H * .14))
        c["bg"] = bg
    _, face, _ = person_center(S, f, e)
    bg = c["bg"].copy()
    if "glow" not in c:
        g = np.zeros((S.H // 4, S.W // 4), np.float32)
        cv2.circle(g, (int(face[0] / 4), int((face[1] + S.H * .05) / 4)), int(S.W * .38 / 4), 1, -1)
        c["glow"] = cv2.resize(cv2.GaussianBlur(g, (0, 0), 20 * u), (S.W, S.H))
    glow = c["glow"] * (.55 + .1 * math.sin(t * 3))
    bg = over_mask(bg, np.full_like(bg, (80, 160, 255)), glow)
    m = S.mask(f)
    out = over_mask(bg, f, m)
    if t < .2:  # qlitç girişi
        sh = int(30 * u * (1 - t / .2))
        out[..., 0] = np.roll(out[..., 0], sh, 1)
        out[..., 2] = np.roll(out[..., 2], -sh, 1)
    return out


# ================================================================ 18. cover story (jurnal üz qabığı)
@fx("cover_story")
def cover_story(S, f, t, e):
    u = S.u
    c = e["cache"]
    m = S.mask(f)
    k = ease_out(t / .35)
    photo = cv2.convertScaleAbs(f, alpha=1.12, beta=-8)
    title = az_upper(str(P(e, "title", "NİHAT")))
    if "mast" not in c:
        fn = fit_font(title, "serif", int(S.W * .92), int(S.H * (.15 if S.vertical else .24)))
        c["mast"] = text_rgba(title, fn, fill=(255, 255, 255))
        lines = [az_upper(str(x)) for x in (P(e, "lines") or ["BİR ÇƏKİLİŞ", "20 EFFEKT", "SIFIR KAMERA"])][:4]
        c["lines"] = [text_rgba(ln, fit_font(ln, "mont", int(S.W * .42), int(54 * u)), fill=INK, bg=ACCENT,
                                bg_pad=(12, 4)) for ln in lines]
        issue = str(P(e, "issue", "OKTYABR 2026 · BAKI"))
        c["issue"] = text_rgba(issue, font("mono", 28 * u), fill=(255, 255, 255))
    out = photo.copy()
    mast = c["mast"]
    my = int(S.H * .045)
    layer = np.zeros((S.H, S.W, 4), np.uint8)
    layer = over(layer, mast, (S.W - mast.shape[1]) // 2, my)
    out = over(out, layer, 0, 0, k)
    out = over_mask(out, photo, m * .97)  # baş başlığın önündə
    out = over(out, c["issue"], (S.W - c["issue"].shape[1]) // 2, my + mast.shape[0] + int(6 * u), k)
    y = int(S.H * .55)
    for i, im in enumerate(c["lines"]):
        a = clamp01((t - .3 - i * .15) / .25)
        out = over(out, im, int(S.W * .05) - int((1 - a) * 200 * u), y + i * int(84 * u), a)
    # barkod
    bw, bh = int(170 * u), int(110 * u)
    bx, by = S.W - bw - int(46 * u), S.H - bh - int(70 * u)
    cv2.rectangle(out, (bx - 10, by - 10), (bx + bw + 10, by + bh + 34), (255, 255, 255), -1)
    rng = rng_for("bar", title)
    x = bx
    while x < bx + bw:
        w = rng.choice((2, 3, 5))
        if rng.random() > .4:
            cv2.rectangle(out, (x, by), (x + w - 1, by + bh), (0, 0, 0), -1)
        x += w + 1
    # səhifə girişi
    s = 1.06 - .06 * k
    return zoom(out, s, angle=1.5 * (1 - k))


# ================================================================ 19. orbit (3D fırlanma)
@fx("orbit")
def orbit(S, f, t, e):
    u = S.u
    c = e["cache"]
    if "bg" not in c:
        bg = gradient(S.W, S.H, (14, 16, 26), (2, 2, 6))
        rng = rng_for("stars", e["start"])
        for _ in range(140):
            cv2.circle(bg, (rng.randrange(S.W), rng.randrange(S.H)), rng.choice((1, 1, 2)),
                       (rng.randrange(120, 255),) * 3, -1)
        c["bg"] = bg
    pin, pout = env(t, e["dur"], .35, .35)
    k = ease_out(pin) * (1 - ease_in(pout))
    p = ease_io(t / e["dur"])
    yaw = (-38 + 76 * p) * k
    s = 1 - .22 * k
    w, h = S.W * s, S.H * s
    card = np.dstack([f, rounded_mask(S.W, S.H, int(48 * u))])
    quad = rot_quad(S.W / 2, S.H / 2, w, h, yaw_deg=yaw, pitch_deg=-6 * k, focal=2600 * u)
    rgb, a = warp_quad(card, quad, S.W, S.H)
    bg = c["bg"].copy()
    refl = np.flipud(rgb)
    ra = np.flipud(a) * .18 * k
    shift = int(S.H * .5 - (quad[:, 1].max() - S.H / 2))
    ra = np.roll(ra, -shift, 0)
    refl = np.roll(refl, -shift, 0)
    bg = over_mask(bg, refl, ra)
    out = over_mask(bg, rgb, a)
    return cv2.addWeighted(out, k, f, 1 - k, 0) if k < .999 else out


# ================================================================ 20. UI world
@fx("ui_world")
def ui_world(S, f, t, e):
    u = S.u
    c = e["cache"]
    if "bg" not in c:
        bg = gradient(S.W, S.H, (12, 16, 32), (4, 6, 14))
        for yy in range(0, S.H, int(40 * u)):
            for xx in range(0, S.W, int(40 * u)):
                cv2.circle(bg, (xx, yy), 1, (40, 50, 80), -1)
        c["bg"] = bg
    pin, pout = env(t, e["dur"], .45, .3)
    k = ease_out(pin) * (1 - ease_in(pout))
    title = str(P(e, "title", "jarvis — montaj"))
    s = 1 - .3 * k
    ww, wh = int(S.W * s), int(S.H * s)
    bar = int(54 * u)
    win = np.zeros((wh + bar, ww, 4), np.uint8)
    win[..., :3] = (30, 32, 42)
    win[..., 3] = rounded_mask(ww, wh + bar, int(26 * u))
    for i, col in enumerate(((255, 95, 87), (255, 189, 46), (40, 200, 64))):
        cv2.circle(win, (int((30 + i * 34) * u), bar // 2), int(10 * u), col + (255,), -1)
    tl = text_rgba(title, font("mono", 26 * u), fill=(190, 195, 210))
    win = over(win, tl, (ww - tl.shape[1]) // 2, (bar - tl.shape[0]) // 2)
    win[bar:, :, :3] = resize(f, ww, wh)
    win[bar:, :, 3] = np.minimum(win[bar:, :, 3], rounded_mask(ww, wh, int(26 * u))[:, :] | 0)
    drift = math.sin(t * 1.4) * 10 * u
    out = c["bg"].copy()
    wx, wy = (S.W - ww) // 2, int((S.H - wh - bar) / 2 + drift + (1 - k) * 40 * u)
    out = drop(out, win, wx, wy, int(30 * u), .6, 0, int(20 * u), k)
    # üzən kartlar
    items = [str(x) for x in (P(e, "items") or ["✓ Kurgu hazırdır", "✦ 20 effekt", "▶ Render 100%"])][:3]
    pos = ((.04, .08), (.55, .16), (.08, .82)) if S.vertical else ((.03, .1), (.7, .2), (.05, .8))
    for i, it in enumerate(items):
        a = clamp01((t - .35 - i * .2) / .3) * (1 - ease_in(pout))
        card = text_rgba(it, font("inter", 38 * u), fill=(255, 255, 255), bg=(50, 56, 80), bg_pad=(22, 14),
                         radius=int(18 * u))
        px = int(S.W * pos[i][0] + math.sin(t * 1.1 + i) * 12 * u)
        py = int(S.H * pos[i][1] + math.cos(t * 1.3 + i) * 12 * u + (1 - a) * 60 * u)
        out = drop(out, card, px, py, int(16 * u), .5, 0, int(8 * u), a)
    # kursor
    cx = int(S.W * (.8 - .25 * ease_io(t / e["dur"])))
    cy = int(S.H * (.62 - .1 * ease_io(t / e["dur"])))
    cur = np.array([[0, 0], [0, 46], [12, 34], [22, 56], [30, 52], [20, 31], [36, 31]], np.float32) * u
    cv2.fillPoly(out, [(cur + (cx, cy)).astype(np.int32)], (255, 255, 255), cv2.LINE_AA)
    cv2.polylines(out, [(cur + (cx, cy)).astype(np.int32)], True, (0, 0, 0), max(1, int(2 * u)), cv2.LINE_AA)
    return cv2.addWeighted(out, k, f, 1 - k, 0) if k < .999 else out


# ================================================================ render
INFO = {
    "digital_zoom": "bir çəkilişdən ikinci kamera bucağı (anında yaxın plan + kamera çərçivəsi)",
    "speed_ramp": "sürətlənib-yavaşlama keçidi (cümlələr arası, ≤0.9 s)",
    "freeze_frame": "kadr donur, polaroid kimi kiçilir, əl yazısı qeyd (params.text)",
    "checklist": "işarələnən siyahı kartı (params.title, params.items 2-5)",
    "headline_wall": "insanın ARXASINDA iri başlıq divarı (params.words 2-5 söz)",
    "chart_grow": "böyüyən sütun qrafik (params.title, labels, values, unit)",
    "glass_type": "video iri hərflərin içindən görünür (params.text 1-3 söz)",
    "phone_mockup": "video telefon ekranının içində, 3D yellənir",
    "screen_wall": "eyni videodan ekran divarı — uzaqlaşıb yenə yaxınlaşır",
    "zoom_through": "ekranın içinə uçub növbəti səhnəyə keçid (0.5–0.8 s)",
    "kinetic_text": "söz-söz iri sıçrayan yazı (params.text 2-6 söz)",
    "paper_cut": "insan jurnaldan kəsilmiş kimi, kağız fon, kəsik hərflər (params.text)",
    "doodle_boil": "insanın ətrafında titrəyən əl çəkilişi xətt + ulduzlar (params.text)",
    "vertigo": "fon uzaqlaşır/yaxınlaşır, insan yerində (dolly zoom)",
    "frame_break": "video çərçivəyə kiçilir, insan çərçivədən çölə çıxır",
    "pull_back": "üzdən başlayıb geri çəkilərək bütün səhnə açılır",
    "green_screen": "fon dəyişir — studiya/tor fonu (params.text arxada iri söz)",
    "cover_story": "jurnal üz qabığı: başlıq başın arxasında, yazılar, barkod (params.title, lines)",
    "orbit": "video kart kimi 3D fırlanır, ulduzlu fon",
    "ui_world": "video proqram pəncərəsində, ətrafda üzən UI kartlar (params.title, items)",
}


def render(src: str | Path, out: str | Path, effects: list[dict], progress=None) -> int:
    """src videosuna effektləri tətbiq edib out-a yazır (səs olduğu kimi qalır). Tətbiq olunan effekt sayını qaytarır."""
    from editor import media
    info = media.probe(src)
    W, H = info["width"] // 2 * 2, info["height"] // 2 * 2
    fps = round(info["fps"]) or 30
    dur = info["duration"]
    effs = []
    for e in sorted(effects, key=lambda x: float(x["start"])):
        if e.get("type") not in EFFECTS:
            continue
        a, b = max(0.0, float(e["start"])), min(dur, float(e["end"]))
        if e["type"] == "speed_ramp":
            b = min(b, a + .9)
        if b - a < .3 or (effs and a < effs[-1]["end"]):
            continue
        effs.append({**e, "start": a, "end": b, "dur": b - a, "cache": {}})
    if not effs:
        return 0
    S = State(W, H, fps)
    for e in effs:  # speed ramp üçün kadrları əvvəlcədən oxu
        if e["type"] == "speed_ramp":
            r = Reader(src, W, H, fps, e["start"], e["dur"] + .02)
            buf = []
            while (fr := r.read()) is not None:
                buf.append(fr)
            r.close()
            e["cache"]["buf"] = buf
    rd = Reader(src, W, H, fps)
    wr = Writer(out, W, H, fps, audio_src=src if info.get("has_audio") else None)
    n_total = max(1, int(dur * fps))
    i = 0
    ei = 0
    try:
        while (f := rd.read()) is not None:
            t = i / fps
            S.t, S._mask = t, None
            while ei < len(effs) and t >= effs[ei]["end"]:
                effs[ei]["cache"].clear()
                ei += 1
            if ei < len(effs) and effs[ei]["start"] <= t < effs[ei]["end"]:
                e = effs[ei]
                try:
                    f = EFFECTS[e["type"]](S, f, t - e["start"], e)
                except Exception as ex:  # noqa: BLE001 — bir effektin xətası bütün videonu pozmasın
                    print("fx error", e["type"], ex)
            wr.write(f)
            i += 1
            if progress and i % 15 == 0:
                progress(min(1.0, i / n_total))
    finally:
        rd.close()
        wr.close()
        try:
            if S.seg._seg is not None:
                S.seg._seg.close()
        except Exception:  # noqa: BLE001
            pass
    return len(effs)


# ================================================================ planlama (Claude)
PLAN_SYSTEM = """Sən peşəkar Reels/Shorts montajçısısan. Bir çəkilişlik danışan-baş videosuna "çox kameralı",
dinamik görünüş verən effektlər seçirsən. Mövcud effektlər (type — nə edir):
{catalog}

Qaydalar:
- Effekt danışığın mənasına uyğun olsun: sadalama → checklist; rəqəm müqayisəsi → chart_grow (yalnız danışıqda
  deyilən rəqəmlərlə, uydurma); güclü iddia/başlıq → headline_wall, glass_type, kinetic_text; şəxsi hekayə →
  cover_story, paper_cut, doodle_boil; vurğu/dramatik an → vertigo, digital_zoom, freeze_frame; texnologiya/proqram →
  phone_mockup, ui_world, screen_wall; keçidlər (cümlələr arası) → zoom_through, speed_ramp, pull_back, orbit.
- Müddət: adi effektlər 1.6–3.5 s; zoom_through 0.5–0.8 s; speed_ramp 0.6–0.9 s.
- Normalda hər 5–8 saniyədə bir effekt, aralarında ən azı 2 s təmiz video. Ən çox {max_fx}. Növləri təkrarlama.
- İstifadəçi konkret effekt(lər) və ya "hamısı/bütün effektlər" istəyibsə — onları mütləq daxil et, lazım olsa
  sıx (aralıq 0.3 s, hər biri 1.4–2 s).
- İlk 0.8 s və son 1 s-də effekt qoyma (pull_back və cover_story başlanğıcda ola bilər).
- params içindəki mətnlər videonun dilində, qısa (başlıq 1–3 söz, siyahı bəndi 1–3 söz).
JSON: {{"fx":[{{"type":"...","start":s,"end":s,"params":{{...}}}}]}}"""


def plan(words: list[dict], duration: float, extra: str = "", vertical: bool = True) -> list[dict]:
    from editor.transcript import build_utterances
    from llm import ask_json
    utts = build_utterances(words, max_len=8)
    lines = "\n".join(f"[{u['id']}] ({u['start']:.1f}–{u['end']:.1f}) {u['text']}" for u in utts)
    catalog = "\n".join(f"- {k}: {v}" for k, v in INFO.items())
    want_all = any(w in (extra or "").lower() for w in ("hamısı", "bütün effekt", "20 effekt", "all effects"))
    max_fx = 20 if want_all else max(2, min(10, int(duration / 6)))
    user = (f"Video: {duration:.1f} s, {'şaquli 9:16' if vertical else 'üfüqi 16:9'}\n"
            f"İstifadəçinin istəyi: {extra or '-'}\n\nSeqmentlər:\n{lines}")
    p = ask_json(PLAN_SYSTEM.format(catalog=catalog, max_fx=max_fx), user, max_tokens=3000)
    out = []
    for e in (p or {}).get("fx", []):
        try:
            a, b = float(e["start"]), float(e["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e.get("type") in EFFECTS and b - a >= .3 and 0 <= a < duration:
            out.append({"type": e["type"], "start": a, "end": min(b, duration - .05), "params": e.get("params") or {}})
    return out[:max_fx]


SFX_FOR = {"zoom_through": "whoosh", "speed_ramp": "whoosh", "pull_back": "whoosh", "orbit": "whoosh",
           "screen_wall": "whoosh", "phone_mockup": "whoosh", "checklist": "pop", "chart_grow": "pop",
           "kinetic_text": "pop", "headline_wall": "pop", "glass_type": "whoosh", "freeze_frame": "ding",
           "paper_cut": "pop", "cover_story": "whoosh", "green_screen": "whoosh", "frame_break": "pop",
           "digital_zoom": "pop", "ui_world": "pop"}
