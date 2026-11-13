# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Collecte des données d'activité (version 5).

  - périodes d'observation : quand chaque caméra surveillait réellement
    (position Enregistrement, analyse active) ;
  - historique daté des changements de réglages ;
  - météo horaire récupérée auprès d'Open-Meteo ;
  - éphémérides calculées localement (astro.py) ;
  - deux tableaux prêts à l'export : une ligne par passage, une ligne par nuit.

Tout est conservé dans la base SQLite du stockage (events.db).
"""
import datetime as dt
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import astro

log = logging.getLogger("rtsp-web.stats")

TICK = 30                       # secondes entre deux relevés d'observation
WEATHER_INTERVAL = 6 * 3600     # secondes entre deux récupérations météo
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_VARS = ("temperature_2m", "relative_humidity_2m", "precipitation",
                "cloud_cover", "wind_speed_10m")
EXPORT_HOUR = 7                 # heure locale de la copie quotidienne sur le NAS
MIN_OBSERVATION_H = 0.25        # en dessous, pas de taux horaire

TAG_LABELS = {"": "non marqué", "mouse": "souris", "other": "autre animal",
              "false": "fausse alerte"}

# Colonnes des tableaux : (clé, intitulé, largeur en cm, description)
PASSAGE_COLUMNS = [
    ("date", "Date", 2.6, "Date du passage (heure locale du serveur)."),
    ("heure", "Heure", 2.2, "Heure de début du passage (heure locale)."),
    ("nuit", "Nuit du", 2.6, "Nuit à laquelle le passage est rattaché : date du soir. "
     "Une nuit va de midi à midi ; un passage à 2 h appartient à la nuit de la veille."),
    ("camera", "Caméra", 3.0, "Nom de la caméra."),
    ("duree_s", "Durée (s)", 2.2, "Durée du passage, en secondes."),
    ("taille_pct", "Taille (%)", 2.2, "Plus grande part de l'image occupée par l'objet détecté."),
    ("marquage", "Marquage", 3.0, "Marquage fait dans la page Passages : souris, autre animal, "
     "fausse alerte ou non marqué."),
    ("video", "Vidéo", 1.8, "Un extrait vidéo a été enregistré."),
    ("supprime", "Supprimé", 2.2, "La photo et la vidéo ont été supprimées ; le passage reste compté."),
    ("periode", "Période", 2.6, "jour, crépuscule (soleil entre l'horizon et 12° dessous) ou nuit."),
    ("h_apres_coucher", "Heures après le coucher", 3.6, "Heures écoulées depuis le coucher du "
     "soleil de cette nuit (négatif : avant le coucher). Insensible au changement d'heure."),
    ("h_avant_lever", "Heures avant le lever", 3.4, "Heures restantes avant le lever du soleil "
     "(négatif : après le lever)."),
    ("soleil_hauteur", "Soleil (°)", 2.2, "Hauteur du soleil au-dessus de l'horizon, en degrés."),
    ("lune_eclairement", "Lune éclairée (%)", 2.8, "Part éclairée du disque lunaire "
     "(0 : nouvelle lune, 100 : pleine lune)."),
    ("lune_hauteur", "Lune (°)", 2.2, "Hauteur de la lune au-dessus de l'horizon, en degrés."),
    ("lune_visible", "Lune levée", 2.4, "La lune était au-dessus de l'horizon."),
    ("temperature", "Température (°C)", 2.8, "Température à 2 m, à l'heure du passage (Open-Meteo)."),
    ("pluie", "Pluie (mm)", 2.2, "Précipitations pendant l'heure du passage (Open-Meteo)."),
    ("nuages", "Nuages (%)", 2.2, "Couverture nuageuse totale (Open-Meteo)."),
    ("vent", "Vent (km/h)", 2.4, "Vitesse du vent à 10 m (Open-Meteo)."),
    ("humidite", "Humidité (%)", 2.6, "Humidité relative à 2 m (Open-Meteo)."),
    ("horodatage_utc", "Horodatage UTC", 4.2, "Début du passage en temps universel (ISO 8601)."),
    ("id", "Identifiant", 6.0, "Identifiant du passage dans l'application."),
]
NIGHT_COLUMNS = [
    ("nuit", "Nuit du", 2.6, "Date du soir. La nuit va de midi à midi le lendemain."),
    ("camera", "Caméra", 3.0, "Nom de la caméra."),
    ("observation_h", "Observation (h)", 2.8, "Heures pendant lesquelles la caméra surveillait "
     "(position Enregistrement, analyse active), de midi à midi. Vide : inconnu "
     "(données antérieures à la version 5)."),
    ("observation_nuit_h", "Observation de nuit (h)", 3.6, "Part de l'observation située entre le "
     "coucher et le lever du soleil."),
    ("passages", "Passages", 2.2, "Nombre de passages retenus cette nuit-là."),
    ("passages_par_heure", "Passages par heure", 3.2, "Passages divisés par les heures d'observation "
     "(vide si moins d'un quart d'heure d'observation). C'est le chiffre comparable d'une nuit à l'autre."),
    ("premier_passage", "Premier passage", 2.8, "Heure locale du premier passage."),
    ("dernier_passage", "Dernier passage", 2.8, "Heure locale du dernier passage."),
    ("premier_apres_coucher_h", "Premier : heures après le coucher", 4.6,
     "Délai entre le coucher du soleil et le premier passage."),
    ("dernier_avant_lever_h", "Dernier : heures avant le lever", 4.4,
     "Délai entre le dernier passage et le lever du soleil."),
    ("coucher", "Coucher du soleil", 2.8, "Heure locale du coucher du soleil."),
    ("lever", "Lever du soleil", 2.8, "Heure locale du lever du soleil, le lendemain matin."),
    ("duree_nuit_h", "Durée de la nuit (h)", 3.2, "Du coucher au lever du soleil."),
    ("lune_eclairement", "Lune éclairée (%)", 2.8, "Part éclairée du disque lunaire au milieu de la nuit."),
    ("lune_phase", "Lune", 2.6, "croissante ou décroissante."),
    ("temp_min", "Température min. (°C)", 3.2, "Température la plus basse entre le coucher et le lever."),
    ("temp_moy", "Température moy. (°C)", 3.2, "Température moyenne entre le coucher et le lever."),
    ("pluie", "Pluie (mm)", 2.2, "Cumul des précipitations entre le coucher et le lever."),
    ("nuages", "Nuages moy. (%)", 2.8, "Couverture nuageuse moyenne de la nuit."),
    ("vent", "Vent moy. (km/h)", 2.8, "Vent moyen de la nuit."),
    ("reglages_modifies", "Réglages modifiés", 3.0, "Une zone ou un seuil de détection a été modifié "
     "pendant cette nuit : la comparaison avec les autres nuits est à prendre avec prudence."),
]


def night_noon(ts: float) -> dt.datetime:
    """Midi local qui ouvre la « nuit » contenant l'instant ts."""
    local = dt.datetime.fromtimestamp(ts)
    day = local.date() if local.hour >= 12 else local.date() - dt.timedelta(days=1)
    return dt.datetime(day.year, day.month, day.day, 12)


