#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Les contributeurs de rtsp-web
"""
rtsp-web — interface web de visionnage de caméras sur le réseau local,
avec détection de mouvement et photo de chaque passage.

  mediamtx.py   déclare les caméras auprès de MediaMTX et suit leur état
  detection.py  analyse le flux de chaque caméra (OpenCV)
  storage.py    écrit les photos en local puis les copie sur le NAS
  settings.py   réglages modifiables depuis l'interface (settings.json)
  recording.py  extraits vidéo des passages, tirés de l'enregistrement continu
"""
import argparse
import logging
import os
import re
import signal
import sys

# Lecture RTSP par OpenCV en TCP : à définir avant le premier import de cv2
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import yaml  # noqa: E402
from flask import (Flask, Response, abort, jsonify, redirect, render_template,  # noqa: E402
                   request, send_file, url_for)

from detection import Detector  # noqa: E402
from mediamtx import CameraManager, MediaMTX  # noqa: E402
from recording import Recorder  # noqa: E402
from settings import SettingsError, SettingsStore  # noqa: E402
from storage import NasTimeout, Storage  # noqa: E402

__version__ = "4.0.0"

log = logging.getLogger("rtsp-web")
ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
EVENT_RE = re.compile(r"^[a-z0-9_-]{1,32}_\d{8}-\d{6}_\d{3}(-\d+)?$")


# --------------------------------------------------------------------- Web
def create_app(ctx: dict) -> Flask:
    app = Flask(__name__)
    cfg = ctx["cfg"]
    manager, settings, storage = ctx["manager"], ctx["settings"], ctx["storage"]
    detectors = ctx["detectors"]
    recorder = ctx["recorder"]
    cameras = {c["id"]: c for c in cfg["cameras"]}
    mtx = cfg.get("mediamtx") or {}
    page = {
        "title": (cfg.get("server") or {}).get("title", "Caméras"),
        "version": __version__,
        "player": {"webrtcPort": int(mtx.get("webrtc_port", 8889)),
                   "hlsPort": int(mtx.get("hls_port", 8888))},
    }

    def camera_or_404(cid):
        if cid not in cameras:
            abort(404)
        return cameras[cid]

    def detector_or_404(cid):
        camera_or_404(cid)
        if cid not in detectors:
            abort(404, "La détection n'est pas activée pour cette caméra dans config.yaml.")
        return detectors[cid]

    @app.before_request
    def guard_writes():
        # Protection contre les requêtes envoyées par un autre site :
        # un en-tête personnalisé impose une vérification préalable du navigateur.
        if request.method in ("POST", "PUT", "DELETE") and \
                request.headers.get("X-Requested-With") != "rtsp-web":
            abort(403)

    def full_status():
        result = []
        for cam in manager.status():
            det = detectors.get(cam["id"])
            cam["detection"] = None
            if det:
                s = settings.get(cam["id"])
                cam["detection"] = {
                    "mode": s["mode"], **det.status(),
                    "video": s["video"],
                    "recording": s["mode"] == "record" and s["video"],
                    "buffer_mb": round(recorder.buffer_bytes(cam["id"]) / 1e6),
                }
            result.append(cam)
        return result

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def direct():
        return render_template("direct.html", page=page, nav="direct",
                               cameras=full_status())

    @app.get("/passages")
    def passages():
        return render_template("passages.html", page=page, nav="passages",
                               cameras=[{"id": c["id"], "name": c.get("name", c["id"])}
                                        for c in cfg["cameras"]])

    @app.get("/reglages")
    def settings_index():
        if detectors:
            return redirect(url_for("settings_page", cid=next(iter(detectors))))
        return render_template("settings.html", page=page, nav="reglages", camera=None,
                               cameras=full_status(), storage_info=ctx["storage_info"])

    @app.get("/reglages/<cid>")
    def settings_page(cid):
        cam = camera_or_404(cid)
        if cid not in detectors:
            abort(404)
        return render_template("settings.html", page=page, nav="reglages",
                               camera={"id": cid, "name": cam.get("name", cid)},
                               cameras=full_status(), settings=settings.get(cid),
                               defaults=settings.defaults, storage_info=ctx["storage_info"])

    # ------------------------------------------------------------------ API
    @app.get("/api/status")
    def api_status():
        return jsonify({"cameras": full_status(), "storage": storage.status(),
                        "video": {"pending": recorder.pending(), "error": recorder.last_error}})

    @app.get("/api/cameras")
    def api_cameras():                 # compatibilité avec la version 2
        return jsonify(manager.status())

    @app.route("/api/cameras/<cid>/settings", methods=["GET", "PUT"])
    def api_settings(cid):
        detector_or_404(cid)
        if request.method == "GET":
            return jsonify(settings.get(cid))
        try:
            return jsonify(settings.update(cid, request.get_json(force=True, silent=True) or {}))
        except SettingsError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/cameras/<cid>/settings/reset")
    def api_settings_reset(cid):
        detector_or_404(cid)
        return jsonify(settings.reset_thresholds(cid))

    @app.get("/api/cameras/<cid>/detections")
    def api_detections(cid):
        return jsonify(detector_or_404(cid).detections())

    @app.get("/api/cameras/<cid>/mask.png")
    def api_mask(cid):
        png = detector_or_404(cid).mask_png()
        if png is None:
            return Response(status=204)
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    def api_events():
        camera = request.args.get("camera") or None
        if camera:
            camera_or_404(camera)
        try:
            events = storage.list_events(camera=camera, before=request.args.get("before"),
                                         limit=request.args.get("limit", 200))
        except ValueError:
            abort(400)
        names = {c["id"]: c.get("name", c["id"]) for c in cfg["cameras"]}
        for ev in events:
            ev["camera_name"] = names.get(ev["camera"], ev["camera"])
        return jsonify(events)

    def event_or_404(eid):
        if not EVENT_RE.match(eid):
            abort(404)
        event = storage.get_event(eid)
        if event is None:
            abort(404)
        return event

    @app.get("/api/events/<eid>/thumb")
    def api_thumb(eid):
        event_or_404(eid)
        path = storage.thumb_path(eid)
        if not os.path.exists(path):
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=86400)

    @app.get("/api/events/<eid>/photo")
    def api_photo(eid):
        event = event_or_404(eid)
        try:
            data = storage.read_photo(event)
        except NasTimeout:
            return Response("Le NAS ne répond pas.", status=504)
        except OSError:
            abort(404)
        headers = {"Cache-Control": "max-age=86400"}
        if request.args.get("download"):
            name = event["camera"] + "_" + os.path.basename(event["file"])
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return Response(data, mimetype="image/jpeg", headers=headers)

    @app.get("/api/events/<eid>/clip")
    def api_clip(eid):
        event = event_or_404(eid)
        if event["clip_state"] != "ok":
            abort(404)
        try:
            data = storage.read_clip(event)
        except NasTimeout:
            return Response("Le NAS ne répond pas.", status=504)
        except OSError:
            abort(404)
        resp = Response(data, mimetype="video/mp4")
        if request.args.get("download"):
            name = event["camera"] + "_" + os.path.basename(event["clip"])
            resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
        resp.headers["Cache-Control"] = "max-age=86400"
        # Lecture par morceaux (requêtes Range), indispensable à Safari
        return resp.make_conditional(request, accept_ranges=True, complete_length=len(data))

    @app.delete("/api/events/<eid>")
    def api_delete(eid):
        event = event_or_404(eid)
        try:
            storage.delete_event(event)
        except NasTimeout:
            return jsonify({"error": "Le NAS ne répond pas."}), 504
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})

    @app.get("/healthz")
    def healthz():
        cams = manager.status()
        return {"ok": all(c["state"] != "error" for c in cams), "version": __version__,
                "online": sum(c["state"] == "online" for c in cams), "total": len(cams),
                "storage": storage.status()["state"]}

    return app


