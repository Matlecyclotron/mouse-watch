# mouse-watch

*[English version](README.md)*

Interface web de visionnage de caméras pour le réseau local, sous Debian,
avec détection de mouvement, photo et extrait vidéo de chaque passage, et
données d'activité exportables (horaires, soleil, lune, météo).

> Le dépôt s'appelle mouse-watch ; l'application s'installe sous son nom
> technique, rtsp-web (service, dossiers et configuration).

```
Raspberry Pi + caméra          Serveur Debian                                  Navigateurs
(MediaMTX)          ──RTSP──►  MediaMTX (relais) ──WebRTC / HLS──────────────► Direct, Passages,
                               rtsp-web : détection (OpenCV), interface :8080   Réglages
                                   │
                                   └── photos et extraits vidéo ──► disque local ──► NAS (autofs)
```

## Fonctions

- **Direct** : mosaïque des caméras en quasi temps réel (WebRTC, repli HLS),
  zoom numérique, capture d'image, plein écran, et sélecteur de détection
  sur chaque vignette : *Arrêtée*, *Observation* ou *Enregistrement*.
- **Passages** : photos et extraits vidéo classés par nuit, lecture,
  téléchargement, suppression, marquage (souris, autre animal, fausse
  alerte) et export des données d'activité pour LibreOffice ou en CSV.
- **Réglages** (par caméra) : zones de détection tracées à main levée sur
  l'image en direct, zones d'exclusion, seuils, affichage du mouvement
  détecté, journal des dernières détections (retenues ou écartées, et
  pourquoi), extrait vidéo et ses marges avant et après, état du stockage.

## Installation ou mise à jour (Debian 12 ou 13)

```bash
curl -L -o mouse-watch-5.0.0.tar.gz https://github.com/<votre-compte>/mouse-watch/archive/refs/tags/v5.0.0.tar.gz
tar xzf mouse-watch-5.0.0.tar.gz && cd mouse-watch-5.0.0
sudo ./install.sh
sudo nano /etc/rtsp-web/config.yaml
sudo systemctl restart rtsp-web
```

Le téléchargement direct suppose un dépôt public. Pour un dépôt privé,
passez par Git sur le serveur, après `gh auth login` :

```bash
git clone https://github.com/<votre-compte>/mouse-watch.git
cd mouse-watch && git checkout v5.0.0
```

Ouvrez ensuite `http://<adresse-du-serveur>:8080/`.

Le script installe MediaMTX (version figée), Flask, Waitress et OpenCV
depuis les dépôts Debian, crée deux utilisateurs système et deux services
démarrés au boot : `mediamtx` et `rtsp-web`. Il conserve un
`/etc/rtsp-web/config.yaml` existant et signale ce qui manque pour la
version 5.

**Depuis la version 4** : relancez `sudo ./install.sh`, puis ajoutez à
`/etc/rtsp-web/config.yaml` la section `site` (latitude et longitude) pour
obtenir les données de soleil, de lune et de météo. Vos passages existants
sont repris ; les durées d'observation ne sont connues qu'à partir de la
version 5.

**Depuis la version 3** : relancez `sudo ./install.sh`. Rien d'autre n'est
obligatoire : l'extrait vidéo est activé par défaut, avec un tampon de
30 minutes. La section `video` de `config.yaml` permet de changer cette durée.

