# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Relais vidéo : déclaration des caméras auprès de MediaMTX (API locale)
et suivi de leur état.
"""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

log = logging.getLogger("rtsp-web")

CRED_RE = re.compile(r"(rtsps?://)[^/@\s]+@")
CONNECT_GRACE = 15          # secondes avant de déclarer une caméra hors ligne
POLL_INTERVAL = 3
RESYNC_INTERVAL = 30


def mask(text: str) -> str:
    """Masque les identifiants éventuellement présents dans une URL RTSP."""
    return CRED_RE.sub(r"\1***@", text or "")


class MediaMTXError(Exception):
    pass


class MediaMTX:
    """Client minimal de l'API de contrôle de MediaMTX (v3)."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def call(self, method: str, path: str, body: dict = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise MediaMTXError(f"{method} {path} : HTTP {exc.code} {detail}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise MediaMTXError(f"MediaMTX injoignable ({exc})") from None


class CameraManager(threading.Thread):
    """Synchronise les caméras avec MediaMTX et tient leur état à jour."""

    def __init__(self, cameras: list, api: MediaMTX, record_config=None):
        super().__init__(daemon=True, name="camera-manager")
        self.cameras = cameras
        self.api = api
        # Fonction (id de caméra) -> réglages d'enregistrement MediaMTX (version 4)
        self.record_config = record_config or (lambda cid: {"record": False})
        self.started = time.time()
        self._lock = threading.Lock()
        self._status = {c["id"]: self._blank(c) for c in cameras}
        self._last_sync = 0.0
        self._need_sync = True

    @staticmethod
    def _blank(cam: dict) -> dict:
        return {"id": cam["id"], "name": cam.get("name", cam["id"]),
                "state": "connecting", "since": int(time.time()),
                "error": "", "codecs": [], "viewers": 0}

    @staticmethod
    def desired(cam: dict) -> dict:
        return {
            "source": cam["url"],
            "sourceOnDemand": bool(cam.get("on_demand", False)),
            "rtspTransport": cam.get("rtsp_transport", "tcp"),
        }

    def request_sync(self):
        """Demande une synchronisation rapide (changement de réglages)."""
        self._need_sync = True

    # ------------------------------------------------------------ synchro
    def sync(self):
        current = {p["name"]: p for p in
                   self.api.call("GET", "/v3/config/paths/list?itemsPerPage=1000")["items"]}
        wanted_ids = set()
        for cam in self.cameras:
            sid, want = cam["id"], self.desired(cam)
            record = self.record_config(sid)
            wanted_ids.add(sid)
            conf = current.get(sid)
            if conf is None:
                self.api.call("POST", f"/v3/config/paths/add/{quote(sid)}", {**want, **record})
                log.info("[%s] caméra déclarée : %s", sid, mask(cam["url"]))
            elif any(conf.get(k) != v for k, v in want.items()):
                # source modifiée : MediaMTX relance la connexion à la caméra
                self.api.call("POST", f"/v3/config/paths/replace/{quote(sid)}", {**want, **record})
                log.info("[%s] configuration mise à jour", sid)
            elif any(conf.get(k) != v for k, v in record.items()):
                # enregistrement seul : modifié sans couper le flux
                self.api.call("PATCH", f"/v3/config/paths/patch/{quote(sid)}", record)
                log.info("[%s] enregistrement continu %s", sid,
                         "activé" if record.get("record") else "arrêté")
        for name in current:
            if name not in wanted_ids:
                self.api.call("DELETE", f"/v3/config/paths/delete/{quote(name)}")
                log.info("[%s] caméra retirée", name)
        self._last_sync = time.time()
        self._need_sync = False

    # ------------------------------------------------------------- état
    def refresh(self):
        items = self.api.call("GET", "/v3/paths/list?itemsPerPage=1000")["items"]
        live = {p["name"]: p for p in items}
        now = time.time()
        with self._lock:
            for cam in self.cameras:
                st = self._status[cam["id"]]
                if st["state"] == "error":              # MediaMTX est revenu
                    st["state"], st["since"] = "connecting", int(now)
                path = live.get(cam["id"])
                if path is None:
                    self._need_sync = True      # MediaMTX a redémarré
                    new_state, err = "connecting", ""
                elif path.get("ready"):
                    new_state, err = "online", ""
                    st["codecs"] = path.get("tracks", [])
                    st["viewers"] = len(path.get("readers", []))
                elif cam.get("on_demand"):
                    new_state, err = "standby", ""
                elif st["state"] == "connecting" and now - st["since"] < CONNECT_GRACE:
                    new_state, err = "connecting", ""
                else:
                    new_state = "offline"
                    err = ("Le serveur ne reçoit pas l'image de cette caméra. "
                           "Vérifiez qu'elle est allumée et que son adresse est correcte.")
                if new_state != st["state"]:
                    log.info("[%s] %s → %s", cam["id"], st["state"], new_state)
                    st["state"], st["since"] = new_state, int(now)
                st["error"] = err
                if new_state != "online":
                    st["viewers"] = 0

    def _set_all_error(self, message: str):
        with self._lock:
            for st in self._status.values():
                if st["state"] != "error":
                    st["since"] = int(time.time())
                st["state"], st["error"], st["viewers"] = "error", message, 0

    def run(self):
        while True:
            try:
                if self._need_sync or time.time() - self._last_sync > RESYNC_INTERVAL:
                    self.sync()
                self.refresh()
            except MediaMTXError as exc:
                log.warning("%s", mask(str(exc)))
                self._need_sync = True
                self._set_all_error("Le service vidéo (MediaMTX) ne répond pas. "
                                    "Consultez : journalctl -u mediamtx")
            time.sleep(POLL_INTERVAL)

    def status(self) -> list:
        with self._lock:
            return [dict(self._status[c["id"]]) for c in self.cameras]
