"""Montaj qərarları: nəyi kəsmək, Reels üçün hansı hissə, başlıq/təsvir."""
from llm import ask_json
from editor.transcript import numbered

CLEAN_SYSTEM = """Sən peşəkar video montajçısan. Sənə videonun nitqi nömrələnmiş seqmentlər şəklində verilir:
[id] (başlanğıc–son saniyə) mətn

Vəzifən: hansı seqmentlərin ATILACAĞINI seçmək.
- Eyni fikir bir neçə dəfə deyilibsə (təkrar çəkiliş, yarımçıq başlanğıc, səhv deyib yenidən başlama) — yalnız SONUNCU tam variantı saxla, əvvəlkiləri at.
- Yalnız "ee", "hmm", "yaxşı", "bir dəqiqə" kimi doldurucu seqmentləri at.
- Kameradan kənar texniki danışıqları at ("çəkir?", "yenidən", "başlayaq", "kəs", "alınmadı").
- Qalan bütün məzmunu saxla. Sadəcə uzun olduğu üçün məzmun atma.
- Montaj qaydaları və istifadəçinin əlavə göstərişi aşağıdadır, onlara əməl et.

JSON formatı:
{
  "remove": [atılan id-lər],
  "reasons": {"id": "qısa səbəb"},
  "title": "YouTube başlığı (videonun dilində, 70 simvoldan az)",
  "description": "2–4 cümləlik YouTube təsviri",
  "tags": ["5-10 teq"],
  "chapters": [{"id": seqment_id, "title": "fəsil adı"}]
}
chapters: yalnız video 4 dəqiqədən uzundursa və aydın mövzu keçidləri varsa, 3–8 fəsil; əks halda boş siyahı."""


def clean_decision(utts: list[dict], rules: str, extra: str, duration: float) -> dict:
    user = (
        f"Montaj qaydaları:\n{rules}\n\nİstifadəçinin göstərişi: {extra or '-'}\n"
        f"Videonun müddəti: {duration/60:.1f} dəqiqə\n\nSeqmentlər:\n{numbered(utts)}"
    )
    res = ask_json(CLEAN_SYSTEM, user, max_tokens=6000)
    ids = {u["id"] for u in utts}
    res["remove"] = [int(i) for i in res.get("remove", []) if str(i).lstrip("-").isdigit() and int(i) in ids]
    return res


REELS_SYSTEM = """Sən Instagram Reels / YouTube Shorts montajçısan. Sənə artıq təmizlənmiş videonun seqmentləri verilir.
Ən güclü, öz-özlüyündə başa düşülən hissəni seç:
- İlk seqment diqqət çəkən giriş (hook) olsun.
- Ümumi müddət {min_s}–{max_s} saniyə.
- Seqmentlər ardıcıl olmaya da bilər, amma məna axını təbii qalmalıdır; sıranı dəyişmə.
- Bütün video artıq bu müddətə sığırsa, hamısını saxla.

JSON:
{{"keep": [id-lər, xronoloji sırada], "caption": "Instagram mətni (videonun dilində, 1–3 cümlə + 3–6 heşteq)", "title": "Shorts başlığı"}}"""


def reels_decision(utts: list[dict], rules: str, extra: str, min_s: int, max_s: int) -> dict:
    sys = REELS_SYSTEM.format(min_s=min_s, max_s=max_s)
    user = f"Qaydalar:\n{rules}\n\nİstifadəçinin göstərişi: {extra or '-'}\n\nSeqmentlər:\n{numbered(utts)}"
    res = ask_json(sys, user, max_tokens=3000)
    ids = {u["id"] for u in utts}
    res["keep"] = sorted(int(i) for i in res.get("keep", []) if str(i).isdigit() and int(i) in ids)
    return res
