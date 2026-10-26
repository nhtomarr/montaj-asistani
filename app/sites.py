"""Sayt modulu: Telegram-dan təsvirlə sayt yaratmaq, düzəltmək, şəkil əlavə etmək, domen bağlamaq.

Hər sayt: /data/sites/<slug>/index.html + images/ + history/
Ünvan:   https://<slug>.<DOMAIN>   (məs. villagepro.37-27-41-45.sslip.io)
         və ya bağlanmış öz domeni (villagepro.az)
"""
import base64
import re
import shutil
import subprocess
import time
from pathlib import Path

import config
import db
from llm import ask_text

SITES_DIR = config.DATA_DIR / "sites"
INBOX = config.DATA_DIR / "inbox_images"
SITES_DIR.mkdir(parents=True, exist_ok=True)
INBOX.mkdir(parents=True, exist_ok=True)

SYSTEM = """Sən yüksək səviyyəli veb dizayner və front-end developersən. Sənə saytın təsviri verilir.
TƏK, TAM, işlək HTML faylı yaz:
- Bütün CSS <style> içində, JS <script> içində. Xarici fayl yalnız Google Fonts ola bilər.
- Mobil-first, telefonda mükəmməl görünsün; 16px kənar boşluq, üfüqi sürüşmə olmasın.
- Müasir, səliqəli, brendə uyğun dizayn; şablon kimi görünməsin. Yumşaq animasiyalar (prefers-reduced-motion nəzərə al).
- Dil: istifadəçi başqa dil deməyibsə Azərbaycan dili. Mətnləri real və təbii yaz, "Lorem ipsum" olmasın.
- Telefon/WhatsApp/ünvan verilibsə: tel: və https://wa.me/ linkləri, Google Maps linki.
- Şəkillər: yalnız sənə verilən fayl adlarını "images/AD" kimi istifadə et. Şəkil yoxdursa, CSS qradiyent/ikonlarla gözəl et — xarici şəkil linki QOYMA.
- <title> və meta description, og:title, favicon kimi emoji SVG.
Cavab olaraq YALNIZ HTML kodunu qaytar: <!doctype html> ilə başla, </html> ilə bitir. İzah yazma."""

EDIT_SYSTEM = SYSTEM + """

İndi sənə mövcud saytın HTML-i və istifadəçinin düzəliş istəyi verilir.
İstənilən dəyişikliyi et, qalan hər şeyi olduğu kimi saxla. YENƏ tam HTML faylını qaytar."""


def slugify(name: str) -> str:
    tr = str.maketrans("əöüğışçƏÖÜĞİŞÇ", "eougiscEOUGISC")
    s = re.sub(r"[^a-z0-9]+", "-", name.translate(tr).lower()).strip("-")
    return s[:30] or f"sayt-{int(time.time())}"


def list_sites() -> list[dict]:
    out = []
    for d in sorted(SITES_DIR.iterdir()):
        if (d / "index.html").exists():
            out.append({"slug": d.name, "url": site_url(d.name), "domain": domain_of(d.name)})
    return out


def site_url(slug: str) -> str:
    dom = domain_of(slug)
    if dom:
        return f"https://{dom}"
    host = config.PUBLIC_URL.split("://", 1)[-1]
    return f"https://{slug}.{host}"


def domain_of(slug: str) -> str | None:
    return (db.kv_get("site_domains", {}) or {}).get(slug)


def set_domain(slug: str, domain: str) -> None:
    m = db.kv_get("site_domains", {}) or {}
    domain = domain.lower().strip().removeprefix("https://").removeprefix("http://").strip("/")
    m[slug] = domain
    db.kv_set("site_domains", m)


def resolve_host(host: str) -> str | None:
    """Host başlığına görə saytın slug-ını tapır (yoxdursa None)."""
    host = host.split(":")[0].lower()
    for slug, dom in (db.kv_get("site_domains", {}) or {}).items():
        if host in (dom, f"www.{dom}"):
            return slug
    base = config.PUBLIC_URL.split("://", 1)[-1].lower()
    if host.endswith("." + base):
        slug = host[: -len(base) - 1]
        if (SITES_DIR / slug / "index.html").exists():
            return slug
    return None


