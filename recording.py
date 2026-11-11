# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Extraits vidéo des passages (version 4).

MediaMTX enregistre le flux de chaque caméra en continu dans un tampon
local qu'il efface au-delà d'une durée choisie. À la fin d'un passage, ce
module demande au service de relecture de MediaMTX la portion allant de
« début − marge avant » à « fin + marge après », sans réencodage, puis la
confie au stockage, qui la range à côté de la photo.
"""
import datetime as dt
import heapq
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import cv2

log = logging.getLogger("rtsp-web.recording")

FLUSH_DELAY = 3.0       # secondes laissées à MediaMTX pour écrire la fin
RETRY_DELAY = 3.0
MAX_ATTEMPTS = 6
SIZE_REFRESH = 30.0     # secondes entre deux mesures de la taille du tampon


def go_duration(seconds: int) -> str:
    """Durée au format renvoyé par MediaMTX (ex. 1800 → « 30m0s »)."""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if h or m else "") + f"{s}s"


def clip_duration(data: bytes) -> float:
    """Durée d'un MP4 en secondes (0 si illisible)."""
    fd, path = tempfile.mkstemp(suffix=".mp4")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        cap = cv2.VideoCapture(path)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return frames / fps if fps > 0 and frames > 0 else 0.0
    finally:
        os.remove(path)


class Recorder(threading.Thread):

    def __init__(self, storage, playback_url: str, recordings_dir: str, buffer_minutes: int):
        super().__init__(daemon=True, name="recorder")
        self.storage = storage
        self.playback = playback_url.rstrip("/")
        self.recordings_dir = recordings_dir
        self.buffer_minutes = max(1, int(buffer_minutes))
        self._jobs = []
        self._seq = 0
        self._cv = threading.Condition()
        self._sizes = {}
        self._sizes_at = 0.0
        self.last_error = ""

    # ------------------------------------------------------- configuration
    def record_config(self, wanted: bool) -> dict:
        """Réglages d'enregistrement à appliquer à un chemin MediaMTX."""
        if not wanted:
            return {"record": False}
        return {
            "record": True,
            "recordPath": os.path.join(self.recordings_dir, "%path", "%Y-%m-%d_%H-%M-%S-%f"),
            "recordFormat": "fmp4",
            "recordPartDuration": "1s",
            "recordSegmentDuration": "1m0s",
            "recordDeleteAfter": go_duration(self.buffer_minutes * 60),
        }

    # -------------------------------------------------------------- tâches
    def request(self, eid: str, camera: str, start: float, end: float, pre: int, post: int):
        job = {"eid": eid, "camera": camera, "from": start - pre,
               "duration": (end - start) + pre + post, "attempts": 0}
        with self._cv:
            self._seq += 1
            heapq.heappush(self._jobs, (end + post + FLUSH_DELAY, self._seq, job))
            self._cv.notify()

    def _fetch(self, job: dict):
        start = dt.datetime.fromtimestamp(job["from"], dt.timezone.utc)
        query = urllib.parse.urlencode({
            "path": job["camera"],
            "start": start.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "duration": f"{job['duration']:.3f}",
            "format": "mp4",
        })
        try:
            with urllib.request.urlopen(f"{self.playback}/get?{query}", timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:                # rien d'enregistré sur cette période
                return None
            raise

    def _process(self, job: dict):
        job["attempts"] += 1
        try:
            data = self._fetch(job)
        except (urllib.error.URLError, OSError) as exc:
            self.last_error = f"relecture MediaMTX injoignable : {exc}"
            data = None
        got = clip_duration(data) if data else 0.0
        # Sans réencodage, l'extrait commence sur une image clé : il peut être
        # un peu plus long que demandé, jamais plus court s'il est complet.
        complete = got >= job["duration"] - 0.5 or (data and got == 0.0)
        if complete or job["attempts"] >= MAX_ATTEMPTS:
            if data and not complete:
                log.warning("[%s] extrait incomplet (%.1f s sur %.1f s)",
                            job["camera"], got, job["duration"])
            if data:
                self.last_error = ""
            self.storage.attach_clip(job["eid"], data or None)
            return
        with self._cv:
            self._seq += 1
            heapq.heappush(self._jobs, (time.time() + RETRY_DELAY, self._seq, job))

    def run(self):
        while True:
            with self._cv:
                while not self._jobs or self._jobs[0][0] > time.time():
                    timeout = (self._jobs[0][0] - time.time()) if self._jobs else None
                    self._cv.wait(timeout)
                _, _, job = heapq.heappop(self._jobs)
            try:
                self._process(job)
            except Exception:                  # noqa: BLE001 — ne jamais arrêter le module
                log.exception("[%s] extrait vidéo impossible", job["camera"])
                self.storage.attach_clip(job["eid"], None)

    # ---------------------------------------------------------------- état
    def buffer_bytes(self, camera: str) -> int:
        if time.time() - self._sizes_at > SIZE_REFRESH:
            sizes = {}
            try:
                for name in os.listdir(self.recordings_dir):
                    folder = os.path.join(self.recordings_dir, name)
                    total = 0
                    for root, _dirs, files in os.walk(folder):
                        for f in files:
                            try:
                                total += os.path.getsize(os.path.join(root, f))
                            except OSError:
                                pass
                    sizes[name] = total
            except OSError:
                pass
            self._sizes, self._sizes_at = sizes, time.time()
        return self._sizes.get(camera, 0)

    def pending(self) -> int:
        with self._cv:
            return len(self._jobs)
