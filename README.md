# rtsp-web

Interface web de visionnage de caméras pour le réseau local, sous Debian,
avec détection de mouvement et photo de chaque passage.

```
Raspberry Pi + caméra          Serveur Debian                                  Navigateurs
(MediaMTX)          ──RTSP──►  MediaMTX (relais) ──WebRTC / HLS──────────────► Direct, Passages,
                               rtsp-web : détection (OpenCV), interface :8080   Réglages
                                   │
                                   └── photos ──► disque local ──► NAS (autofs)
```

## Fonctions

- **Direct** : mosaïque des caméras en quasi temps réel (WebRTC, repli HLS),
  zoom numérique, capture d'image, plein écran, et sélecteur de détection
  sur chaque vignette : *Arrêtée*, *Observation* ou *Enregistrement*.
- **Passages** : photos classées par nuit, agrandissement, téléchargement,
  suppression.
- **Réglages** (par caméra) : zones de détection tracées à main levée sur
  l'image en direct, zones d'exclusion, seuils, affichage du mouvement
  détecté, journal des dernières détections (retenues ou écartées, et
  pourquoi), état du stockage.

## Installation ou mise à jour (Debian 12 ou 13)

```bash
tar xzf rtsp-web.tar.gz && cd rtsp-web
sudo ./install.sh
sudo nano /etc/rtsp-web/config.yaml
sudo systemctl restart rtsp-web
```

Ouvrez ensuite `http://<adresse-du-serveur>:8080/`.

Le script installe MediaMTX (version figée), Flask, Waitress et OpenCV
depuis les dépôts Debian, crée deux utilisateurs système et deux services
démarrés au boot : `mediamtx` et `rtsp-web`. Il conserve un
`/etc/rtsp-web/config.yaml` existant et signale ce qui manque pour la
version 3.

