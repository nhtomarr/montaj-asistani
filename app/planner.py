"""Sərbəst əmri (Telegram mətni, səsli mesaj, telefon zəngi) icra planına çevirir."""
import config
from llm import ask_json, LLMError

DEFAULT_PLAN = {
    "action": "process",
    "video": "latest",
    "clean_edit": True,
    "loudnorm": True,
    "long_video": True,
    "reels": False,
    "subtitles_long": False,
    "subtitles_reels": True,
    "logo": None,
    "upload_youtube": False,
    "upload_instagram": False,
    "youtube_privacy": None,
    "youtube_title": None,
    "instagram_caption": None,
    "reels_max_seconds": None,
    "extra": "",
}

PRESETS = {
    "full_youtube": {"long_video": True, "upload_youtube": True},
    "reels_instagram": {"long_video": False, "reels": True, "upload_instagram": True, "logo": "default"},
    "both": {"long_video": True, "reels": True, "upload_youtube": True, "upload_instagram": True, "logo": "default"},
    "edit_only": {"long_video": True},
}

SYSTEM = """Sən video montaj asistanının əmr analizatorusan. İstifadəçi (Azərbaycan, türk, rus və ya ingilis dilində) tapşırıq verir.
Onu JSON plana çevir. Sahələr:
- action: "process" (video emalı), "status" (iş vəziyyətini soruşur), "chat" (başqa sual/söhbət)
- video: "latest" və ya konkret video id (rəqəm), istifadəçi aydın göstəribsə
- clean_edit: təkrarları/boşluqları/artıq hissələri kəsmək (default true)
- loudnorm: səs səviyyəsini normallaşdırmaq (default true)
- long_video: tam (horizontal/orijinal) montaj versiyası lazımdırmı
- reels: 9:16 qısa Reels/Shorts versiyası lazımdırmı
- subtitles_long, subtitles_reels: altyazı (reels üçün default true, uzun üçün default false)
- logo: loqonun adı (mövcud loqolardan) və ya "default" və ya null. Reels üçün istifadəçi brend adı çəkirsə həmin loqonu seç.
- upload_youtube, upload_instagram: yükləmək istəyirmi
- youtube_privacy: "public" | "unlisted" | "private" | null (deməyibsə null)
- youtube_title, instagram_caption: istifadəçi özü veribsə, yoxsa null
- reels_max_seconds: deyibsə rəqəm, yoxsa null
- extra: montajla bağlı əlavə göstərişlər (məs. "girişi qısalt", "sondakı reklamı at") — sərbəst mətn
- reply: action "status"/"chat" olduqda istifadəçiyə qısa cavab (onun dilində); "process" üçün 1 cümlə təsdiq ("Not alıram, ...").
Qeyd: "YouTube-a yüklə" = long_video true + upload_youtube true. "Reels/Instagram" = reels true. "Shorts" = reels true + upload_youtube.
Əgər yalnız reels istənibsə long_video false et."""


def plan_from_text(text: str, latest_video: dict | None, logos: list[str]) -> dict:
    vinfo = (f"Son video: id={latest_video['id']}, ad={latest_video['name']}, "
             f"{latest_video['duration']/60:.1f} dəq" if latest_video else "Hələ video yoxdur")
    user = (f"{vinfo}\nMövcud loqolar: {', '.join(logos) or 'yoxdur'}\n"
            f"Montaj qaydaları:\n{config.rules()}\n\nƏmr: {text}")
    try:
        p = ask_json(SYSTEM, user, max_tokens=1200)
    except LLMError:
        p = {"action": "process", "extra": text}
    plan = {**DEFAULT_PLAN, **{k: v for k, v in p.items() if k in DEFAULT_PLAN or k == "reply"}}
    plan["source_text"] = text
    return plan


def preset(name: str) -> dict:
    return {**DEFAULT_PLAN, **PRESETS[name]}
