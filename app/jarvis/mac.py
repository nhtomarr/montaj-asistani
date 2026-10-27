"""iMac/Mac köməkçisi: Mac özü serverə qoşulur (long-poll), Jarvis əmrləri növbəyə qoyur, nəticəni gözləyir."""
import asyncio
import base64
import hashlib
import hmac
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

import config

router = APIRouter()
_cv = threading.Condition()
_queue: list[dict] = []
_results: dict[str, dict] = {}
_state = {"seen": 0.0, "name": ""}
SHOTS = config.PUBLIC_DIR / "mac"
SHOTS.mkdir(parents=True, exist_ok=True)

ACTIONS = ("open_app", "quit_app", "open_url", "play_video", "screenshot", "notify", "play_audio",
           "volume", "media", "lock", "apps", "shell", "contacts", "whatsapp", "update")


def key() -> str:
    return hmac.new(config.PANEL_TOKEN.encode(), b"jarvis-mac-v1", hashlib.sha256).hexdigest()[:40] \
        if config.PANEL_TOKEN else ""


def online() -> bool:
    return time.time() - _state["seen"] < 70


def install_command() -> str:
    return f"curl -fsSL '{config.PUBLIC_URL}/mac/install?k={key()}' | zsh"


def send(action: str, arg: str = "", timeout: float = 45) -> dict:
    """Əmri Mac-ə göndərir və nəticəni gözləyir (sinxron — alət axınından çağırılır)."""
    if action not in ACTIONS:
        return {"ok": False, "text": f"naməlum əmr {action}"}
    if not online():
        return {"ok": False, "text": "Mac oflayndır (söndürülüb, yuxudadır və ya köməkçi quraşdırılmayıb)."}
    if action not in ("shell", "whatsapp"):
        arg = arg.replace('"', "'").replace("\\", "")
    cid = uuid.uuid4().hex[:12]
    with _cv:
        _queue.append({"id": cid, "action": action, "arg": arg})
        _cv.notify_all()
        end = time.time() + timeout
        while cid not in _results:
            left = end - time.time()
            if left <= 0:
                return {"ok": False, "text": "Mac vaxtında cavab vermədi (əmr göndərildi, bəlkə yenə icra olunacaq)."}
            _cv.wait(left)
        return _results.pop(cid)


def _check(k: str):
    if not key() or not hmac.compare_digest(k or "", key()):
        raise HTTPException(403, "icazə yoxdur")


def _take(wait: float) -> list[dict]:
    with _cv:
        if not _queue:
            _cv.wait(wait)
        out = _queue[:]
        _queue.clear()
        return out


@router.get("/api/mac/poll", response_class=PlainTextResponse)
async def poll(k: str = "", name: str = ""):
    _check(k)
    _state["seen"], _state["name"] = time.time(), name[:60]
    cmds = await asyncio.to_thread(_take, 25)
    _state["seen"] = time.time()
    return "".join(f"{c['id']}\t{c['action']}\t{base64.b64encode(c['arg'].encode()).decode()}\n" for c in cmds)


@router.post("/api/mac/result")
async def result(k: str = "", id: str = Form(...), ok: str = Form("1"), text: str = Form(""),
                 file: UploadFile | None = File(None)):
    _check(k)
    r = {"ok": ok == "1", "text": text[:6000]}
    if file is not None:
        p = SHOTS / f"shot_{uuid.uuid4().hex[:16]}.png"
        p.write_bytes(await file.read())
        r["file"] = str(p)
        r["url"] = f"{config.PUBLIC_URL}/media/mac/{p.name}"
    with _cv:
        _results[id] = r
        _cv.notify_all()
    return {"ok": True}


@router.get("/mac/install", response_class=PlainTextResponse)
def install(k: str = ""):
    _check(k)
    return INSTALL.replace("__AGENT__", AGENT).replace("__SERVER__", config.PUBLIC_URL).replace("__KEY__", key())


