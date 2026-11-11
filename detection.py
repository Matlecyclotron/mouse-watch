# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Détection de mouvement, une instance par caméra.

Le flux est lu localement depuis MediaMTX, analysé en petite résolution
(soustraction de fond), et filtré : zones dessinées dans l'interface,
taille minimale, persistance, rejet des changements globaux, délai entre
deux passages. La photo retenue est l'image en pleine résolution où
l'objet est le plus grand.
"""
import json
import logging
import threading
import time
from collections import deque

import cv2
import numpy as np

log = logging.getLogger("rtsp-web.detection")

ANALYSIS_WIDTH = 320
MAX_FPS = 15                 # images analysées par seconde au plus
WARMUP_FRAMES = 30           # apprentissage initial du décor
NOISE_FLOOR = 0.3            # % de l'image : en dessous, ce n'est rien
GLOBAL_CHANGE = 45.0         # % de l'image qui change d'un coup : lumière
GLOBAL_PAUSE = 3.0           # secondes d'attente après un changement global
END_GAP = 0.7                # secondes sans mouvement pour clore un passage
MAX_EVENT = 60.0             # durée maximale d'un passage
LEARNING_RATE = 0.005        # adaptation du décor (≈ 15 s à 15 im/s)
FREEZE_MAX = 60.0            # gel maximal du décor pendant une présence
LOG_SIZE = 50

RESULTS = {
    "saved": "Passage enregistré",
    "observed": "Passage détecté (observation)",
    "cooldown": "Ignoré : délai entre deux passages",
    "small": "Écarté : trop petit",
    "brief": "Écarté : trop bref",
    "global": "Écarté : changement global",
}


def var_threshold(sensitivity: int) -> float:
    """Sensibilité 1..10 → seuil de la soustraction de fond (64 → 10)."""
    return float(70 - 6 * int(sensitivity))


class Detector(threading.Thread):

    def __init__(self, camera_id: str, url: str, settings, storage, recorder=None):
        super().__init__(daemon=True, name=f"detect-{camera_id}")
        self.cid = camera_id
        self.url = url
        self.settings = settings
        self.storage = storage
        self.recorder = recorder
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.cap = None
        self.state = "off"
        self.error = ""
        self.fps = 0.0
        self.log = deque(maxlen=LOG_SIZE)
        self.last_mask = None
        settings.subscribe(self._on_settings)
        self._reset_analysis()

    # ---------------------------------------------------------- état interne
    def _reset_analysis(self):
        self.bg = None
        self.bg_sensitivity = None
        self.frames = 0
        self.zone_key = None
        self.zone_mask = None
        self.episode = None
        self.pause_until = 0.0
        self.relearn = False
        self.last_event_end = 0.0

    def _on_settings(self, cid):
        if cid == self.cid:
            self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "error": self.error, "fps": round(self.fps, 1)}

    def detections(self) -> list:
        with self._lock:
            return list(self.log)

    def mask_png(self):
        with self._lock:
            fg = None if self.last_mask is None else self.last_mask.copy()
        if fg is None:
            return None
        rgba = np.zeros((*fg.shape, 4), np.uint8)
        rgba[fg > 0] = (224, 168, 110, 150)        # BGRA : bleu clair translucide
        ok, png = cv2.imencode(".png", rgba)
        return png.tobytes() if ok else None

    def _set_state(self, state: str, error: str = ""):
        with self._lock:
            if state != self.state:
                log.info("[%s] détection : %s", self.cid, state)
            self.state, self.error = state, error

    # ------------------------------------------------------------ lecture
    def _open(self):
        open_t = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
        read_t = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
        cap = None
        if open_t is not None and read_t is not None:
            try:
                cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [open_t, 10000, read_t, 10000])
            except (TypeError, cv2.error):
                cap = None
        if cap is None:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            return None
        return cap

    def _release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
            self._reset_analysis()

    def run(self):
        backoff = 2
        last_analysis = 0.0
        fps_count, fps_since = 0, time.monotonic()
        while not self._stop.is_set():
            cfg = self.settings.get(self.cid)
            if cfg["mode"] == "off":
                self._release()
                self._set_state("off")
                with self._lock:
                    self.last_mask = None
                self._wake.wait(5)
                self._wake.clear()
                continue

            if self.cap is None:
                self._set_state("connecting")
                self.cap = self._open()
                if self.cap is None:
                    self._set_state("error", "Flux de la caméra indisponible pour l'analyse.")
                    self._wake.wait(backoff)
                    self._wake.clear()
                    backoff = min(backoff * 2, 30)
                    continue
                backoff = 2

            if not self.cap.grab():
                self._release()
                self._set_state("error", "Lecture du flux interrompue, reconnexion…")
                time.sleep(1)
                continue

            now = time.monotonic()
            if now - last_analysis < 1.0 / MAX_FPS:
                continue                       # image lue mais pas décodée en couleur
            ok, frame = self.cap.retrieve()
            if not ok or frame is None:
                continue
            last_analysis = now
            self._set_state("running")
            try:
                self._analyse(frame, cfg, now)
            except cv2.error as exc:
                log.exception("[%s] erreur d'analyse : %s", self.cid, exc)

            fps_count += 1
            if now - fps_since >= 2:
                with self._lock:
                    self.fps = fps_count / (now - fps_since)
                fps_count, fps_since = 0, now
        self._release()

    # ------------------------------------------------------------ analyse
    def _zone_mask(self, zones: dict, w: int, h: int):
        key = (json.dumps(zones, sort_keys=True), w, h)
        if key != self.zone_key:
            include, exclude = zones.get("include", []), zones.get("exclude", [])
            mask = np.zeros((h, w), np.uint8) if include else np.full((h, w), 255, np.uint8)
            scale = np.array([w, h], np.float32)
            for poly in include:
                cv2.fillPoly(mask, [np.round(np.array(poly, np.float32) * scale).astype(np.int32)], 255)
            for poly in exclude:
                cv2.fillPoly(mask, [np.round(np.array(poly, np.float32) * scale).astype(np.int32)], 0)
            self.zone_key, self.zone_mask = key, mask
        return self.zone_mask

    def _analyse(self, frame, cfg: dict, now: float):
        fh, fw = frame.shape[:2]
        aw = ANALYSIS_WIDTH
        ah = max(1, round(fh * aw / fw))
        small = cv2.resize(frame, (aw, ah), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)

        if self.bg is None or self.bg_sensitivity != cfg["sensitivity"]:
            self.bg = cv2.createBackgroundSubtractorMOG2(
                history=500, varThreshold=var_threshold(cfg["sensitivity"]), detectShadows=False)
            self.bg_sensitivity = cfg["sensitivity"]
            self.frames = 0
            self.episode = None

        self.frames += 1
        if self.frames <= WARMUP_FRAMES:
            self.bg.apply(gray, learningRate=-1)
            return
        if self.relearn:
            lr, self.relearn = 1.0, False
        elif self.episode and now - self.episode["mono_start"] < FREEZE_MAX:
            lr = 0.0                           # présence : le décor ne bouge pas
        elif now < self.pause_until:
            lr = 0.05
        else:
            lr = LEARNING_RATE
        fg = self.bg.apply(gray, learningRate=lr)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((5, 5), np.uint8))
        with self._lock:
            self.last_mask = fg

        total = 100.0 * cv2.countNonZero(fg) / fg.size
        if total >= GLOBAL_CHANGE:
            if now >= self.pause_until:
                self._log(time.time(), total, 0.0, "global")
            self.episode = None
            self.relearn = True
            self.pause_until = now + GLOBAL_PAUSE
            return
        if now < self.pause_until:
            return

        zone = cv2.bitwise_and(fg, self._zone_mask(cfg["zones"], aw, ah))
        contours = cv2.findContours(zone, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        size = 100.0 * max((cv2.contourArea(c) for c in contours), default=0.0) / (aw * ah)
        self._track(frame, size, cfg, now)

    def _track(self, frame, size: float, cfg: dict, now: float):
        wall = time.time()
        ep = self.episode
        if size >= NOISE_FLOOR:
            if ep is None:
                ep = self.episode = {"mono_start": now, "start": wall, "last": now,
                                     "last_wall": wall, "max": 0.0, "consec": 0,
                                     "event": False, "best": 0.0, "frame": None}
            ep["last"], ep["last_wall"] = now, wall
            ep["max"] = max(ep["max"], size)
            if size >= cfg["min_size"]:
                ep["consec"] += 1
                if size > ep["best"]:
                    ep["best"], ep["frame"] = size, frame.copy()
                if ep["consec"] >= cfg["persistence"]:
                    ep["event"] = True
            else:
                ep["consec"] = 0
            if now - ep["mono_start"] >= MAX_EVENT:
                self._finish(cfg)
        elif ep is not None and now - ep["last"] > END_GAP:
            self._finish(cfg)

    def _finish(self, cfg: dict):
        ep, self.episode = self.episode, None
        start, end = ep["start"], ep["last_wall"]
        if ep["event"]:
            if start - self.last_event_end < cfg["cooldown"]:
                result = "cooldown"
            elif cfg["mode"] == "record":
                want_clip = self.recorder is not None and cfg.get("video", False)
                try:
                    eid = self.storage.save_event(self.cid, ep["frame"], start, end,
                                                  ep["best"], clip_expected=want_clip)
                    if want_clip:
                        self.recorder.request(eid, self.cid, start, end,
                                              cfg["pre_roll"], cfg["post_roll"])
                    result = "saved"
                except Exception:              # noqa: BLE001 — ne jamais arrêter la détection
                    log.exception("[%s] enregistrement impossible", self.cid)
                    result = "observed"
            else:
                result = "observed"
            if result != "cooldown":
                self.last_event_end = end
            if result != "saved":
                log.info("[%s] %s (%.1f %%)", self.cid, RESULTS[result], ep["best"])
        elif ep["max"] < cfg["min_size"]:
            result = "small"
        else:
            result = "brief"
        self._log(start, ep["max"], end - start, result)

    def _log(self, start: float, size: float, duration: float, result: str):
        with self._lock:
            self.log.appendleft({"time": start, "size": round(size, 1),
                                 "duration": round(duration, 1), "result": result,
                                 "label": RESULTS[result]})
