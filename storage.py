# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
Stockage des passages.

Chaque photo est d'abord écrite sur le disque local du serveur (dossier
« pending »), puis copiée sur le NAS dès qu'il répond. Toutes les opérations
sur le NAS passent par un unique fil d'exécution avec délai maximal : un
partage autofs injoignable ne bloque ni la détection ni l'interface.
Un index SQLite garde la liste des passages pour l'interface.
"""
import concurrent.futures
import datetime as dt
import json
import logging
import os
import shutil
import sqlite3
import threading
import time

import cv2

log = logging.getLogger("rtsp-web.storage")

PROBE_INTERVAL = 10       # secondes entre deux vérifications du NAS
NAS_TIMEOUT = 15          # délai maximal d'une opération sur le NAS
THUMB_WIDTH = 480
TAGS = ("", "mouse", "other", "false")   # non marqué, souris, autre animal, fausse alerte


class NasTimeout(Exception):
    pass


class Storage(threading.Thread):

    def __init__(self, nas_dir: str, local_dir: str, state_dir: str, keep_days: int = 0):
        super().__init__(daemon=True, name="storage")
        self.nas_dir = os.path.abspath(nas_dir) if nas_dir else None
        self.local_dir = os.path.abspath(local_dir)
        self.thumb_dir = os.path.join(state_dir, "thumbs")
        self.keep_days = int(keep_days or 0)
        os.makedirs(self.local_dir, exist_ok=True)
        os.makedirs(self.thumb_dir, exist_ok=True)

        self._db_lock = threading.Lock()
        self._db = sqlite3.connect(os.path.join(state_dir, "events.db"), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, camera TEXT NOT NULL, start REAL NOT NULL,
            end REAL NOT NULL, size REAL NOT NULL, rel TEXT NOT NULL,
            location TEXT NOT NULL)""")
        self._db.execute("CREATE INDEX IF NOT EXISTS events_start ON events(start)")
        columns = {r[1] for r in self._db.execute("PRAGMA table_info(events)")}
        if "clip_state" not in columns:          # mise à jour depuis la version 3
            self._db.execute("ALTER TABLE events ADD COLUMN clip_state TEXT NOT NULL DEFAULT 'none'")
        if "tag" not in columns:                 # mise à jour depuis la version 4
            self._db.execute("ALTER TABLE events ADD COLUMN tag TEXT NOT NULL DEFAULT ''")
            self._db.execute("ALTER TABLE events ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        # Extraits en cours de préparation lors d'un arrêt : perdus, la photo reste
        self._db.execute("UPDATE events SET clip_state='failed' WHERE clip_state='pending'")
        self._db.commit()

        # Un seul fil pour toutes les opérations sur le NAS
        self._nas = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="nas")
        self._wake = threading.Event()
        self._status_lock = threading.Lock()
        self._status = {"state": "disabled" if not self.nas_dir else "checking",
                        "message": "", "checked": 0, "last_copy": 0}

    # ------------------------------------------------------------ utilitaires
    def _sql(self, query: str, args=(), fetch=False):
        with self._db_lock:
            cur = self._db.execute(query, args)
            rows = cur.fetchall() if fetch else None
            self._db.commit()
            return rows

    def _on_nas(self, fn, *args, timeout=NAS_TIMEOUT):
        future = self._nas.submit(fn, *args)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise NasTimeout("le NAS ne répond pas") from None

    def _set_status(self, state: str, message: str = ""):
        with self._status_lock:
            if state != self._status["state"]:
                log.info("NAS : %s %s", state, message)
            self._status.update(state=state, message=message, checked=time.time())

    @staticmethod
    def _row(row) -> dict:
        eid, camera, start, end, size, rel, location, clip_state, tag, deleted = row
        return {"id": eid, "camera": camera, "start": start, "end": end,
                "duration": round(end - start, 1), "size": round(size, 1),
                "file": rel, "location": location, "clip_state": clip_state,
                "clip": rel[:-4] + ".mp4" if clip_state == "ok" and not deleted else None,
                "tag": tag, "deleted": bool(deleted)}

    @staticmethod
    def _companions(rel: str) -> list:
        """Fichiers d'un passage : photo, fiche JSON, extrait vidéo."""
        base = rel[:-4]
        return [rel, base + ".json", base + ".mp4"]

    # ------------------------------------------------------------- écriture
    def save_event(self, camera: str, frame, start: float, end: float, size: float,
                   clip_expected: bool = False) -> str:
        """Enregistre la photo d'un passage (appelé par la détection)."""
        stamp = dt.datetime.fromtimestamp(start)
        folder = os.path.join(camera, stamp.strftime("%Y-%m-%d"))
        name = stamp.strftime("%H-%M-%S")
        os.makedirs(os.path.join(self.local_dir, folder), exist_ok=True)
        rel = os.path.join(folder, name + ".jpg")
        n = 2
        while (os.path.exists(os.path.join(self.local_dir, rel))
               or self._sql("SELECT 1 FROM events WHERE rel=?", (rel,), fetch=True)):
            rel = os.path.join(folder, f"{name}-{n}.jpg")
            n += 1
        eid = f"{camera}_{stamp.strftime('%Y%m%d-%H%M%S')}_{int(start * 1000) % 1000:03d}"
        if n > 2:
            eid += f"-{n - 1}"

        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise RuntimeError("encodage JPEG impossible")
        path = os.path.join(self.local_dir, rel)
        with open(path + ".part", "wb") as fh:
            fh.write(jpg.tobytes())
        os.replace(path + ".part", path)
        meta = {"camera": camera, "start": stamp.isoformat(timespec="milliseconds"),
                "end": dt.datetime.fromtimestamp(end).isoformat(timespec="milliseconds"),
                "duration_s": round(end - start, 2), "size_percent": round(size, 2),
                "photo": os.path.basename(rel)}
        with open(path[:-4] + ".json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)

        h, w = frame.shape[:2]
        thumb = cv2.resize(frame, (THUMB_WIDTH, max(1, round(h * THUMB_WIDTH / w))),
                           interpolation=cv2.INTER_AREA)
        cv2.imwrite(os.path.join(self.thumb_dir, eid + ".jpg"), thumb,
                    [cv2.IMWRITE_JPEG_QUALITY, 80])

        self._sql("INSERT INTO events (id, camera, start, end, size, rel, location, clip_state) "
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (eid, camera, start, end, size, rel, "pending",
                   "pending" if clip_expected else "none"))
        log.info("[%s] passage enregistré : %s", camera, rel)
        self._wake.set()
        return eid

    def attach_clip(self, eid: str, data):
        """Range l'extrait vidéo d'un passage (data = octets MP4, ou None si échec)."""
        event = self.get_event(eid)
        if event is None or event["deleted"]:  # passage supprimé entre-temps
            return
        if data is None:
            self._sql("UPDATE events SET clip_state='failed' WHERE id=?", (eid,))
            log.warning("[%s] extrait vidéo indisponible pour %s", event["camera"], event["file"])
            self._wake.set()
            return
        base = os.path.join(self.local_dir, event["file"][:-4])
        with open(base + ".mp4.part", "wb") as fh:
            fh.write(data)
        os.replace(base + ".mp4.part", base + ".mp4")
        try:
            with open(base + ".json", encoding="utf-8") as fh:
                meta = json.load(fh)
            meta["video"] = os.path.basename(base) + ".mp4"
            with open(base + ".json", "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, ensure_ascii=False)
        except (OSError, ValueError):
            pass
        self._sql("UPDATE events SET clip_state='ok' WHERE id=?", (eid,))
        log.info("[%s] extrait vidéo enregistré : %s.mp4", event["camera"], event["file"][:-4])
        self._wake.set()

    # --------------------------------------------------------------- lecture
    def list_events(self, camera=None, before=None, limit=200, tag=None,
                    since=None, include_deleted=False) -> list:
        """Passages, du plus récent au plus ancien.

        tag : None (tous), "" (non marqués), "mouse", "other" ou "false".
        Les passages supprimés restent dans l'index pour les statistiques ;
        ils ne sont renvoyés que si include_deleted est vrai."""
        query, args = "SELECT * FROM events WHERE 1=1", []
        if not include_deleted:
            query += " AND deleted=0"
        if camera:
            query += " AND camera=?"
            args.append(camera)
        if tag is not None:
            query += " AND tag=?"
            args.append(tag)
        if since:
            query += " AND start>=?"
            args.append(float(since))
        if before:
            query += " AND start<?"
            args.append(float(before))
        query += " ORDER BY start DESC LIMIT ?"
        args.append(max(1, min(int(limit), 100000)))
        return [self._row(r) for r in self._sql(query, args, fetch=True)]

    def set_tag(self, eid: str, tag: str):
        if tag not in TAGS:
            raise ValueError("marquage inconnu")
        self._sql("UPDATE events SET tag=? WHERE id=?", (tag, eid))

    def get_event(self, eid: str):
        rows = self._sql("SELECT * FROM events WHERE id=?", (eid,), fetch=True)
        return self._row(rows[0]) if rows else None

    def thumb_path(self, eid: str) -> str:
        return os.path.join(self.thumb_dir, eid + ".jpg")

    def _read(self, event: dict, rel: str) -> bytes:
        if event["location"] == "pending":
            with open(os.path.join(self.local_dir, rel), "rb") as fh:
                return fh.read()

        def read():
            with open(os.path.join(self.nas_dir, rel), "rb") as fh:
                return fh.read()
        return self._on_nas(read)

    def read_photo(self, event: dict) -> bytes:
        return self._read(event, event["file"])

    def read_clip(self, event: dict) -> bytes:
        if event["clip_state"] != "ok":
            raise FileNotFoundError("pas d'extrait vidéo")
        return self._read(event, event["clip"])

    def delete_event(self, event: dict):
        rel = event["file"]
        targets = self._companions(rel)
        if event["location"] == "pending":
            for t in targets:
                _remove(os.path.join(self.local_dir, t))
        else:
            self._on_nas(lambda: [_remove(os.path.join(self.nas_dir, t)) for t in targets])
        _remove(self.thumb_path(event["id"]))
        # La ligne reste dans l'index, marquée supprimée : les statistiques
        # continuent de compter ce passage (sauf s'il est marqué fausse alerte).
        self._sql("UPDATE events SET deleted=1, clip_state='none' WHERE id=?", (event["id"],))
        log.info("[%s] photo et vidéo supprimées : %s", event["camera"], rel)

    def write_nas_files(self, files: dict):
        """Écrit des fichiers (chemin relatif -> octets) sur le NAS, sans bloquer."""
        if not self.nas_dir:
            raise OSError("NAS non configuré")

        def write():
            for rel, data in files.items():
                dst = os.path.join(self.nas_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst + ".part", "wb") as fh:
                    fh.write(data)
                os.replace(dst + ".part", dst)
        self._on_nas(write, timeout=60)

    def status(self) -> dict:
        with self._status_lock:
            st = dict(self._status)
        st["nas_dir"] = self.nas_dir
        st["pending"] = self._sql("SELECT COUNT(*) FROM events WHERE location='pending' AND deleted=0",
                                  fetch=True)[0][0]
        return st

    # ----------------------------------------------------- tâche de fond NAS
    def _probe(self):
        if not os.path.isdir(self.nas_dir):
            return "missing", "Dossier introuvable : le partage est-il monté ?"
        test = os.path.join(self.nas_dir, ".rtsp-web-test")
        try:
            with open(test, "w") as fh:
                fh.write("ok")
            os.remove(test)
        except PermissionError:
            return "denied", "Écriture refusée : vérifiez les droits du partage."
        except OSError as exc:
            return "unreachable", str(exc)
        return "ok", ""

    def _copy_one(self, rel: str):
        for name in self._companions(rel):
            src = os.path.join(self.local_dir, name)
            if not os.path.exists(src):
                continue
            dst = os.path.join(self.nas_dir, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst + ".part")
            if os.path.getsize(dst + ".part") != os.path.getsize(src):
                raise OSError("copie incomplète")
            os.replace(dst + ".part", dst)

    def _move_pending(self):
        # Un passage attend que son extrait vidéo soit prêt avant d'être copié
        rows = self._sql("SELECT id, rel FROM events WHERE location='pending' AND deleted=0 "
                         "AND clip_state!='pending' ORDER BY start LIMIT 50", fetch=True)
        for eid, rel in rows:
            self._on_nas(self._copy_one, rel, timeout=60)
            self._sql("UPDATE events SET location='nas' WHERE id=?", (eid,))
            for name in self._companions(rel):
                _remove(os.path.join(self.local_dir, name))
            _prune_empty_dirs(self.local_dir, os.path.dirname(rel))
            with self._status_lock:
                self._status["last_copy"] = time.time()

    def _cleanup(self):
        if self.keep_days <= 0:
            return
        limit = time.time() - self.keep_days * 86400
        old = self._sql("SELECT * FROM events WHERE start<? AND deleted=0", (limit,), fetch=True)
        for row in old:
            event = self._row(row)
            try:
                self.delete_event(event)
            except (OSError, NasTimeout) as exc:
                log.warning("effacement de %s différé : %s", event["file"], exc)
                break

    def run(self):
        last_cleanup = 0
        while True:
            if self.nas_dir:
                try:
                    state, message = self._on_nas(self._probe)
                except NasTimeout as exc:
                    state, message = "unreachable", "Le NAS ne répond pas."
                    log.debug("%s", exc)
                self._set_status(state, message)
                if state == "ok":
                    try:
                        self._move_pending()
                    except (OSError, NasTimeout) as exc:
                        self._set_status("unreachable", f"Copie interrompue : {exc}")
            if time.time() - last_cleanup > 3600:
                last_cleanup = time.time()
                self._cleanup()
            self._wake.wait(PROBE_INTERVAL)
            self._wake.clear()


def _remove(path: str):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _prune_empty_dirs(root: str, rel_dir: str):
    while rel_dir:
        try:
            os.rmdir(os.path.join(root, rel_dir))
        except OSError:
            return
        rel_dir = os.path.dirname(rel_dir)
