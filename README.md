# Montaj Asistanı — quraşdırma təlimatı

Telefonla zəng edirsiniz və ya Telegram-a yazırsınız: *"Son videonu montaj et, Reels hazırla, YouTube-a yüklə"*.
Server videonu mətnə çevirir, təkrarları, boşluqları və artıq hissələri kəsir, səsi normallaşdırır,
9:16 Reels versiyasını loqo və altyazı ilə hazırlayır, sonra YouTube və Instagram-a yükləyib linki Telegram-a göndərir.

```
Telefon zəngi (ElevenLabs agent) ─┐
Telegram (mətn / səsli mesaj) ────┼──► Server (Docker) ──► Səs → mətn (ElevenLabs Scribe)
Veb panel (/panel) ───────────────┘          │             Kəsim qərarları (Claude API)
                                             │             ffmpeg: kəsim, səs, Reels, loqo, altyazı
                                             └──► YouTube · Instagram · Telegram-da link
```

---

## 1. Server (≈ 15 dəq)

1. **Hetzner Cloud** → yeni server: Ubuntu 24.04, **CPX31** (4 vCPU / 8 GB, ~€13/ay) və ya CX32 (~€7/ay).
   24 dəqiqəlik 1080p videonun render-i CPX31-də təxminən 5–10 dəqiqə çəkir.
2. Domen: Cloudflare DNS-də `asistan.pilothayat.az` → serverin IP-si üçün **A** qeydi yaradın.
   **Proxy-ni söndürün (boz bulud)**: Cloudflare pulsuz planda 100 MB-dan böyük yükləməni kəsir.
3. Serverə daxil olub Docker quraşdırın:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
4. Bu qovluğu serverə köçürün (məs. `scp -r ai-editor root@IP:/opt/`), sonra:
   ```bash
   cd /opt/ai-editor
   cp .env.example .env
   nano .env        # aşağıdakı açarları doldurun
   ```

## 2. Açarlar

| Nə | Haradan |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram-da @BotFather → /newbot |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | https://my.telegram.org → API development tools (2 GB fayl limiti üçün lazımdır) |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `ELEVENLABS_API_KEY` | elevenlabs.io → Profile → API Keys (Scribe + telefon agenti) |
| `PANEL_TOKEN`, `AGENT_TOOL_SECRET` | `openssl rand -hex 24` ilə yaradın |

**Vacib, bir dəfəlik addım:** botu Telegram-ın rəsmi serverindən çıxarın ki, bizim serverə keçsin:
```bash
curl https://api.telegram.org/bot<TOKEN>/logOut
```

## 3. İşə salmaq

```bash
docker compose up -d --build
docker compose logs -f app
```
Telegram-da bota `/start` yazın. İlk yazan sahib olur, başqaları istifadə edə bilməz.
Veb panel: `https://asistan.pilothayat.az/panel?t=<PANEL_TOKEN>` (iMac-dan böyük faylları buradan yükləmək rahatdır).

**Loqo:** Nizami City loqosunu şəffaf PNG kimi bota **sənəd (fayl)** şəklində göndərin, izahına `loqo nizami` yazın.
Tapşırıqda "Nizami City loqosu ilə" desəniz, həmin loqo qoyulur.

**Montaj qaydaları:** `/qaydalar` — baxmaq; `/qaydalar <yeni mətn>` — dəyişmək. Asistan hər montajda bu qaydalara əməl edir.

---

## 4. YouTube

1. https://console.cloud.google.com → yeni layihə → **YouTube Data API v3**-ü aktiv edin.
2. *OAuth consent screen*: External, öz e-poçtunuzu **Test users**-ə əlavə edin.
3. *Credentials* → *Create OAuth client ID* → **Desktop app** → JSON-u yükləyin (`client_secret.json`).
4. iMac-da:
   ```bash
   pip3 install google-auth-oauthlib
   python3 scripts/youtube_auth.py ~/Downloads/client_secret.json
   ```
   Çıxan 3 sətri `.env`-ə yazın və `docker compose up -d` edin.

> ⚠️ Google qaydası: yoxlanmamış (audit keçməmiş) API layihəsindən yüklənən videolar avtomatik **private** olur.
> Linkli (unlisted) və ya public yükləmə üçün Google-a *YouTube API Services Audit* müraciəti göndərmək lazımdır
> (pulsuzdur, bir neçə həftə çəkə bilər). O vaxta qədər `YOUTUBE_DEFAULT_PRIVACY=private` qoyun və YouTube Studio-da bir kliklə açın.
> Test rejimində refresh token 7 gündən bir köhnəlir. Bunun qarşısını almaq üçün consent screen-i **In production** vəziyyətinə keçirin.

