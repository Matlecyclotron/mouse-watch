# Historique des versions

## 4.0.1 — 2026-11-11

- Correction : après un changement brusque de luminosité, la détection
  pouvait rester bloquée (image entièrement vue « en mouvement ») jusqu'à
  un passage par la position Arrêtée. Le décor est désormais entièrement
  réappris après chaque changement global de l'image.
- Garde-fou : si l'image reste largement « en mouvement » plus de 20 s
  d'affilée, le décor est réappris automatiquement.
- Le décor n'est plus figé par de petites taches de bruit, seulement par un
  objet de taille suffisante : meilleure adaptation en faible lumière.
- Réglages : message pendant le réapprentissage du décor.

## 4.0.0 — 2026-11-10

- Extrait vidéo de chaque passage enregistré : de 2 s avant le début à 2 s
  après la fin (marges réglables de 0 à 10 s), au format MP4, rangé à côté
  de la photo sur le NAS.
- Enregistrement continu par MediaMTX dans un tampon local
  (`/var/lib/mediamtx/recordings`, 30 minutes par défaut), actif seulement
  en position Enregistrement avec l'extrait vidéo coché ; découpe sans
  réencodage par le service de relecture de MediaMTX.
- Nouveau module `recording.py` ; index des passages complété (état de
  l'extrait), mis à jour automatiquement.
- Interface : lecture des extraits et bascule vers la photo dans Passages,
  repère « vidéo » sur les vignettes, réglages vidéo par caméra, état du
  tampon.
- Configuration : section `video` (`buffer_minutes`) et clé
  `mediamtx.playback` ; MediaMTX : relecture activée sur 127.0.0.1:9996.

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