AGENT = r'''#!/bin/zsh
# Jarvis Mac köməkçisi — serverdən əmr alır, icra edir, nəticəni qaytarır.
SERVER="__SERVER__"; KEY="__KEY__"; DIR="$HOME/.jarvis"; MOV="$HOME/Movies/Jarvis"
mkdir -p "$DIR" "$MOV"
NAME=$(scutil --get ComputerName 2>/dev/null | tr ' ' '_')
reply() { # id ok text [file]
  if [[ -n "$4" ]]; then curl -fsS -m 60 -F "id=$1" -F "ok=$2" -F "text=$3" -F "file=@$4" "$SERVER/api/mac/result?k=$KEY" >/dev/null
  else curl -fsS -m 30 -F "id=$1" -F "ok=$2" -F "text=$3" "$SERVER/api/mac/result?k=$KEY" >/dev/null; fi
}
run() { # id action arg
  local id=$1 act=$2 arg=$3 out ok=1 f
  case $act in
    open_app) out=$(open -a "$arg" 2>&1) || ok=0; [[ $ok == 1 ]] && osascript -e "tell application \"$arg\" to activate" >/dev/null 2>&1; out=${out:-"$arg açıldı"} ;;
    quit_app) out=$(osascript -e "tell application \"$arg\" to quit" 2>&1) || ok=0; out=${out:-"$arg bağlandı"} ;;
    open_url) out=$(open "$arg" 2>&1) || ok=0; out=${out:-"açıldı"} ;;
    play_video)
      f="$MOV/$(basename "${arg%%\?*}")"
      [[ -s "$f" ]] || curl -fsSL -m 900 -o "$f" "$arg" || { reply $id 0 "video yüklənmədi"; return; }
      out=$(osascript -e "tell application \"QuickTime Player\"" -e "activate" -e "set d to open POSIX file \"$f\"" \
            -e "delay 1" -e "present d" -e "play d" -e "end tell" 2>&1) || ok=0
      out=${out:-"video ekranda oynayır: $f"} ;;
    play_audio) f="$DIR/say.mp3"; curl -fsSL -m 60 -o "$f" "$arg" && afplay "$f" && out="səsləndirildi" || { ok=0; out="səs alınmadı"; } ;;
    screenshot) f="$DIR/shot.png"; rm -f "$f"; screencapture -x "$f" 2>/dev/null
      if [[ -s "$f" ]]; then reply $id 1 "ekran şəkli" "$f"; return; else ok=0; out="Ekran şəkli alınmadı — Sistem Ayarları → Məxfilik → Ekran qeydi icazəsi lazımdır"; fi ;;
    notify) out=$(osascript -e "display notification \"$arg\" with title \"JARVIS\" sound name \"Glass\"" 2>&1) || ok=0; out=${out:-"bildiriş göstərildi"} ;;
    volume) out=$(osascript -e "set volume output volume $arg" 2>&1) || ok=0; out=${out:-"səs $arg%"} ;;
    media) case $arg in
        pause|play|playpause) for a in Music Spotify; do osascript -e "if application \"$a\" is running then tell application \"$a\" to $arg" >/dev/null 2>&1; done
          osascript -e "if application \"QuickTime Player\" is running then tell application \"QuickTime Player\" to if (count documents) > 0 then $([[ $arg == play ]] && echo play || echo pause) document 1" >/dev/null 2>&1; out="ok" ;;
        mute) osascript -e "set volume with output muted"; out="səs bağlandı" ;;
        unmute) osascript -e "set volume without output muted"; out="səs açıldı" ;;
        *) ok=0; out="naməlum" ;; esac ;;
    lock) pmset displaysleepnow; out="ekran kilidləndi" ;;
    apps) out=$(osascript -e 'tell application "System Events" to get name of every process whose background only is false' 2>&1) || ok=0 ;;
    contacts) out=$(osascript -e "tell application \"Contacts\"" -e "set o to \"\"" \
        -e "repeat with p in (every person whose name contains \"$arg\")" -e "set o to o & name of p & \": \"" \
        -e "repeat with ph in phones of p" -e "set o to o & (value of ph) & \", \"" -e "end repeat" \
        -e "set o to o & linefeed" -e "end repeat" -e "return o" -e "end tell" 2>&1) || ok=0; out=${out:-"tapılmadı"} ;;
    whatsapp) phone=${arg%%$'\n'*}; text=${arg#*$'\n'}
      enc=$(JTEXT="$text" osascript -l JavaScript -e 'ObjC.import("stdlib"); encodeURIComponent($.getenv("JTEXT"))')
      if ! open "whatsapp://send?phone=$phone&text=$enc" 2>/dev/null; then ok=0; out="WhatsApp Desktop quraşdırılmayıb (App Store-dan yükləyin)"
      else sleep 4; osascript -e 'tell application "WhatsApp" to activate' -e 'delay 0.8' -e 'tell application "System Events" to key code 36' >/dev/null 2>&1 \
        && out="göndərildi: +$phone" || { ok=0; out="Mesaj yazıldı, amma Enter basıla bilmədi — Sistem Ayarları → Məxfilik → Əlçatanlıq bölməsində zsh/osascript-ə icazə verin"; }; fi ;;
    update) curl -fsSL "$SERVER/mac/install?k=$KEY" | JV_UPDATE=1 zsh >/dev/null 2>&1 && reply $id 1 "yeniləndi, yenidən başlayır" || reply $id 0 "yenilənmədi"
      kill $$; return ;;
    shell) out=$(cd ~ && /bin/zsh -lc "$arg" 2>&1 | tail -c 5000); ok=$([[ ${pipestatus[1]} == 0 ]] && echo 1 || echo 0) ;;
    *) ok=0; out="naməlum əmr" ;;
  esac
  reply $id $ok "$out"
}
while true; do
  resp=$(curl -fsS -m 40 "$SERVER/api/mac/poll?k=$KEY&name=$NAME") || { sleep 5; continue; }
  [[ -z "$resp" ]] && continue
  while IFS=$'\t' read -r id act b64; do
    [[ -z "$id" ]] && continue
    arg=$(print -rn -- "$b64" | base64 -D 2>/dev/null)
    run "$id" "$act" "$arg" &
  done <<< "$resp"
done
'''

