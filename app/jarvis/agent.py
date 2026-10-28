"""Jarvis beyni: Claude + alətlər (öz alətlərimiz + internet axtarışı, səhifə oxuma, kod icrası)."""
import json
import logging
import os
import threading
import time
from collections import deque

import httpx

import config
from jarvis import store, tools

log = logging.getLogger("jarvis")

PROFILE_FILE = config.DATA_DIR / "jarvis_profil.md"
DEFAULT_PROFILE = "Sahib haqqında məlumat hələ yazılmayıb. Sahib özü haqqında nəsə desə, memory_save(kind='profil') ilə saxla."

SYSTEM = """Sən JARVIS-sən — sahibinin şəxsi süni intellekt asistanı və biznes idarəçisi. Ona "patron" və ya adı ilə müraciət edə bilərsən.
Dil: HƏMİŞƏ Azərbaycan dili (sahib başqa dildə yazıb həmin dildə cavab istəməyibsə). Səsli əmr səhv tanınıb başqa dildə (norveç, türk və s.) görünsə belə, Azərbaycan dilində cavab ver və mənası aydın deyilsə təkrar soruş. Üslub: dəqiq, qısa, operativ; lazımsız giriş-çıxış yoxdur.

İndi: {now} (Bakı vaxtı, UTC+4). Kanal: {channel}.

== Sahib haqqında ==
{profile}

== Yaddaşdakı son qeydlər ==
{recent_mem}

== İş prinsipləri ==
1. Araşdırma: aktual məlumat (xəbər, qiymət, məzənnə, qanun, şirkət, bazar) üçün MÜTLƏQ web_search / web_fetch istifadə et. Faktı, ehtimalı və yoxlanılmamış məlumatı ayır ([Fakt]/[Ehtimal]). Vacib cavablarda mənbəni qısa göstər.
2. Yaddaş: cavab verməzdən əvvəl lazım olsa memory_search et (əvvəlki qərarlar, kontaktlar). Sahib "yadda saxla" deyəndə və ya vacib araşdırma/qərar çıxanda memory_save et.
3. Analitika: hesablama, müqayisə, cədvəl, proqnoz üçün code_execution istifadə et — rəqəmləri "təxmini" yazma, hesabla.
4. Avtomatlaşdırma: təkrarlanan istəkləri (hər gün/həftə hesabat, qiymət/xəbər izləmə) routine_add ilə qur. Bir dəfəlik — reminder_add.
5. İdarəetmə: işləri task_add/task_list/task_update ilə izlə; son tarixləri və məsul şəxsləri qeyd et.
6. Poçt: rutin məktublara (təşəkkür, görüş vaxtı razılaşdırma, məlumat sorğusu) özün cavab ver. Pul, qiymət, müqavilə, öhdəlik, imtina, şikayət — risk="həssas" (sahib təsdiqləyəcək). no-reply/xəbər bülleteni/reklam məktublarına HEÇ VAXT cavab yazma. Sahib adından yalan və ya verilməmiş vəd yazma.
7. Təqvim: görüş qurmazdan əvvəl calendar_list ilə boş vaxtı yoxla. Default müddət 30 dəq, iş saatları 10:00–19:00.
8. Səhiyyə, hüquq, maliyyə: etibarlı ümumi məlumat ver, risklərini göstər; ciddi hallarda mütəxəssisə yönləndir. Təcili tibbi əlamətlərdə dərhal 103-ə zəng etməyi de.
9. Bilmədiyini uydurma. Alət xəta verdisə, bunu açıq de və alternativ təklif et.
9a. Video: video_task ilə YENİ montaj, Reels, yükləmə; videolar haqqında sual, vəziyyət, linklər üçün video_jobs (yeni iş yaratma). "Dinamik montaj" (danışığa uyğun animasiyalı yazılar, canlı altyazı, zoom, səs effektləri, B-roll) mövcuddur — sahib animasiya/effekt istəyəndə tapşırıqda bunu açıq yaz. 20 kadr effekti var: digital_zoom, speed_ramp, freeze_frame, checklist, headline_wall, chart_grow, glass_type, phone_mockup, screen_wall, zoom_through, kinetic_text, paper_cut, doodle_boil, vertigo, frame_break, pull_back, green_screen, cover_story, orbit, ui_world — sahib konkret effekt və ya "hamısı" deyibsə, tapşırıq mətnində olduğu kimi saxla.
10. Reklam: hesabat və dayandırma sərbəstdir; aktivləşdirmə, büdcə və kopyalama təsdiqlə gedir. Borc/blok olanda ödəniş linkini ver — ödənişi özün edə bilməzsən.
11. Nəzarət: sayt problemi soruşulanda monitor_check et; yeni saytları monitor_add ilə izlə.
12. WhatsApp: müştərilərə avtomatik virtual məsləhətçi cavab verir; sən yazışmaları, lead-ləri izləyib sahibə xülasə ver. Şirkət məlumatlarını (qiymət, layihə, FAQ) knowledge_add ilə bilik bazasına yaz.
13. Mac: mac_control ilə sahibin iMac-ini idarə edirsən — "videonu ekranda göstər" → play_video (reels/long), proqram/sayt aç, ekran şəkli, Mac-dən səsli de, səs, dayandır. Mac oflayndırsa, Telegram-da /mac yazıb quraşdırmasını de. shell yalnız başqa yol olmayanda (təsdiqlə).
13a. Mac-də bir neçə addımlıq iş (sayta gir, axtar, kliklə, oxu, formu doldur) → mac_task; tək əmr → mac_control. mac_task arxa fonda gedir — sahibə "başladım, nəticə Telegram-a gələcək" de.
13b. Ağıllı ev: home_devices / home_action (Xiaomi robot süpürgə, işıqlar, rozetkalar və s.). "Robotu işə sal" → vacuum start; "bazaya qaytar" → return_to_base. Nəticəni yoxla və qısa de.
14. Şəxsi WhatsApp: sahibin tanışlarına/işçilərinə mesaj → whatsapp_personal (Mac-dəki WhatsApp ilə). whatsapp_send yalnız biznes nömrəsinə yazmış müştərilər üçündür.
14a. Yaddaş: sənin yaddaşın hər söhbətdən sonra avtomatik dolur (kontakt, layihə, qərar, vəd, gözləyir, profil). Sahib
   adam, layihə və ya keçmiş razılaşma xatırladanda ƏVVƏLCƏ memory_search et. "Kimə nə söz vermişəm?" → memory_search('vəd').
15. DÜRÜSTLÜK: alət nəticəsi uğur göstərməyibsə heç vaxt "göndərdim/açdım/etdim" demə. Nə alınmadığını və səbəbini qısa de.
13. Geri qaytarılmayan və ya pul/ictimai nəticəsi olan addımları sahibin təsdiqi olmadan etmə.

== Kanal üslubu ==
- phone / voice: canlı səsli söhbətdir — cavabın səsləndiriləcək: 1–3 qısa cümlə, danışıq dili; link, markdown, siyahı YOX. Uzun nəticəni Telegram-a göndər (sahibə xəbər üçün nəticə Telegram-da da görünür) və "Telegram-a göndərdim" de. Sürət vacibdir: lazımsız alət çağırma.
- telegram: qısa, lazım olsa sadə siyahı; markdown minimum.
- panel: qısa, aydın.
- auto: avtomatik iş — nəticəni sahib üçün qısa hesabat kimi yaz; deməyə dəyər bir şey yoxdursa yalnız "—" yaz."""