**Depuis la version 2** : relancez `sudo ./install.sh`, puis ajoutez à
`/etc/rtsp-web/config.yaml` la section `storage`, `detection: true` sur la
caméra concernée et, si besoin, `rtsp_port` dans la section `mediamtx`
(modèle : `config.yaml` de l'archive).

## Configuration

Deux niveaux de réglages :

- **`/etc/rtsp-web/config.yaml`**, édité à la main : caméras, ports, dossier
  du NAS, durée de conservation, valeurs de départ des seuils. Redémarrer le
  service après modification.
- **L'interface (Réglages)** : position du sélecteur, zones, seuils, extrait
  vidéo et marges. Pris en
  compte immédiatement et conservés dans `/var/lib/rtsp-web/settings.json`
  (fichier géré par l'application).

Extrait :

```yaml
storage:
  nas_dir: /mnt/nas/cameras              # vide : stockage local seul
  local_dir: /var/lib/rtsp-web/pending   # tampon si le NAS ne répond pas
  keep_days: 0                           # 0 = jamais d'effacement automatique

video:
  buffer_minutes: 30                     # tampon de l'enregistrement continu

site:
  latitude: 47.50                        # au centième de degré : inutile d'être plus précis
  longitude: -2.00

weather:
  enabled: true                          # météo horaire depuis Open-Meteo

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

## Extraits vidéo

Chaque passage enregistré produit, en plus de la photo, un extrait MP4
couvrant 2 secondes avant le début du passage et 2 secondes après sa fin
(marges réglables de 0 à 10 secondes dans **Réglages**). L'extrait porte le
nom de la photo (`02-14-37.mp4`) et la suit sur le NAS.

Pour remonter avant le déclenchement, MediaMTX enregistre le flux en
continu dans `/var/lib/mediamtx/recordings` et efface ce qui dépasse la
durée du tampon (30 minutes par défaut). Cet enregistrement ne tourne que
pour les caméras en position **Enregistrement** avec l'extrait vidéo coché.
L'extrait est découpé sans réencodage, quelques secondes après la fin du
passage ; il peut commencer un peu plus tôt que demandé, sur l'image clé
précédente.

Si l'extrait échoue, la photo est tout de même conservée, et le passage est
marqué « vidéo indisponible ».

## Données d'activité

L'application collecte ce qu'il faut pour étudier l'activité dans le temps :
nombre de passages, horaires, et leur contexte.

- **Périodes d'observation** : le serveur note quand chaque caméra
  surveillait réellement (position *Enregistrement*, analyse active). Une
  nuit sans passage se distingue ainsi d'une nuit sans surveillance, et
  l'activité s'exprime en passages par heure d'observation.
- **Marquage** : dans **Passages**, chaque passage peut être marqué *Souris*,
  *Autre animal* ou *Fausse alerte* (au clavier : 1, 2, 3, et 0 pour retirer
  le marquage ; flèches pour passer au suivant). Les fausses alertes sont
  exclues des données. Supprimer une photo conserve le passage dans les
  comptes.
- **Soleil et lune** : calculés sur le serveur à partir des coordonnées du
  site (section `site` de `config.yaml`). Chaque passage est situé en heures
  après le coucher du soleil, mesure insensible au changement d'heure.
- **Météo** : température, pluie, nuages, vent et humidité, heure par heure,
  récupérés auprès d'[Open-Meteo](https://open-meteo.com) (données CC BY 4.0,
  issues de modèles météorologiques). Le serveur doit avoir accès à
  Internet ; seules les coordonnées, arrondies au centième de degré
  (environ 1 km), sont transmises.
- **Historique des réglages** : les changements de zone et de seuils sont
  datés, car ils modifient ce qui est détecté.

Le bouton **Télécharger les données**, dans la page Passages, propose la
période, la caméra, les passages retenus et le format :

- **Tableur LibreOffice (.ods)** : un classeur à trois feuilles, *Passages*
  (une ligne par passage), *Nuits* (une ligne par nuit, y compris sans
  passage) et *Notice* (signification de chaque colonne) ;
- **CSV (.zip)** : les mêmes tableaux en fichiers texte.

Chaque matin, les données complètes sont aussi copiées en CSV dans le
dossier `statistiques/` du NAS. Pour un script, une adresse directe existe :
`http://<serveur>:8080/api/export.csv?table=passages&depuis=2026-11-01`
(`table=nuits` pour l'autre tableau ; `jusqu`, `camera` et `retenus=souris`
sont aussi acceptés).

Un passage mesure une activité, pas un nombre d'animaux : le même animal
peut passer plusieurs fois, et l'entrée ne se distingue pas de la sortie.

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

Le service de relecture de MediaMTX (9996/tcp) n'écoute que sur le serveur
lui-même : il n'y a pas à l'ouvrir.

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
- **Disque du serveur** : le tampon vidéo occupe 1 à 2 Go par heure et par
  caméra en 1080p, soit 0,5 à 1 Go avec le réglage par défaut. Chaque extrait
  pèse 1 à 3 Mo sur le NAS.

## Organisation du code

| Fichier | Rôle |
| --- | --- |
| `app.py` | démarrage, pages et API web |
| `mediamtx.py` | déclaration des caméras auprès de MediaMTX, état des flux |
| `detection.py` | détection de mouvement (OpenCV) |
| `storage.py` | photos : écriture locale, copie vers le NAS, index SQLite |
| `settings.py` | réglages de l'interface (`settings.json`) |
| `recording.py` | extraits vidéo, tirés de l'enregistrement continu de MediaMTX |
| `stats.py` | périodes d'observation, météo, tableaux de données d'activité |
| `astro.py` | soleil et lune, calculés localement |
| `export.py` | export ODS (LibreOffice) et CSV |
| `templates/`, `static/` | interface web |

## Développement

```bash
sudo apt install python3-flask python3-yaml python3-opencv python3-numpy
mediamtx mediamtx.yml &
# dans config.yaml : storage.state_dir et storage.local_dir vers un dossier local
python3 app.py -c config.yaml -v
```

## Licence

mouse-watch (nom technique : rtsp-web) est un logiciel libre, distribué sous licence
[GNU GPL version 3 ou ultérieure](LICENSE) (GPL-3.0-or-later). Vous pouvez
l'utiliser, l'étudier, le modifier et le redistribuer ; toute version
modifiée redistribuée doit l'être sous la même licence, avec son code source.

Les composants utilisés gardent leur propre licence : MediaMTX (MIT),
Flask et Waitress (BSD, ZPL), OpenCV et hls.js (Apache 2.0), tous
compatibles avec la GPL-3.0.