# -------------------------------------------------------------------- Main
def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cameras = cfg.get("cameras") or cfg.get("streams") or []   # « streams » : version 1
    if not cameras:
        sys.exit(f"{path} : aucune caméra définie dans « cameras ».")
    seen = set()
    for cam in cameras:
        sid = cam.get("id", "")
        if not ID_RE.match(sid):
            sys.exit(f"{path} : identifiant invalide « {sid} » "
                     "(minuscules, chiffres, - et _ ; 32 caractères max).")
        if sid in seen:
            sys.exit(f"{path} : identifiant en double « {sid} ».")
        if not str(cam.get("url", "")).startswith(("rtsp://", "rtsps://")):
            sys.exit(f"{path} : la caméra « {sid} » doit avoir une URL rtsp://")
        seen.add(sid)
    cfg["cameras"] = cameras
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Interface web de visionnage de caméras")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"rtsp-web {__version__}")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_config(args.config)
    mtx = cfg.get("mediamtx") or {}
    st = cfg.get("storage") or {}
    state_dir = st.get("state_dir", "/var/lib/rtsp-web")
    os.makedirs(state_dir, exist_ok=True)

    storage = Storage(nas_dir=st.get("nas_dir"),
                      local_dir=st.get("local_dir", os.path.join(state_dir, "pending")),
                      state_dir=state_dir, keep_days=st.get("keep_days", 0))
    detect_ids = [c["id"] for c in cfg["cameras"] if c.get("detection")]
    settings = SettingsStore(os.path.join(state_dir, "settings.json"), detect_ids,
                             cfg.get("detection_defaults"))
    rtsp_port = int(mtx.get("rtsp_port", 8554))
    video = cfg.get("video") or {}
    recorder = Recorder(storage,
                        playback_url=mtx.get("playback", "http://127.0.0.1:9996"),
                        recordings_dir=video.get("recordings_dir", "/var/lib/mediamtx/recordings"),
                        buffer_minutes=video.get("buffer_minutes", 30))
    detectors = {cid: Detector(cid, f"rtsp://127.0.0.1:{rtsp_port}/{cid}", settings, storage,
                               recorder)
                 for cid in detect_ids}

    def record_for(cid):
        # Enregistrement continu seulement en position Enregistrement avec vidéo
        if cid not in detectors:
            return recorder.record_config(False)
        s = settings.get(cid)
        return recorder.record_config(s["mode"] == "record" and s["video"])

    manager = CameraManager(cfg["cameras"], MediaMTX(mtx.get("api", "http://127.0.0.1:9997")),
                            record_config=record_for)
    settings.subscribe(lambda _cid: manager.request_sync())

    manager.start()
    storage.start()
    recorder.start()
    for det in detectors.values():
        det.start()

    def shutdown(signum, _frame):
        log.info("arrêt demandé (signal %d)", signum)
        for det in detectors.values():
            det.stop()
        for det in detectors.values():
            det.join(timeout=5)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    ctx = {"cfg": cfg, "manager": manager, "settings": settings, "storage": storage,
           "detectors": detectors, "recorder": recorder,
           "storage_info": {"nas_dir": storage.nas_dir, "config": os.path.abspath(args.config)}}
    app = create_app(ctx)
    server = cfg.get("server") or {}
    host, port = server.get("host", "0.0.0.0"), int(server.get("port", 8080))
    log.info("rtsp-web %s — interface web sur http://%s:%d/", __version__, host, port)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=12, ident="rtsp-web")
    except ImportError:
        log.warning("waitress absent : utilisation du serveur de développement Flask")
        app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