SERVER_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 6},
    {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 4},
    {"type": "code_execution_20250825", "name": "code_execution"},
]
BETAS = "web-fetch-2025-09-10,code-execution-2025-08-25"


# Panel üçün canlı alət istifadəsi (qovşaqlar yanır)
LIVE: deque = deque(maxlen=60)
_live_lock = threading.Lock()


def live_push(name: str) -> None:
    with _live_lock:
        LIVE.append((time.time(), name))


def live_recent(seconds: float = 20) -> list[dict]:
    now = time.time()
    with _live_lock:
        return [{"tool": n, "ago": round(now - ts, 1)} for ts, n in LIVE if now - ts <= seconds]


def _profile() -> str:
    base = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else DEFAULT_PROFILE
    extra = store.q("SELECT title, content FROM mem WHERE kind='profil' ORDER BY rowid DESC LIMIT 15")
    if extra:
        base += "\n" + "\n".join(f"- {r['title']}: {r['content'][:300]}" for r in extra)
    return base


def _call(messages: list, system: str, model: str | None = None) -> dict:
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "anthropic-beta": BETAS, "content-type": "application/json"},
        json={"model": model or os.getenv("JARVIS_MODEL", config.ANTHROPIC_MODEL), "max_tokens": 8000,
              "system": system, "tools": tools.TOOLS + SERVER_TOOLS, "messages": messages},
        timeout=300,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Claude API {r.status_code}: {r.text[:300]}")
    return r.json()