## 5. Instagram (Facebook səhifəsi lazım deyil)

1. Instagram hesabı **Professional** (Business və ya Creator) olmalıdır.
2. https://developers.facebook.com → *Create app* → tip: **Business** → **Instagram** məhsulunu əlavə edin →
   *API setup with Instagram login*.
3. *Generate access tokens* → hesabınızı əlavə edin → icazələr: `instagram_business_basic`, `instagram_business_content_publish`.
4. Göstərilən **Instagram User ID** və **token**-i `.env`-ə yazın (`INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`).
   Token 60 gün yaşayır, server onu özü yeniləyir.
5. Tətbiq development rejimində qala bilər: öz hesabınızla işləyir, App Review lazım deyil.

Default olaraq Instagram-a paylaşmazdan əvvəl Telegram-da **önizləmə və "Paylaş" düyməsi** gəlir (`REQUIRE_CONFIRM_PUBLIC=true`).
Tam avtomatik paylaşım istəsəniz `false` edin.

---

## 6. Telefon zəngi (ElevenLabs Agents)

1. elevenlabs.io → **Agents** → *Create agent* (Blank).
2. **System prompt** və **First message**: `elevenlabs/agent_prompt.md` faylından köçürün.
   Dil: Azerbaijani. Səs: kişi və ya qadın, istədiyinizi seçin.
3. **Tools** → *Add tool* → *Webhook*: `elevenlabs/tools.json` faylındakı iki aləti əlavə edin.
   URL-də domeninizi, header-də `AGENT_TOOL_SECRET`-i yazın.
   `caller_id` üçün *Dynamic variable* = `system__caller_id` seçin.
4. **Phone Numbers**:
   - **Ən sadə yol:** Twilio-dan nömrə alıb ElevenLabs-a *Import* edin (ABŞ/Böyük Britaniya nömrəsi; Azərbaycandan zəng beynəlxalq tarifdir).
   - **Yerli nömrə:** planlaşdırdığınız **Yeastar TG200L GSM gateway** SIP trunk dəstəkləyir. ElevenLabs → *Phone Numbers* → *SIP trunk*
     ilə bağlanır, yerli SIM kartdakı nömrəyə zəng birbaşa agentə gəlir. AI zəng agenti layihəsi ilə eyni avadanlıqdır.
5. `.env`-də `ALLOWED_CALLER_IDS` sahəsinə öz nömrənizi yazın: başqa nömrədən gələn tapşırıqlar qəbul olunmayacaq.

Zəng nümunəsi:
> — Buyurun, patron, dinləyirəm.
> — Salam. Son göndərdiyim videonu montaj et, Nizami City loqosu ilə 45 saniyəlik Reels də hazırla, YouTube-a yüklə.
> — Not alıram, patron. Video 24 dəqiqədir, tam montaj, Reels və YouTube. Bitəndə Telegram-a link göndərəcəyəm.

---

## JARVIS — şəxsi asistan

Telegram-a yazdığınız və ya səsli dediyiniz hər şey, telefon zəngləri və panel (`/jarvis?t=<PANEL_TOKEN>`) Jarvis-ə gedir.

| Blok | Nə edir |
|---|---|
| Araşdırma | İnternetdə axtarış və səhifə oxuma (Claude web search/fetch), fakt və ehtimalı ayırır |
| Yaddaş | Qeydlər, qərarlar, kontaktlar, araşdırmalar — tam mətn axtarışı ilə |
| Analitika | Hesablama, müqayisə, cədvəl — kod icrası ilə |
| Avtomatlaşdırma | Rutinlər (`daily 09:00`, `weekly mon 10:00`, `every 3h`), xatırlatmalar, səhər hesabatı |
| İdarəetmə | Tapşırıqlar (məsul, son tarix, prioritet), fəaliyyət jurnalı |
| Google | Gmail (oxu, çeşidlə, cavab ver), Calendar (görüş), Drive/Docs (sənəd) |
| Media | Video montaj, Reels, YouTube/Instagram, saytlar |

Təhlükəsizlik: pul, qiymət, müqavilə, öhdəlik olan məktublar və silmə əməliyyatları Telegram-da **Təsdiqlə** düyməsi ilə icra olunur.
`/profil <mətn>` — Jarvis-ə özünüz və şirkətləriniz haqqında yazın.

### Reklam kabinetləri (Meta, Google Ads, TikTok)
Jarvis: hesabat, kampaniya siyahısı, **dayandırma** (dərhal), **aktivləşdirmə / büdcə / kopyalama** (Telegram təsdiqi ilə),
borc, blok və aşağı balans üçün 2 saatdan bir yoxlama və **ödəniş linki**. Ödənişin özü API ilə mümkün deyil.
- **Meta:** business.facebook.com → Business ayarları → *System users* → Admin system user → reklam hesablarını *Assign assets* →
  *Generate token* (ads_management, ads_read, business_management) → setup-da `META_ADS_TOKEN`.
