#!/usr/bin/env bash
# Montaj Asistanı — avtomatik quraşdırma (Ubuntu 22.04/24.04).
# Hetzner "Cloud config" ilə və ya əl ilə:  curl -fsSL <raw>/install.sh | SETUP_TOKEN=xxx bash
set -euo pipefail
REPO="${REPO:-https://github.com/nhtomarr/montaj-asistani.git}"
DIR=/opt/montaj-asistani
export DEBIAN_FRONTEND=noninteractive

# Kiçik serverlərdə uzun render üçün swap
if [ ! -f /swapfile ] && [ "$(free -m | awk '/Mem:/{print $2}')" -lt 7000 ]; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

apt-get update -y
apt-get install -y git curl ca-certificates openssl
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh

if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone --depth 1 "$REPO" "$DIR"; fi
cd "$DIR"

if [ ! -f .env ]; then
  IP=$(curl -4 -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
  DOMAIN="${DOMAIN:-$(echo "$IP" | tr . -).sslip.io}"
  TOKEN="${SETUP_TOKEN:-$(openssl rand -hex 16)}"
  cat > .env <<ENV
DOMAIN=$DOMAIN
PUBLIC_URL=https://$DOMAIN
PANEL_TOKEN=$TOKEN
ENV
  chmod 600 .env
fi
mkdir -p data && touch data/setup.env && chmod 600 data/setup.env

# /setup səhifəsi yadda saxlayanda konteynerləri yenidən başladan xidmət
cat > /etc/systemd/system/montaj-restart.service <<UNIT
[Unit]
Description=Montaj asistani restart
[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=/bin/sh -c 'rm -f data/.restart; docker compose up -d --force-recreate app telegram-bot-api'
UNIT
cat > /etc/systemd/system/montaj-restart.path <<UNIT
[Unit]
Description=Montaj asistani restart trigger
[Path]
PathExists=$DIR/data/.restart
[Install]
WantedBy=multi-user.target
UNIT
# Telegram-dan /yenile → GitHub-dan son versiyanı çəkib yenidən yığır
cat > /etc/systemd/system/montaj-update.service <<UNIT
[Unit]
Description=Montaj asistani update
[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=/bin/sh -c 'rm -f data/.update; git pull --ff-only && docker compose up -d --build && echo ok > data/.update_result || echo fail > data/.update_result'
UNIT
cat > /etc/systemd/system/montaj-update.path <<UNIT
[Unit]
Description=Montaj asistani update trigger
[Path]
PathExists=$DIR/data/.update
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now montaj-restart.path montaj-update.path

docker compose up -d --build

set -a; . ./.env; set +a
echo
echo "=============================================="
echo " Hazırdır! Telefonda açın:"
echo " https://$DOMAIN/setup?t=$PANEL_TOKEN"
echo "=============================================="
