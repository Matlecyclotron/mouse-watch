# Historique des versions

## 3.0.0 — 2026-11-09

- Détection de mouvement par caméra (OpenCV) : zones tracées à main levée,
  zones d'exclusion, taille minimale, sensibilité, persistance, délai entre
  deux passages, rejet des changements globaux de l'image.
- Sélecteur à trois positions (arrêtée, observation, enregistrement) sur la
  page Direct et dans les réglages, conservé après redémarrage.
- Photo en pleine résolution de chaque passage, écrite localement puis
  copiée sur le NAS ; état du NAS affiché dans l'interface.
- Nouvelles pages : Passages (photos par nuit) et Réglages (par caméra).
- Configuration : sections `storage` et `detection_defaults`, clé
  `detection` par caméra ; réglages de l'interface dans
  `/var/lib/rtsp-web/settings.json`.
- Service systemd : dossier d'état `/var/lib/rtsp-web`, `ProtectSystem=full`
  pour l'écriture sur un partage monté sous `/mnt`.

## 2.0.0 — 2026-11-08

- MediaMTX installé sur le serveur comme relais ; lecture WebRTC (moins
  d'une seconde de latence) avec repli HLS.
- Caméras déclarées automatiquement auprès de MediaMTX par son API.
- Interface : zoom numérique, capture d'image, plein écran.

## 1.0.0 — 2026-11-08

- Première version : conversion RTSP vers HLS par FFmpeg, mosaïque web.
