"""Bütün tənzimləmələr .env faylından oxunur."""
import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "bəli")


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")  # məs: https://asistan.pilothayat.az
PANEL_TOKEN = os.getenv("PANEL_TOKEN", "")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "http://telegram-bot-api:8081").rstrip("/")
TELEGRAM_LOCAL_MODE = _bool("TELEGRAM_LOCAL_MODE", True)
TELEGRAM_ALLOWED_USER_IDS = [int(x) for x in _list("TELEGRAM_ALLOWED_USER_IDS")]

# Süni intellekt
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

# Səs → mətn
STT_PROVIDER = os.getenv("STT_PROVIDER", "elevenlabs")  # elevenlabs | openai | local
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "")  # boş = avtomatik; məs. "aze", "tur"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "small")

# Telefon agenti (ElevenLabs)
AGENT_TOOL_SECRET = os.getenv("AGENT_TOOL_SECRET", "")
ALLOWED_CALLER_IDS = _list("ALLOWED_CALLER_IDS")  # boşdursa yoxlanmır

# YouTube
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_DEFAULT_PRIVACY = os.getenv("YOUTUBE_DEFAULT_PRIVACY", "unlisted")

# Instagram (Instagram API with Instagram Login)
INSTAGRAM_USER_ID = os.getenv("INSTAGRAM_USER_ID", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_GRAPH_HOST = os.getenv("INSTAGRAM_GRAPH_HOST", "https://graph.instagram.com")
INSTAGRAM_GRAPH_VERSION = os.getenv("INSTAGRAM_GRAPH_VERSION", "v23.0")

# Davranış
REQUIRE_CONFIRM_PUBLIC = _bool("REQUIRE_CONFIRM_PUBLIC", True)  # Instagram / public YouTube öncə təsdiq
REELS_FRAMING = os.getenv("REELS_FRAMING", "crop")  # crop | blur
REELS_MAX_SECONDS = int(os.getenv("REELS_MAX_SECONDS", "60"))
LONG_LOUDNESS = float(os.getenv("LONG_LOUDNESS", "-14"))
X264_PRESET = os.getenv("X264_PRESET", "veryfast")
X264_CRF = os.getenv("X264_CRF", "20")
SUB_FONT = os.getenv("SUB_FONT", "Noto Sans")
FONTS_DIR = os.getenv("FONTS_DIR", "/usr/share/fonts")
KEEP_DAYS = int(os.getenv("KEEP_DAYS", "14"))

VIDEOS_DIR = DATA_DIR / "videos"
WORK_DIR = DATA_DIR / "work"
PUBLIC_DIR = DATA_DIR / "public"
LOGOS_DIR = DATA_DIR / "logos"
RULES_FILE = DATA_DIR / "qaydalar.md"
DB_PATH = DATA_DIR / "asistan.db"

for d in (VIDEOS_DIR, WORK_DIR, PUBLIC_DIR, LOGOS_DIR):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_RULES = """\
- Təkrar çəkilən cümlələrdən yalnız sonuncu, tam alınmış variantı saxla.
- "ee", "hmm", yarımçıq başlanğıclar və kameradan kənar danışıqları ("yenidən", "başlayaq", "kəs") at.
- Fikirlər arasında 0.5 saniyədən uzun boşluq qalmasın.
- Reels: ilk 3 saniyədə güclü giriş (hook), 30–60 saniyə, sonda aydın nəticə.
- Altyazılar Azərbaycan dilində, böyük və oxunaqlı.
- YouTube başlığı qısa və maraqlı olsun, klikbeyt olmasın.
"""


def rules() -> str:
    if RULES_FILE.exists():
        return RULES_FILE.read_text(encoding="utf-8")
    return DEFAULT_RULES


def set_rules(text: str) -> None:
    RULES_FILE.write_text(text.strip() + "\n", encoding="utf-8")