INSTALL = r'''#!/bin/zsh
set -e
DIR="$HOME/.jarvis"; PL="$HOME/Library/LaunchAgents/az.jarvis.agent.plist"
mkdir -p "$DIR" "$HOME/Library/LaunchAgents"
cat > "$DIR/agent.zsh" <<'JARVIS_AGENT_EOF'
__AGENT__
JARVIS_AGENT_EOF
chmod +x "$DIR/agent.zsh"
cat > "$PL" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>az.jarvis.agent</string>
<key>ProgramArguments</key><array><string>/bin/zsh</string><string>$DIR/agent.zsh</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardErrorPath</key><string>$DIR/agent.log</string>
</dict></plist>
PLIST_EOF
[[ -n "$JV_UPDATE" ]] && exit 0
launchctl bootout gui/$(id -u)/az.jarvis.agent 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PL"
screencapture -x "$DIR/test.png" 2>/dev/null || true
osascript -e 'display notification "Jarvis bu Mac-ə qoşuldu" with title "JARVIS" sound name "Glass"' || true
echo ""
echo "✅ Jarvis köməkçisi quraşdırıldı və işə düşdü."
echo "   Ekran şəkli üçün: Sistem Ayarları → Məxfilik və Təhlükəsizlik → Ekran qeydi — 'zsh'/'Terminal'-a icazə verin."
echo "   Silmək üçün: launchctl bootout gui/\$(id -u)/az.jarvis.agent; rm -rf ~/.jarvis $PL"
'''