# ---- şəkillər
def add_inbox_image(src: Path, name: str) -> Path:
    name = re.sub(r"[^\w.\-]+", "_", name)
    dst = INBOX / f"{int(time.time()*1000)}_{name}"
    shutil.move(str(src), dst)
    return dst


def inbox_count() -> int:
    return len(list(INBOX.iterdir()))


def _take_inbox(site_dir: Path) -> list[Path]:
    imgdir = site_dir / "images"
    imgdir.mkdir(exist_ok=True)
    taken = []
    for i, f in enumerate(sorted(INBOX.iterdir()), 1):
        dst = imgdir / f"sekil-{int(time.time())}-{i}.jpg"
        # Veb üçün kiçildir (en çox 1800px), JPEG
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(f), "-vf",
                        "scale='min(1800,iw)':-2", "-q:v", "4", str(dst)], check=False)
        f.unlink(missing_ok=True)
        if dst.exists():
            taken.append(dst)
    return taken


def _image_blocks(paths: list[Path]) -> list[dict]:
    blocks = []
    for p in paths[:12]:
        small = p.with_suffix(".prev.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", "scale='min(700,iw)':-2",
                        "-q:v", "6", str(small)], check=False)
        if small.exists():
            blocks.append({"type": "text", "text": f"Şəkil faylı: images/{p.name}"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.b64encode(small.read_bytes()).decode()}})
            small.unlink()
    return blocks


def _clean_html(text: str) -> str:
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i = text.lower().find("<!doctype")
    if i == -1:
        i = text.lower().find("<html")
    if i == -1:
        raise RuntimeError("Claude HTML qaytarmadı")
    text = text[i:]
    j = text.lower().rfind("</html>")
    return text[: j + 7] if j != -1 else text


def _save(site_dir: Path, html: str) -> None:
    idx = site_dir / "index.html"
    if idx.exists():
        hist = site_dir / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(idx, hist / f"{int(time.time())}.html")
    idx.write_text(html, encoding="utf-8")


def create(name: str, request: str) -> dict:
    slug = slugify(name)
    d = SITES_DIR / slug
    n = 2
    while (d / "index.html").exists():
        slug = f"{slugify(name)}-{n}"
        d = SITES_DIR / slug
        n += 1
    d.mkdir(parents=True, exist_ok=True)
    imgs = _take_inbox(d)
    content = [{"type": "text", "text": f"Saytın adı: {name}\n\nTəsvir:\n{request}"}] + _image_blocks(imgs)
    html = _clean_html(ask_text(SYSTEM, content))
    _save(d, html)
    return {"slug": slug, "url": site_url(slug), "images": len(imgs)}


def edit(slug: str, request: str) -> dict:
    d = SITES_DIR / slug
    cur = (d / "index.html").read_text(encoding="utf-8")
    imgs = _take_inbox(d)
    existing = [p.name for p in (d / "images").glob("*.jpg")] if (d / "images").exists() else []
    content = [{"type": "text", "text": f"Mövcud şəkillər: {', '.join(existing) or 'yoxdur'}\n\n"
                                        f"Mövcud HTML:\n{cur}\n\nDüzəliş istəyi:\n{request}"}]
    if imgs:
        content.append({"type": "text", "text": "Yeni əlavə olunan şəkillər (istəyə uyğun yerləşdir):"})
        content += _image_blocks(imgs)
    html = _clean_html(ask_text(EDIT_SYSTEM, content))
    _save(d, html)
    return {"slug": slug, "url": site_url(slug), "images": len(imgs)}


def undo(slug: str) -> bool:
    hist = SITES_DIR / slug / "history"
    vers = sorted(hist.glob("*.html")) if hist.exists() else []
    if not vers:
        return False
    shutil.move(str(vers[-1]), SITES_DIR / slug / "index.html")
    return True


def delete(slug: str) -> None:
    shutil.rmtree(SITES_DIR / slug, ignore_errors=True)
    m = db.kv_get("site_domains", {}) or {}
    m.pop(slug, None)
    db.kv_set("site_domains", m)