**Depuis la version 2** : relancez `sudo ./install.sh`, puis ajoutez à
`/etc/rtsp-web/config.yaml` la section `storage`, `detection: true` sur la
caméra concernée et, si besoin, `rtsp_port` dans la section `mediamtx`
(modèle : `config.yaml` de l'archive).

## Configuration

Deux niveaux de réglages :

- **`/etc/rtsp-web/config.yaml`**, édité à la main : caméras, ports, dossier
  du NAS, durée de conservation, valeurs de départ des seuils. Redémarrer le
  service après modification.
- **L'interface (Réglages)** : position du sélecteur, zones, seuils. Pris en
  compte immédiatement et conservés dans `/var/lib/rtsp-web/settings.json`
  (fichier géré par l'application).

Extrait :

```yaml
storage:
  nas_dir: /mnt/nas/cameras              # vide : stockage local seul
  local_dir: /var/lib/rtsp-web/pending   # tampon si le NAS ne répond pas
  keep_days: 0                           # 0 = jamais d'effacement automatique

cameras:
  - id: terrier
    name: "Terrier"
    url: "rtsp://192.168.1.30:8554/cam"
    detection: true
```

## Stockage sur le NAS

Les photos sont rangées par caméra puis par date :
`/mnt/nas/cameras/terrier/2026-11-12/02-14-37.jpg`, accompagnées d'une
fiche `.json` (heure, durée, taille de l'objet).

Chaque photo est d'abord écrite sur le serveur, puis copiée sur le NAS dès
qu'il répond. Un NAS éteint ou injoignable ne bloque ni la détection ni
l'interface ; l'en-tête de l'interface indique son état et le nombre de
photos en attente.

Exemple de montage autofs d'un partage SMB (paquets `autofs` et `cifs-utils`) :

```bash
# /etc/auto.master.d/nas.autofs
/mnt/nas  /etc/auto.nas  --timeout=300

# /etc/auto.nas
cameras  -fstype=cifs,rw,uid=rtsp-web,gid=rtsp-web,file_mode=0664,dir_mode=0775,credentials=/etc/nas-credentials  ://192.168.1.10/cameras

# /etc/nas-credentials (chmod 600)
username=...
password=...
```

Pour un partage NFS (paquet `nfs-common`) :
`cameras  -fstype=nfs4,rw  192.168.1.10:/volume1/cameras`, en donnant au
dossier côté NAS les droits d'écriture pour l'utilisateur `rtsp-web` (même
identifiant numérique que sur le serveur, ou dossier ouvert en écriture).

Évitez l'option `--ghost` d'autofs : elle fait apparaître le dossier même
quand le partage n'est pas monté.

## Régler la détection

1. Dans **Réglages**, tracez la zone autour de l'entrée du terrier, au ras
   du sol. Ajoutez au besoin une zone d'exclusion sur un élément qui bouge
   au vent.
2. Passez en **Observation** pour une première nuit : rien n'est
   enregistré, mais le tableau « Dernières détections » indique ce qui a été
   retenu ou écarté, et pourquoi.
3. Ajustez les seuils :

   | Seuil | Effet | Départ |
   | --- | --- | --- |
   | Taille minimale | écarte ce qui est plus petit (insectes, feuilles) | 4 % |
   | Sensibilité | contraste nécessaire pour voir un mouvement | 6/10 |
   | Persistance | images consécutives avant de retenir un passage | 3 |
   | Délai entre deux passages | un aller-retour = une seule photo | 10 s |

4. Passez en **Enregistrement**.

Les changements brusques de toute l'image (allumage de l'éclairage
infrarouge, variation d'exposition) sont ignorés automatiquement.

## Réseau

| Port | Usage |
| --- | --- |
| 8080/tcp | interface web |
| 8889/tcp | WebRTC (établissement de la connexion) |
| 8189/udp | WebRTC (vidéo) |
| 8888/tcp | HLS (repli) |
| 8554/tcp | RTSP réémis par le serveur (facultatif : VLC, enregistreur) |

## Exploitation

```bash
systemctl status mediamtx rtsp-web
journalctl -u rtsp-web -f          # caméras, détection, passages, NAS
journalctl -u mediamtx -f          # connexions vidéo
curl http://localhost:8080/healthz
```

## Limites

- **Accès** : pas d'authentification ; l'interface est destinée à un réseau
  local de confiance. Ne pas l'exposer sur Internet sans reverse proxy avec
  HTTPS et authentification.
- **Codec** : WebRTC lit le H.264 partout ; le H.265 n'est lu que par
  certains navigateurs.
- **Charge** : la détection décode le flux complet de la caméra ; l'analyse
  elle-même, faite en 320 pixels de large, est légère.

## Organisation du code

| Fichier | Rôle |
| --- | --- |
| `app.py` | démarrage, pages et API web |
| `mediamtx.py` | déclaration des caméras auprès de MediaMTX, état des flux |
| `detection.py` | détection de mouvement (OpenCV) |
| `storage.py` | photos : écriture locale, copie vers le NAS, index SQLite |
| `settings.py` | réglages de l'interface (`settings.json`) |
| `templates/`, `static/` | interface web |

## Développement

```bash
sudo apt install python3-flask python3-yaml python3-opencv python3-numpy
mediamtx mediamtx.yml &
# dans config.yaml : storage.state_dir et storage.local_dir vers un dossier local
python3 app.py -c config.yaml -v
```

## Licence

rtsp-web est un logiciel libre, distribué sous licence
[GNU GPL version 3 ou ultérieure](LICENSE) (GPL-3.0-or-later). Vous pouvez
l'utiliser, l'étudier, le modifier et le redistribuer ; toute version
modifiée redistribuée doit l'être sous la même licence, avec son code source.

Les composants utilisés gardent leur propre licence : MediaMTX (MIT),
Flask et Waitress (BSD, ZPL), OpenCV et hls.js (Apache 2.0), tous
compatibles avec la GPL-3.0.
