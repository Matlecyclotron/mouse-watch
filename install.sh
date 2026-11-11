#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
# Installation ou mise à jour de rtsp-web + MediaMTX
# Debian 12 / 13 (amd64, arm64, armhf)
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Lancez ce script avec sudo." >&2; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/rtsp-web
ETC=/etc/rtsp-web
MTX_VERSION=v1.21.3
HLSJS_VERSION=1.5.13
VERSION=$(grep -oP '__version__ = "\K[^"]+' "$SRC/app.py")

case "$(dpkg --print-architecture)" in
  amd64) MTX_ARCH=linux_amd64 ;;
  arm64) MTX_ARCH=linux_arm64 ;;
  armhf) MTX_ARCH=linux_armv7 ;;
  *) echo "Architecture non prise en charge : $(dpkg --print-architecture)" >&2; exit 1 ;;
esac

echo "==> rtsp-web $VERSION"
echo "==> Paquets"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-flask python3-yaml python3-waitress python3-opencv python3-numpy \
  curl ca-certificates

echo "==> Utilisateurs système"
for u in rtsp-web mediamtx; do
  id "$u" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$u"
done

echo "==> MediaMTX $MTX_VERSION"
if ! /usr/local/bin/mediamtx --version 2>/dev/null | grep -q "$MTX_VERSION"; then
  TMP=$(mktemp -d)
  curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VERSION}/mediamtx_${MTX_VERSION}_${MTX_ARCH}.tar.gz" \
    | tar xz -C "$TMP"
  install -m 755 "$TMP/mediamtx" /usr/local/bin/mediamtx
  rm -rf "$TMP"
fi
install -d /etc/mediamtx
if [[ -f /etc/mediamtx/mediamtx.yml ]] && ! cmp -s "$SRC/mediamtx.yml" /etc/mediamtx/mediamtx.yml; then
  cp /etc/mediamtx/mediamtx.yml "/etc/mediamtx/mediamtx.yml.$(date +%Y%m%d%H%M%S).bak"
fi
install -m 644 "$SRC/mediamtx.yml" /etc/mediamtx/mediamtx.yml

echo "==> Application"
install -d "$APP/templates" "$APP/static"
install -m 644 "$SRC"/*.py "$APP/"
install -m 644 "$SRC"/templates/*.html "$APP/templates/"
install -m 644 "$SRC"/static/*.js "$SRC"/static/*.css "$APP/static/"
rm -f "$APP/templates/index.html"            # gabarit de la version 2
if [[ ! -s "$APP/static/hls.min.js" ]]; then
  # Lecteur de secours (HLS), copié en local : l'interface marche sans Internet
  curl -fsSL "https://cdn.jsdelivr.net/npm/hls.js@${HLSJS_VERSION}/dist/hls.min.js" \
       -o "$APP/static/hls.min.js" \
    || echo "ATTENTION : hls.js non téléchargé ; le repli HLS ne marchera que sur Safari." >&2
fi

echo "==> Configuration"
install -d -m 750 -g rtsp-web "$ETC"
if [[ ! -f "$ETC/config.yaml" ]]; then
  install -m 640 -g rtsp-web "$SRC/config.yaml" "$ETC/config.yaml"
  echo "    Exemple copié dans $ETC/config.yaml : indiquez l'adresse de vos caméras et le dossier du NAS."
else
  echo "    $ETC/config.yaml existant conservé."
  grep -q "^storage:" "$ETC/config.yaml" || \
    echo "    ATTENTION : pas de section « storage » : les photos resteront sur ce serveur. Modèle : $SRC/config.yaml"
  grep -q "detection: *true" "$ETC/config.yaml" || \
    echo "    ATTENTION : aucune caméra n'a « detection: true » : la détection n'apparaîtra pas dans l'interface."
  grep -q "^video:" "$ETC/config.yaml" || \
    echo "    Info : pas de section « video » : tampon vidéo de 30 minutes par défaut (modèle : $SRC/config.yaml)."
fi

NAS=$(python3 -c "import yaml;print(((yaml.safe_load(open('$ETC/config.yaml')) or {}).get('storage') or {}).get('nas_dir') or '')")
if [[ -n "$NAS" ]]; then
  if timeout 15 test -d "$NAS"; then
    if timeout 15 runuser -u rtsp-web -- test -w "$NAS"; then
      echo "    NAS : $NAS accessible en écriture."
    else
      echo "    ATTENTION : l'utilisateur rtsp-web ne peut pas écrire dans $NAS (options uid/gid du montage ou droits du NAS)."
    fi
  else
    echo "    ATTENTION : $NAS introuvable pour l'instant (partage non monté ?). Les photos attendront sur ce serveur."
  fi
fi

FREE_GB=$(df -BG --output=avail /var/lib | tail -1 | tr -dc '0-9')
if [[ -n "$FREE_GB" && "$FREE_GB" -lt 3 ]]; then
  echo "    ATTENTION : seulement ${FREE_GB} Go libres sous /var/lib ; le tampon vidéo demande 1 à 2 Go par heure et par caméra."
fi

echo "==> Services"
install -m 644 "$SRC/mediamtx.service" /etc/systemd/system/mediamtx.service
install -m 644 "$SRC/rtsp-web.service" /etc/systemd/system/rtsp-web.service
systemctl daemon-reload
systemctl enable mediamtx rtsp-web
systemctl restart mediamtx
systemctl restart rtsp-web

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  echo "    Pare-feu ufw actif : ouvrez 8080/tcp, 8889/tcp, 8189/udp et 8888/tcp au réseau local."
fi

PORT=$(python3 -c "import yaml;print(((yaml.safe_load(open('$ETC/config.yaml')) or {}).get('server') or {}).get('port',8080))")
IP=$(hostname -I | awk '{print $1}')
echo
echo "rtsp-web $VERSION installé. Interface : http://${IP}:${PORT}/"
echo "Journaux : journalctl -u rtsp-web -f   et   journalctl -u mediamtx -f"
