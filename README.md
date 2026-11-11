# mouse-watch

*[Version française](README.fr.md)*

Self-hosted wildlife camera trap for the local network, running on Debian:
live view of RTSP cameras in the browser, motion detection, and a photo and
video clip of each passage.

> The repository is named mouse-watch; the application installs under its
> technical name, rtsp-web (service, folders and configuration).
>
> The web interface is currently in French. Interface labels are given below
> in French, with their English meaning.

```
Raspberry Pi + camera          Debian server                                   Browsers
(MediaMTX)          ──RTSP──►  MediaMTX (relay) ──WebRTC / HLS───────────────► Live view, Passages,
                               rtsp-web: detection (OpenCV), web UI :8080       Settings
                                   │
                                   └── photos and video clips ──► local disk ──► NAS (autofs)
```

## Features

- **Live view** (*Direct*): grid of all cameras in near real time (WebRTC,
  with HLS fallback), digital zoom, image capture, full screen, and a
  detection selector on each tile: *Arrêtée* (off), *Observation*
  (detect only) or *Enregistrement* (recording).
- **Passages**: photos and video clips grouped by night, with playback,
  download and deletion.
- **Settings** (*Réglages*, per camera): detection zones drawn freehand on
  the live image, exclusion zones, thresholds, live display of detected
  motion, log of recent detections (kept or discarded, and why), video clip
  and its margins before and after, storage status.

## Installing or upgrading (Debian 12 or 13)

```bash
curl -L -o mouse-watch-4.0.0.tar.gz https://github.com/<your-account>/mouse-watch/archive/refs/tags/v4.0.0.tar.gz
tar xzf mouse-watch-4.0.0.tar.gz && cd mouse-watch-4.0.0
sudo ./install.sh
sudo nano /etc/rtsp-web/config.yaml
sudo systemctl restart rtsp-web
```

Direct download requires a public repository. For a private repository, use
Git on the server, after `gh auth login`:

```bash
git clone https://github.com/<your-account>/mouse-watch.git
cd mouse-watch && git checkout v4.0.0
```

Then open `http://<server-address>:8080/`.

The script installs MediaMTX (pinned version), Flask, Waitress and OpenCV
from the Debian repositories, creates two system users and two services
started at boot: `mediamtx` and `rtsp-web`. It keeps an existing
`/etc/rtsp-web/config.yaml` and reports anything missing for version 4.

**From version 3**: run `sudo ./install.sh` again. Nothing else is required:
video clips are enabled by default, with a 30-minute buffer. The `video`
section of `config.yaml` changes this duration.

**From version 2**: run `sudo ./install.sh` again, then add to
`/etc/rtsp-web/config.yaml` the `storage` section, `detection: true` on the
camera concerned and, if needed, `rtsp_port` in the `mediamtx` section (see
the `config.yaml` template in the archive).

## Configuration

Settings come in two levels:

- **`/etc/rtsp-web/config.yaml`**, edited by hand: cameras, ports, NAS
  folder, retention, default thresholds. Restart the service after any
  change.
- **The web interface (*Réglages*)**: selector position, zones, thresholds,
  video clip and margins. Applied immediately and stored in
  `/var/lib/rtsp-web/settings.json` (managed by the application).

Excerpt:

```yaml
storage:
  nas_dir: /mnt/nas/cameras              # empty: local storage only
  local_dir: /var/lib/rtsp-web/pending   # buffer while the NAS is unreachable
  keep_days: 0                           # 0 = never delete automatically

video:
  buffer_minutes: 30                     # continuous recording buffer

cameras:
  - id: terrier
    name: "Terrier"
    url: "rtsp://192.168.1.30:8554/cam"
    detection: true
```

## Storage on the NAS

Photos are filed by camera, then by date:
`/mnt/nas/cameras/terrier/2026-11-12/02-14-37.jpg`, along with a `.json`
record (time, duration, object size).

Each photo is first written on the server, then copied to the NAS as soon as
it responds. A NAS that is off or unreachable blocks neither detection nor
the interface; the interface header shows its status and the number of
photos waiting.

Example autofs mount of an SMB share (packages `autofs` and `cifs-utils`):

```bash
# /etc/auto.master.d/nas.autofs
/mnt/nas  /etc/auto.nas  --timeout=300

# /etc/auto.nas
cameras  -fstype=cifs,rw,uid=rtsp-web,gid=rtsp-web,file_mode=0664,dir_mode=0775,credentials=/etc/nas-credentials  ://192.168.1.10/cameras

# /etc/nas-credentials (chmod 600)
username=...
password=...
```