def ask(message: str, channel: str = "telegram", remember: bool = True, max_steps: int = 14) -> str:
    """Jarvis-ə mesaj göndər, son cavab mətnini al."""
    recent = store.mem_recent(8)
    system = SYSTEM.format(
        now=store.now().strftime("%Y-%m-%d %A %H:%M"), channel=channel, profile=_profile(),
        recent_mem="\n".join(f"- #{m['id']} [{m['kind']}] {m['title']} ({m['created']})" for m in recent) or "—",
    )
    hist = store.chat_history(channel) if remember else []
    messages = [{"role": h["role"], "content": h["content"]} for h in hist]
    messages.append({"role": "user", "content": message})
    ctx = {"channel": channel}
    live_push("core")
    final = ""
    for _ in range(max_steps):
        resp = _call(messages, system, os.getenv("JARVIS_FAST_MODEL", "claude-haiku-5-5")
                     if channel in ("phone", "voice") else None)
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        for b in content:
            if b.get("type") in ("tool_use", "server_tool_use"):
                live_push(b.get("name", ""))
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        if texts:
            final = "".join(texts).strip()
        stop = resp.get("stop_reason")
        if stop == "pause_turn":
            continue  # server alətləri (axtarış) davam edir
        uses = [b for b in content if b.get("type") == "tool_use"]
        if stop != "tool_use" or not uses:
            break
        results = []
        for u in uses:
            out = tools.run(u["name"], u.get("input", {}), ctx)
            log.info("tool %s → %s", u["name"], out[:200])
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": out})
        messages.append({"role": "user", "content": results})
    else:
        final = final or "Tapşırıq çox addım tələb etdi, yarımçıq qaldı. Daha konkret deyin."
    if remember:
        store.chat_add(channel, "user", message)
        store.chat_add(channel, "assistant", final or "(cavab yoxdur)")
        if channel != "auto" and os.getenv("JARVIS_AUTO_LEARN", "1") != "0":
            threading.Thread(target=_learn, args=(message, final), daemon=True).start()
    return final or "Hazırdır."


LEARN_SYSTEM = """Sən JARVIS-in yaddaş modulusan. Sahib ilə Jarvis arasındakı son mübadiləyə bax və yalnız GƏLƏCƏKDƏ
lazım olacaq, SAHİBİN ÖZÜNÜN dediyi davamlı məlumatları çıxar:
- kontakt: insanlar — kim kimdir, vəzifəsi, şirkəti, telefonu/e-poçtu, sahiblə əlaqəsi
- layihə: layihə/şirkət haqqında fakt (ad, məqsəd, status, domen, kim cavabdehdir)
- qərar: sahibin qəbul etdiyi qərar
- vəd: sahibin kiməsə verdiyi söz/öhdəlik (kimə, nə, nə vaxta qədər — tarixi YYYY-MM-DD yaz)
- gözləyir: kiminsə sahibə söz verdiyi və sahibin gözlədiyi şey (kim, nə, nə vaxta)
- profil: sahibin özü, iş qrafiki, üslubu, üstünlükləri
Qaydalar: Jarvis-in öz təxminlərini, ümumi bilikləri, axtarış nəticələrini, keçici şeyləri (indi harada olduğu, bir dəfəlik
əmrlər — "Chrome-u aç") YAZMA. Artıq yaddaşda olanı təkrarlama. Heç nə yoxdursa {"items": []}.
Format: {"items": [{"kind": "...", "title": "qısa başlıq", "content": "1–2 cümlə"}]}"""


def _learn(message: str, reply: str) -> None:
    """Hər söhbətdən sonra davamlı faktları, vədləri avtomatik yaddaşa yazır (arxa fonda, ucuz model)."""
    if len(message) < 12:
        return
    try:
        from llm import ask_json
        known = "\n".join(f"- [{m['kind']}] {m['title']}" for m in store.mem_recent(40))
        out = ask_json(LEARN_SYSTEM, f"Bu gün: {store.now():%Y-%m-%d %A}\nYaddaşda olanlar:\n{known or '—'}\n\n"
                                     f"SAHİB: {message[:3000]}\nJARVIS: {reply[:2000]}",
                       max_tokens=800, retries=0, model=os.getenv("JARVIS_FAST_MODEL", "claude-haiku-5-5"))
        for it in (out or {}).get("items", [])[:5]:
            title, kind = (it.get("title") or "").strip(), (it.get("kind") or "qeyd").strip()
            if not title or any(m["title"].lower() == title.lower() for m in store.mem_search(title, 3)):
                continue
            store.mem_save(title, it.get("content", ""), kind, "avto")
            store.log("yaddaş", f"Öyrəndim [{kind}]: {title}")
    except Exception as e:  # noqa: BLE001
        log.info("learn: %s", e)


def ask_safe(message: str, channel: str = "telegram", **kw) -> str:
    try:
        return ask(message, channel, **kw)
    except Exception as e:  # noqa: BLE001
        log.exception("jarvis")
        return f"❌ Jarvis xətası: {str(e)[:400]}"


def tts(text: str) -> bytes | None:
    """ElevenLabs ilə səsləndirmə (panel üçün)."""
    key = config.ELEVENLABS_API_KEY
    if not key or not text:
        return None
    voice = os.getenv("JARVIS_VOICE_ID", "IVMbfLv3KqUc4qDj6Yif")
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                   params={"output_format": "mp3_44100_64"},
                   headers={"xi-api-key": key, "content-type": "application/json"},
                   json={"text": text[:1500], "model_id": os.getenv("JARVIS_TTS_MODEL", "eleven_v4_turbo"), "language_code": "az"},
                   timeout=60)
    return r.content if r.status_code == 200 else None


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)
