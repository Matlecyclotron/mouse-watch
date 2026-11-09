# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Réglages de détection modifiables depuis l'interface (position du sélecteur,
zones, seuils). Ils sont enregistrés dans settings.json, dans le dossier
d'état du service, et non dans config.yaml (réservé à l'installation).
"""
import copy
import json
import logging
import os
import tempfile
import threading

log = logging.getLogger("rtsp-web.settings")

MODES = ("off", "observe", "record")

# Valeurs par défaut des seuils ; config.yaml peut les remplacer
# (section « detection_defaults »).
THRESHOLD_DEFAULTS = {
    "min_size": 4.0,       # % de l'image
    "sensitivity": 6,      # 1 (peu sensible) à 10 (très sensible)
    "persistence": 3,      # images consécutives
    "cooldown": 10,        # secondes entre deux passages
}
LIMITS = {
    "min_size": (0.5, 50.0, float),
    "sensitivity": (1, 10, int),
    "persistence": (1, 30, int),
    "cooldown": (0, 600, int),
}
MAX_POLYGONS = 20
MAX_POINTS = 200


class SettingsError(ValueError):
    pass


def _validate_polygons(value) -> list:
    if not isinstance(value, list) or len(value) > MAX_POLYGONS:
        raise SettingsError(f"au plus {MAX_POLYGONS} zones de chaque sorte")
    polygons = []
    for poly in value:
        if not isinstance(poly, list) or not 3 <= len(poly) <= MAX_POINTS:
            raise SettingsError(f"une zone doit avoir de 3 à {MAX_POINTS} sommets")
        points = []
        for pt in poly:
            if (not isinstance(pt, (list, tuple)) or len(pt) != 2
                    or not all(isinstance(c, (int, float)) for c in pt)):
                raise SettingsError("sommet invalide")
            x, y = (min(1.0, max(0.0, float(c))) for c in pt)
            points.append([round(x, 4), round(y, 4)])
        polygons.append(points)
    return polygons


class SettingsStore:
    """Réglages par caméra, enregistrés de façon atomique dans un fichier JSON."""

    def __init__(self, path: str, camera_ids: list, defaults: dict = None):
        self.path = path
        self.camera_ids = list(camera_ids)
        self.defaults = dict(THRESHOLD_DEFAULTS)
        for key, value in (defaults or {}).items():
            if key in LIMITS:
                self.defaults[key] = self._clamp(key, value)
        self._lock = threading.Lock()
        self._listeners = []
        self._data = {}
        self._load()

    # ---------------------------------------------------------------- base
    @staticmethod
    def _clamp(key, value):
        low, high, cast = LIMITS[key]
        try:
            value = cast(value)
        except (TypeError, ValueError):
            raise SettingsError(f"valeur invalide pour {key}") from None
        return min(high, max(low, value))

    def _blank(self) -> dict:
        return {"mode": "off", "zones": {"include": [], "exclude": []}, **self.defaults}

    def _load(self):
        raw = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh).get("cameras", {})
            except (OSError, ValueError) as exc:
                log.error("réglages illisibles (%s) : valeurs par défaut utilisées", exc)
        for cid in self.camera_ids:
            current = self._blank()
            try:
                current = self._merge(current, raw.get(cid, {}))
            except SettingsError as exc:
                log.warning("[%s] réglages enregistrés ignorés : %s", cid, exc)
            self._data[cid] = current

    def _save(self):
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "cameras": self._data}, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def _merge(self, current: dict, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise SettingsError("format invalide")
        result = copy.deepcopy(current)
        for key, value in patch.items():
            if key == "mode":
                if value not in MODES:
                    raise SettingsError("position inconnue")
                result["mode"] = value
            elif key == "zones":
                if not isinstance(value, dict):
                    raise SettingsError("zones invalides")
                result["zones"] = {
                    "include": _validate_polygons(value.get("include", [])),
                    "exclude": _validate_polygons(value.get("exclude", [])),
                }
            elif key in LIMITS:
                result[key] = self._clamp(key, value)
            # les clés inconnues sont ignorées
        return result

    # ---------------------------------------------------------------- API
    def subscribe(self, callback):
        self._listeners.append(callback)

    def get(self, cid: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data[cid])

    def update(self, cid: str, patch: dict) -> dict:
        with self._lock:
            new = self._merge(self._data[cid], patch)
            changed = new != self._data[cid]
            if changed:
                if new["mode"] != self._data[cid]["mode"]:
                    log.info("[%s] détection : %s → %s", cid, self._data[cid]["mode"], new["mode"])
                self._data[cid] = new
                self._save()
            result = copy.deepcopy(new)
        if changed:
            for cb in self._listeners:
                cb(cid)
        return result

    def reset_thresholds(self, cid: str) -> dict:
        return self.update(cid, dict(self.defaults))