- **Google Ads:** ads.google.com → Tools → *API Center* → developer token (Basic access müraciəti) → `GOOGLE_ADS_DEVELOPER_TOKEN`,
  `GOOGLE_ADS_CUSTOMER_IDS`. Google Cloud-da *Google Ads API*-ni Enable edin və "Google hesabını qoş"u yenidən basın.
- **TikTok:** business-api.tiktok.com → developer app → access token → `TIKTOK_ACCESS_TOKEN`, `TIKTOK_ADVERTISER_IDS`.

### Sayt nəzarəti
Setup-da `MONITOR_URLS` (vergüllə) və ya Jarvis-ə: "luxresidence.az-ı nəzarətə götür". 5 dəqiqədən bir yoxlanır,
gündə bir dəfə SSL və domen bitmə tarixi. Çökəndə / bərpa olunanda / SSL ≤14 gün / domen ≤30 gün → Telegram.

### WhatsApp (Cloud API)
Müştərilərə virtual məsləhətçi cavab verir (yalnız bilik bazasından), lead toplayır, endirim/şikayət/görüşü sizə ötürür.
1. developers.facebook.com → tətbiq → **WhatsApp** məhsulu → nömrə əlavə edin (mövcud WhatsApp Business nömrəsi köçürülə bilər).
2. Setup-da `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_BUSINESS`.
3. WhatsApp → Configuration → Webhook: setup səhifəsindəki URL + verify token → *messages* sahəsinə abunə olun.
4. Bilik bazası: bota qiymət/FAQ faylını (.pdf/.docx/.txt) göndərin, izahına `biznes` yazın — və ya Jarvis-ə "bilik bazasına əlavə et: ..." deyin.

### Sənədləri yaddaşa yükləmək
Bota .pdf / .docx / .txt / .md / .csv faylı göndərin → Jarvis-in yaddaşına yazılır və sual verə bilərsiniz.

### Google-u qoşmaq (Gmail, Calendar, Drive, YouTube) — ≈10 dəq, kompüterdən

1. https://console.cloud.google.com → yeni layihə: `Jarvis`.
2. **APIs & Services → Library**: bu 4-ünü **Enable** edin: *Gmail API*, *Google Calendar API*, *Google Drive API*, *YouTube Data API v3*.
3. **OAuth consent screen**: *External* → ad `Jarvis`, e-poçtunuz → *Test users*-ə öz Gmail-inizi əlavə edin.
   Sonra **Publish app** (In production) basın. Bunu etməsəniz, giriş 7 gündən bir kəsiləcək.
4. **Credentials → Create credentials → OAuth client ID → Web application**.
   *Authorized redirect URIs*: setup səhifəsində göstərilən ünvan (`https://<server>/oauth/youtube/callback`).
5. Client ID və Secret-i setup səhifəsinə yapışdırın → **Yadda saxla** → 1 dəq gözləyin.
6. **🔗 Google hesabını qoş** → hesabı seçin → "Google hasn't verified this app" → *Advanced* → *Go to Jarvis* → bütün icazələri verin.

---

## Təxmini xərclər

| | |
|---|---|
| Server (Hetzner) | €7–13/ay |
| ElevenLabs (Scribe + agent dəqiqələri) | Creator planı ~$22/ay kifayət edir (aylıq limit daxilində) |
| Claude API | 24 dəqiqəlik video üçün ≈ $0.05–0.20 |
| Twilio nömrəsi (lazım olsa) | ~$1–2/ay + dəqiqə haqqı |

Qiymətlər dəyişə bilər, qeydiyyatdan əvvəl saytlarda yoxlayın.

## Faydalı əmrlər

```bash
docker compose logs -f app          # canlı log
docker compose restart app          # .env dəyişəndən sonra
docker compose pull && docker compose up -d --build   # yeniləmə
```
Fayllar `./data` qovluğundadır. `KEEP_DAYS` (default 14) gündən köhnə videolar avtomatik silinir.

## Fayl strukturu

```
app/main.py          veb server, telefon agenti API, panel
app/bot.py           Telegram bot
app/pipeline.py      icra ardıcıllığı (skan → mətn → kəsim → render → yükləmə)
app/planner.py       sərbəst əmri plana çevirir (Claude)
app/editor/          ffmpeg, seqmentlər, altyazı, kəsim qərarları
app/uploaders/       YouTube, Instagram
elevenlabs/          agent göstərişi və alətlər
scripts/             YouTube token skripti
```