def _local_time(ts):
    return dt.datetime.fromtimestamp(ts).time().replace(microsecond=0) if ts else None


def _round(value, digits=1):
    return None if value is None else round(value, digits)


class Stats(threading.Thread):

    def __init__(self, storage, settings, detectors: dict, camera_names: dict,
                 site: dict = None, weather: dict = None):
        super().__init__(daemon=True, name="stats")
        self.storage = storage
        self.settings = settings
        self.detectors = detectors
        self.names = camera_names
        site = site or {}
        self.lat, self.lon = site.get("latitude"), site.get("longitude")
        self.has_site = isinstance(self.lat, (int, float)) and isinstance(self.lon, (int, float))
        self.weather_enabled = bool((weather or {}).get("enabled", True)) and self.has_site
        self.weather_error = ""
        self._open = {}                       # caméra -> ligne d'observation en cours
        self._snapshots = {cid: settings.get(cid) for cid in detectors}
        sql = storage._sql
        sql("""CREATE TABLE IF NOT EXISTS observation (
                 id INTEGER PRIMARY KEY AUTOINCREMENT, camera TEXT NOT NULL,
                 start REAL NOT NULL, end REAL NOT NULL)""")
        sql("CREATE INDEX IF NOT EXISTS observation_start ON observation(start)")
        sql("""CREATE TABLE IF NOT EXISTS weather (
                 hour INTEGER PRIMARY KEY, temp REAL, humidity REAL, precip REAL,
                 cloud REAL, wind REAL)""")
        sql("CREATE TABLE IF NOT EXISTS settings_log (ts REAL NOT NULL, camera TEXT NOT NULL, changes TEXT NOT NULL)")
        sql("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        settings.subscribe(self._on_settings)

    # ---------------------------------------------------------------- outils
    def _meta(self, key, default=None):
        rows = self.storage._sql("SELECT value FROM meta WHERE key=?", (key,), fetch=True)
        return rows[0][0] if rows else default

    def _set_meta(self, key, value):
        self.storage._sql("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))

    # ----------------------------------------------------------- observation
    def _track(self):
        now = time.time()
        for cid, det in self.detectors.items():
            active = (self.settings.get(cid)["mode"] == "record"
                      and det.status()["state"] == "running")
            if active and cid in self._open:
                self.storage._sql("UPDATE observation SET end=? WHERE id=?", (now, self._open[cid]))
            elif active:
                self.storage._sql("INSERT INTO observation (camera, start, end) VALUES (?,?,?)",
                                  (cid, now, now))
                self._open[cid] = self.storage._sql(
                    "SELECT MAX(id) FROM observation WHERE camera=?", (cid,), fetch=True)[0][0]
            else:
                self._open.pop(cid, None)

    def _observed(self, camera: str, a: float, b: float):
        """Secondes d'observation de la caméra dans [a, b] ; None si aucune donnée."""
        rows = self.storage._sql(
            "SELECT start, end FROM observation WHERE camera=? AND end>? AND start<?",
            (camera, a, b), fetch=True)
        if not rows:
            known = self.storage._sql("SELECT MIN(start) FROM observation", fetch=True)[0][0]
            return None if known is None or known > b else 0.0
        return sum(max(0.0, min(e, b) - max(s, a)) for s, e in rows)

    # --------------------------------------------------- historique réglages
    def _on_settings(self, cid: str):
        if cid not in self._snapshots:
            return
        new, old = self.settings.get(cid), self._snapshots[cid]
        changes = {k: new[k] for k in new if new[k] != old.get(k)}
        self._snapshots[cid] = new
        if changes:
            self.storage._sql("INSERT INTO settings_log VALUES (?,?,?)",
                              (time.time(), cid, json.dumps(changes, ensure_ascii=False)))

    def _detection_changed(self, camera: str, a: float, b: float) -> bool:
        rows = self.storage._sql(
            "SELECT changes FROM settings_log WHERE camera=? AND ts>=? AND ts<?",
            (camera, a, b), fetch=True)
        keys = {"zones", "min_size", "sensitivity", "persistence", "cooldown"}
        return any(keys & set(json.loads(r[0])) for r in rows)

    # ------------------------------------------------------------------ météo
    def fetch_weather(self):
        """Récupère la météo horaire récente (3 jours, ou 92 la première fois)."""
        past = 3 if self._meta("weather_backfill") else 92
        query = urllib.parse.urlencode({
            # Coordonnées arrondies au centième de degré (environ 1 km) :
            # suffisant pour la météo, sans transmettre l'emplacement exact.
            "latitude": round(self.lat, 2), "longitude": round(self.lon, 2),
            "hourly": ",".join(WEATHER_VARS),
            "past_days": past, "forecast_days": 1,
            "timeformat": "unixtime", "timezone": "GMT",
        })
        req = urllib.request.Request(f"{WEATHER_URL}?{query}",
                                     headers={"User-Agent": "rtsp-web (mouse-watch)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        self.store_weather(data)
        self._set_meta("weather_backfill", 1)
        self._set_meta("weather_last", time.time())

    def store_weather(self, data: dict) -> int:
        hourly = data["hourly"]
        now = time.time()
        count = 0
        for i, hour in enumerate(hourly["time"]):
            if hour > now:                     # prévisions : non conservées
                continue
            values = [hourly[name][i] for name in WEATHER_VARS]
            if all(v is None for v in values):
                continue
            temp, humidity, precip, cloud, wind = values
            self.storage._sql("INSERT OR REPLACE INTO weather VALUES (?,?,?,?,?,?)",
                              (int(hour), temp, humidity, precip, cloud, wind))
            count += 1
        return count

    def _weather_at(self, ts: float) -> dict:
        """Météo de l'heure du passage. Les précipitations d'Open-Meteo sont le
        cumul de l'heure précédant chaque horodatage : on lit donc l'heure suivante."""
        hour = int(ts // 3600) * 3600
        rows = dict((r[0], r) for r in self.storage._sql(
            "SELECT * FROM weather WHERE hour IN (?,?)", (hour, hour + 3600), fetch=True))
        cur, nxt = rows.get(hour), rows.get(hour + 3600)
        return {"temperature": _round(cur[1]) if cur else None,
                "humidite": _round(cur[2], 0) if cur else None,
                "pluie": _round(nxt[3]) if nxt else None,
                "nuages": _round(cur[4], 0) if cur else None,
                "vent": _round(cur[5]) if cur else None}

    def _weather_between(self, a: float, b: float) -> dict:
        rows = self.storage._sql("SELECT * FROM weather WHERE hour>=? AND hour<=?",
                                 (int(a // 3600) * 3600, int(b // 3600) * 3600 + 3600), fetch=True)
        if not rows:
            return {"temp_min": None, "temp_moy": None, "pluie": None, "nuages": None, "vent": None}
        col = lambda i: [r[i] for r in rows if r[i] is not None]
        mean = lambda v: sum(v) / len(v) if v else None
        rain = [r[3] for r in rows[1:] if r[3] is not None]     # cumuls des heures de la nuit
        return {"temp_min": _round(min(col(1), default=None)), "temp_moy": _round(mean(col(1))),
                "pluie": _round(sum(rain)) if rain else None,
                "nuages": _round(mean(col(4)), 0), "vent": _round(mean(col(5)))}

    # --------------------------------------------------------------- tableaux
    def _events(self, since=None, until=None, camera=None, confirmed=False) -> list:
        events = self.storage.list_events(camera=camera, since=since, before=until,
                                          limit=100000, include_deleted=True)
        keep = (lambda e: e["tag"] == "mouse") if confirmed else (lambda e: e["tag"] != "false")
        return sorted((e for e in events if keep(e)), key=lambda e: e["start"])

    def passages(self, since=None, until=None, camera=None, confirmed=False) -> list:
        rows, nights = [], {}
        for ev in self._events(since, until, camera, confirmed):
            ts = ev["start"]
            local = dt.datetime.fromtimestamp(ts)
            noon = night_noon(ts)
            row = {
                "date": local.date(), "heure": local.time().replace(microsecond=0),
                "nuit": noon.date(), "camera": self.names.get(ev["camera"], ev["camera"]),
                "duree_s": ev["duration"], "taille_pct": ev["size"],
                "marquage": TAG_LABELS.get(ev["tag"], ev["tag"]),
                "video": "oui" if ev["clip_state"] == "ok" else "non",
                "supprime": "oui" if ev["deleted"] else "non",
                "horodatage_utc": dt.datetime.fromtimestamp(ts, dt.timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "id": ev["id"],
            }
            if self.has_site:
                if noon not in nights:
                    nights[noon] = astro.night(noon.timestamp(), self.lat, self.lon)
                n = nights[noon]
                moon_alt = astro.moon_altitude(ts, self.lat, self.lon)
                row.update({
                    "periode": astro.period(ts, self.lat, self.lon),
                    "h_apres_coucher": _round((ts - n["sunset"]) / 3600, 2) if n["sunset"] else None,
                    "h_avant_lever": _round((n["sunrise"] - ts) / 3600, 2) if n["sunrise"] else None,
                    "soleil_hauteur": _round(astro.sun_altitude(ts, self.lat, self.lon)),
                    "lune_eclairement": _round(astro.moon_illumination(ts), 0),
                    "lune_hauteur": _round(moon_alt),
                    "lune_visible": "oui" if moon_alt > 0 else "non",
                })
            row.update(self._weather_at(ts))
            rows.append(row)
        return rows

    def nights(self, since=None, until=None, camera=None, confirmed=False) -> list:
        events = self._events(since, until, camera, confirmed)
        cameras = [camera] if camera else list(self.names)
        first = self.storage._sql("SELECT MIN(start) FROM observation", fetch=True)[0][0]
        known = [t for t in (first, events[0]["start"] if events else None) if t]
        if not known:
            return []
        begin = night_noon(max(min(known), since or 0))
        last = min(until or time.time(), time.time())
        by_night = {}
        for ev in events:
            by_night.setdefault((night_noon(ev["start"]), ev["camera"]), []).append(ev["start"])

        rows, noon = [], begin
        while noon.timestamp() <= last:
            a, b = noon.timestamp(), noon.timestamp() + 86400
            n = astro.night(a, self.lat, self.lon) if self.has_site else {}
            sunset, sunrise = n.get("sunset"), n.get("sunrise")
            meteo = self._weather_between(sunset, sunrise) if sunset and sunrise else {}
            for cid in cameras:
                times = by_night.get((noon, cid), [])
                observed = self._observed(cid, a, b)
                if not times and not observed:
                    continue                      # ni passage ni observation : nuit non listée
                hours = observed / 3600 if observed is not None else None
                row = {
                    "nuit": noon.date(), "camera": self.names.get(cid, cid),
                    "observation_h": _round(hours, 2),
                    "passages": len(times),
                    "passages_par_heure": _round(len(times) / hours, 2)
                                          if hours and hours >= MIN_OBSERVATION_H else None,
                    "premier_passage": _local_time(times[0]) if times else None,
                    "dernier_passage": _local_time(times[-1]) if times else None,
                    "reglages_modifies": "oui" if self._detection_changed(cid, a, b) else "non",
                }
                if sunset and sunrise:
                    night_obs = self._observed(cid, sunset, sunrise)
                    row.update({
                        "observation_nuit_h": _round(night_obs / 3600, 2) if night_obs is not None else None,
                        "premier_apres_coucher_h": _round((times[0] - sunset) / 3600, 2) if times else None,
                        "dernier_avant_lever_h": _round((sunrise - times[-1]) / 3600, 2) if times else None,
                        "coucher": _local_time(sunset), "lever": _local_time(sunrise),
                        "duree_nuit_h": _round((sunrise - sunset) / 3600, 2),
                        "lune_eclairement": _round(n["moon_illumination"], 0),
                        "lune_phase": "croissante" if n["moon_waxing"] else "décroissante",
                    })
                row.update(meteo)
                rows.append(row)
            noon += dt.timedelta(days=1)
        return rows

    # ------------------------------------------------------------------- état
    def status(self) -> dict:
        last = self._meta("weather_last")
        return {
            "site": self.has_site,
            "weather": {"enabled": self.weather_enabled,
                        "last": float(last) if last else None,
                        "error": self.weather_error},
            "nas_export": self._meta("nas_export_day"),
        }

    # ------------------------------------------------------------ tâche de fond
    def _daily_export(self):
        """Copie quotidienne des données en CSV dans <NAS>/statistiques/."""
        import export                          # import tardif : évite une boucle d'imports
        today = dt.date.today().isoformat()
        if (not self.storage.nas_dir or dt.datetime.now().hour < EXPORT_HOUR
                or self._meta("nas_export_day") == today):
            return
        files = export.csv_files(self)
        self.storage.write_nas_files({f"statistiques/{name}": data for name, data in files.items()})
        self._set_meta("nas_export_day", today)
        log.info("données d'activité copiées sur le NAS (statistiques/)")

    def run(self):
        next_weather = 0.0
        while True:
            try:
                self._track()
            except Exception:                  # noqa: BLE001
                log.exception("relevé d'observation impossible")
            if self.weather_enabled and time.time() >= next_weather:
                try:
                    self.fetch_weather()
                    self.weather_error = ""
                    next_weather = time.time() + WEATHER_INTERVAL
                except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
                    self.weather_error = f"météo indisponible : {exc}"
                    log.warning("%s", self.weather_error)
                    next_weather = time.time() + 1800      # nouvel essai dans 30 min
            try:
                self._daily_export()
            except Exception as exc:           # noqa: BLE001 — NAS absent, etc.
                log.debug("copie des données sur le NAS reportée : %s", exc)
            time.sleep(TICK)
