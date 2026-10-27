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
           "volume", "media", "lock", "apps", "shell", "contacts", "whatsapp", "update",
           "shot", "click", "type", "key", "scroll", "perm_check")


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
    if action not in ("shell", "whatsapp", "type"):
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
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
SERVER="__SERVER__"; KEY="__KEY__"; DIR="$HOME/.jarvis"; MOV="$HOME/Movies/Jarvis"
mkdir -p "$DIR" "$MOV"
if [[ ! -f "$DIR/.perm" ]]; then  # ilk işə düşəndə macOS icazə pəncərələrini bir dəfə çıxart
  touch "$DIR/.perm"
  ( sleep 3; osascript -e 'tell application "Contacts" to count people'
    osascript -e 'tell application "System Events" to keystroke ""'
    screencapture -x "$DIR/test.png"
    osascript -e 'display notification "Jarvis bu Mac-ə qoşuldu" with title "JARVIS" sound name "Glass"' ) >/dev/null 2>&1 &
fi
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
      if [[ -s "$f" ]]; then reply $id 1 "ekran şəkli" "$f"; return; else ok=0; out="Ekran şəkli alınmadı — Sistem Ayarları → Gizlilik → Ekran Kaydı bölməsinə Jarvis.app əlavə edin"; fi ;;
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
    perm_check)
      c1=$(osascript -e 'tell application "Contacts" to count people' 2>&1)
      c2=$(osascript -e 'tell application "System Events" to get name of first process' 2>&1)
      rm -f "$DIR/pc.png"; screencapture -x -m "$DIR/pc.png" 2>&1; [[ -s "$DIR/pc.png" ]] && c3="ok" || c3="YOX"
      out="Kontaktlar: $c1 | System Events: $c2 | Ekran: $c3 | proses: $(ps -o comm= -p $PPID) " ;;
    shot) f="$DIR/op.png"; rm -f "$f" "$DIR/op.jpg"; screencapture -x -m "$f" 2>/dev/null
      if [[ -s "$f" ]]; then
        sips --resampleWidth 1280 -s format jpeg -s formatOptions 70 "$f" --out "$DIR/op.jpg" >/dev/null 2>&1
        pw=$(osascript -l JavaScript -e 'ObjC.import("AppKit"); $.NSScreen.mainScreen.frame.size.width')
        reply $id 1 "$pw 1280" "$DIR/op.jpg"; return
      else ok=0; out="Ekran şəkli alınmadı — Ekran Kaydı bölməsinə Jarvis.app əlavə edin"; fi ;;
    click) out=$(JA="$arg" osascript -l JavaScript -e '
        ObjC.import("CoreGraphics"); ObjC.import("stdlib");
        var a=$.getenv("JA").split(" "), p={x:+a[0], y:+a[1]}, r=(a[2]=="right"), n=+(a[3]||1);
        function ev(t,c){var e=$.CGEventCreateMouseEvent(null,t,p,r?1:0); if(c) $.CGEventSetIntegerValueField(e,1,c); $.CGEventPost(0,e);}
        ev(5,0); delay(0.05);
        for (var i=1;i<=n;i++){ ev(r?3:1,i); ev(r?4:2,i); delay(0.05); }
        "klik " + a[0] + "," + a[1];' 2>&1) || ok=0 ;;
    type) print -rn -- "$arg" | pbcopy
      out=$(osascript -e 'tell application "System Events" to keystroke "v" using command down' 2>&1) || ok=0; out=${out:-"yazıldı"} ;;
    key) k=${arg##*+}; mods=""
      [[ $arg == *cmd+* || $arg == *command+* ]] && mods+="command down, "
      [[ $arg == *shift+* ]] && mods+="shift down, "
      [[ $arg == *alt+* || $arg == *option+* ]] && mods+="option down, "
      [[ $arg == *ctrl+* || $arg == *control+* ]] && mods+="control down, "
      mods=${mods%, }
      case $k in return|enter) kc=36;; tab) kc=48;; space) kc=49;; delete|backspace) kc=51;; esc|escape) kc=53;;
        left) kc=123;; right) kc=124;; down) kc=125;; up) kc=126;; pagedown) kc=121;; pageup) kc=116;; home) kc=115;; end) kc=119;; *) kc="";; esac
      if [[ -n $kc ]]; then c="key code $kc"; else c="keystroke \"${k//\"/}\""; fi
      [[ -n $mods ]] && c="$c using {$mods}"
      out=$(osascript -e "tell application \"System Events\" to $c" 2>&1) || ok=0; out=${out:-"ok"} ;;
    scroll) out=$(JA="$arg" osascript -l JavaScript -e '
        ObjC.import("CoreGraphics"); ObjC.import("stdlib");
        var d=+$.getenv("JA"); var e=$.CGEventCreateScrollWheelEvent2(null,0,1,d,0,0); $.CGEventPost(0,e); "ok"' 2>&1) || ok=0 ;;
    contacts) out=$(osascript -e "tell application \"Contacts\"" -e "set o to \"\"" \
        -e "repeat with p in (every person whose name contains \"$arg\")" -e "set o to o & name of p & \": \"" \
        -e "repeat with ph in phones of p" -e "set o to o & (value of ph) & \", \"" -e "end repeat" \
        -e "set o to o & linefeed" -e "end repeat" -e "return o" -e "end tell" 2>&1) || ok=0; out=${out:-"tapılmadı"} ;;
    whatsapp) phone=${arg%%$'\n'*}; text=${arg#*$'\n'}
      enc=$(JTEXT="$text" osascript -l JavaScript -e 'ObjC.import("stdlib"); encodeURIComponent($.getenv("JTEXT"))')
      if ! open "whatsapp://send?phone=$phone&text=$enc" 2>/dev/null; then ok=0; out="WhatsApp Desktop quraşdırılmayıb (App Store-dan yükləyin)"
      else sleep 4; osascript -e 'tell application "WhatsApp" to activate' -e 'delay 0.8' -e 'tell application "System Events" to key code 36' >/dev/null 2>&1 \
        && out="göndərildi: +$phone" || { ok=0; out="Mesaj yazıldı, amma Enter basıla bilmədi — Sistem Ayarları → Gizlilik → Erişilebilirlik bölməsinə Jarvis.app əlavə edin"; }; fi ;;
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
DIR="$HOME/.jarvis"; PL="$HOME/Library/LaunchAgents/az.jarvis.agent.plist"; APP="$HOME/Applications/Jarvis.app"
mkdir -p "$DIR" "$HOME/Library/LaunchAgents" "$HOME/Applications"
cat > "$DIR/agent.zsh" <<'JARVIS_AGENT_EOF'
__AGENT__
JARVIS_AGENT_EOF
chmod +x "$DIR/agent.zsh"
[[ -n "$JV_UPDATE" ]] && exit 0
# macOS icazələri (Kontaktlar, WhatsApp, ekran) arxa fon skriptinə yox, tətbiqə verilir — ona görə kiçik Jarvis.app
rm -rf "$APP"
osacompile -o "$APP" -e 'do shell script "/bin/zsh $HOME/.jarvis/agent.zsh > /dev/null 2>&1"'
IP="$APP/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string az.jarvis.helper "$IP"
plutil -replace CFBundleName -string Jarvis "$IP"
plutil -replace LSUIElement -bool true "$IP"
plutil -replace NSAppleEventsUsageDescription -string "Jarvis sizin əmrinizlə proqramları idarə edir (WhatsApp, QuickTime, Kontaktlar)." "$IP"
plutil -replace NSContactsUsageDescription -string "Jarvis mesaj göndərmək üçün kontaktın nömrəsini tapır." "$IP"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
rm -f "$DIR/.perm"
cat > "$PL" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>az.jarvis.agent</string>
<key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-W</string><string>-g</string><string>-a</string><string>$APP</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardErrorPath</key><string>$DIR/agent.log</string>
</dict></plist>
PLIST_EOF
pkill -f "$DIR/agent.zsh" 2>/dev/null || true
pkill -f "Jarvis.app/Contents/MacOS/applet" 2>/dev/null || true
launchctl bootout gui/$(id -u)/az.jarvis.agent 2>/dev/null || true
sleep 1
launchctl bootstrap gui/$(id -u) "$PL"
echo ""
echo "✅ Jarvis köməkçisi quraşdırıldı (Jarvis.app) və işə düşdü."
echo "   İndi ekranda «Jarvis ... idarə etmək istəyir» pəncərələri çıxacaq — hamısında «İzin Ver» basın."
echo "   Sonra Sistem Ayarları → Gizlilik → Erişilebilirlik və Ekran Kaydı-na + ilə ~/Applications/Jarvis.app əlavə edin."
echo "   Silmək üçün: launchctl bootout gui/\$(id -u)/az.jarvis.agent; rm -rf ~/.jarvis $APP $PL"
'''