For an NFS share (package `nfs-common`):
`cameras  -fstype=nfs4,rw  192.168.1.10:/volume1/cameras`, giving the
`rtsp-web` user write access to the folder on the NAS (same numeric ID as on
the server, or a world-writable folder).

Avoid the autofs `--ghost` option: it shows the folder even when the share
is not mounted.

## Video clips

Each recorded passage produces, in addition to the photo, an MP4 clip
covering 2 seconds before the passage starts and 2 seconds after it ends
(margins adjustable from 0 to 10 seconds in *Réglages*). The clip has the
same name as the photo (`02-14-37.mp4`) and follows it to the NAS.

To go back in time before the trigger, MediaMTX records the stream
continuously in `/var/lib/mediamtx/recordings` and deletes anything older
than the buffer duration (30 minutes by default). This recording only runs
for cameras set to *Enregistrement* with the video clip option checked. The
clip is cut without re-encoding a few seconds after the passage ends; it may
start slightly earlier than requested, on the previous keyframe.

If a clip fails, the photo is still kept, and the passage is marked "video
unavailable".

## Tuning detection

1. In *Réglages*, draw the zone around the burrow entrance, at ground level.
   If needed, add an exclusion zone over anything that moves in the wind.
2. Switch to *Observation* for a first night: nothing is recorded, but the
   *Dernières détections* (recent detections) table shows what was kept or
   discarded, and why.
3. Adjust the thresholds:

   | Threshold | Effect | Default |
   | --- | --- | --- |
   | Minimum size | discards anything smaller (insects, leaves) | 4 % |
   | Sensitivity | contrast needed to see motion | 6/10 |
   | Persistence | consecutive frames before a passage is kept | 3 |
   | Delay between passages | a round trip gives a single photo | 10 s |

4. Switch to *Enregistrement*.

Sudden changes across the whole image (infrared lighting switching on,
exposure changes) are ignored automatically.

## Network

| Port | Use |
| --- | --- |
| 8080/tcp | web interface |
| 8889/tcp | WebRTC (connection setup) |
| 8189/udp | WebRTC (video) |
| 8888/tcp | HLS (fallback) |
| 8554/tcp | RTSP re-streamed by the server (optional: VLC, recorder) |

The MediaMTX playback service (9996/tcp) only listens on the server itself:
there is no need to open it.

## Operation

```bash
systemctl status mediamtx rtsp-web
journalctl -u rtsp-web -f          # cameras, detection, passages, NAS
journalctl -u mediamtx -f          # video connections
curl http://localhost:8080/healthz
```

## Limitations

- **Access**: no authentication; the interface is meant for a trusted local
  network. Do not expose it to the Internet without a reverse proxy with
  HTTPS and authentication.
- **Codec**: WebRTC plays H.264 everywhere; H.265 only plays in some
  browsers.
- **Load**: detection decodes the full camera stream; the analysis itself,
  done at 320 pixels wide, is lightweight.
- **Server disk**: the video buffer uses 1 to 2 GB per hour and per camera
  in 1080p, i.e. 0.5 to 1 GB with the default setting. Each clip weighs 1 to
  3 MB on the NAS.
- **Language**: the interface, configuration comments and logs are in French.

## Code layout

| File | Role |
| --- | --- |
| `app.py` | startup, web pages and API |
| `mediamtx.py` | registers cameras with MediaMTX, stream status |
| `detection.py` | motion detection (OpenCV) |
| `storage.py` | photos: local write, copy to the NAS, SQLite index |
| `settings.py` | interface settings (`settings.json`) |
| `recording.py` | video clips, cut from MediaMTX continuous recording |
| `templates/`, `static/` | web interface |

## Development

```bash
sudo apt install python3-flask python3-yaml python3-opencv python3-numpy
mediamtx mediamtx.yml &
# in config.yaml: point storage.state_dir and storage.local_dir to a local folder
python3 app.py -c config.yaml -v
```

## License

mouse-watch (technical name: rtsp-web) is free software, released under the
[GNU GPL version 3 or later](LICENSE) (GPL-3.0-or-later). You may use,
study, modify and redistribute it; any modified version you redistribute
must be under the same license, with its source code.

The components used keep their own licenses: MediaMTX (MIT), Flask and
Waitress (BSD, ZPL), OpenCV and hls.js (Apache 2.0), all compatible with
GPL-3.0.
