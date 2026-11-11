/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Page Direct : mosaïque des caméras. */
(() => {
  const VIDEO_CODECS = new Set(["H264", "H265", "AV1", "VP8", "VP9", "MJPEG", "MPEG-4 Video", "MPEG-1/2 Video"]);
  const MAX_ZOOM = 8;
  const NOTICES = {
    connecting: ["Connexion à la caméra…", ""],
    offline: ["Caméra indisponible", "Nouvel essai automatique en cours."],
    error: ["Service vidéo indisponible", ""],
  };
  const BADGES = {
    off: ["off", "Détection arrêtée"],
    observe: ["obs", "Observation"],
    record: ["rec", "Enregistrement"],
  };

  class Tile {
    constructor(el) {
      this.el = el;
      this.id = el.dataset.id;
      this.view = el.querySelector(".view");
      this.video = el.querySelector("video");
      this.z = { s: 1, x: 0, y: 0 };
      this.pointers = new Map();
      this.player = new LivePlayer(this.video, this.id, {
        onPlaying: () => { this.el.dataset.playing = ""; },
        onStop: () => { delete this.el.dataset.playing; },
      });
      this.bindZoom();
    }

    update(cam) {
      this.el.dataset.state = cam.state;
      this.el.querySelector(".state-dot").className =
        "dot state-dot " + (cam.state === "online" ? "ok" : cam.state === "connecting" ? "warn" : "");
      this.el.querySelector('[data-act="sound"]').hidden = !cam.codecs.some(c => !VIDEO_CODECS.has(c));
      this.player.follow(cam.state);
      const [title, hint] = NOTICES[cam.state] || NOTICES.connecting;
      this.el.querySelector(".notice strong").textContent = title;
      this.el.querySelector(".notice small").textContent = cam.error || hint;

      const det = cam.detection;
      if (!det) return;
      this.el.querySelectorAll(".seg button").forEach(b => {
        b.setAttribute("aria-pressed", String(b.dataset.mode === det.mode));
      });
      const badge = this.el.querySelector(".badge");
      const [cls, label] = BADGES[det.mode];
      badge.hidden = false;
      badge.querySelector(".dot").className = `dot ${cls}`;
      badge.querySelector(".txt").textContent =
        det.mode !== "off" && det.state === "error" ? `${label} · analyse indisponible`
        : det.recording ? `${label} · vidéo` : label;
    }

    async setMode(mode, btn) {
      const group = btn.parentElement;
      group.querySelectorAll("button").forEach(b => { b.disabled = true; });
      try {
        await RW.api(`/api/cameras/${this.id}/settings`, { method: "PUT", body: { mode } });
        await RW.poll();
      } catch (err) {
        this.flash(`Changement impossible : ${err.message}`);
      } finally {
        group.querySelectorAll("button").forEach(b => { b.disabled = false; });
      }
    }

    /* ---------- Zoom numérique ---------- */
    applyZoom() {
      const { s, x, y } = this.z;
      this.video.style.transform = s === 1 ? "" : `translate(${x}px, ${y}px) scale(${s})`;
      this.el.querySelector(".level").textContent = `${Math.round(s * 100)} %`;
      if (s > 1) this.el.dataset.zoomed = ""; else delete this.el.dataset.zoomed;
    }
    clampPan() {
      const w = this.view.clientWidth, h = this.view.clientHeight, s = this.z.s;
      this.z.x = Math.min(0, Math.max(w - w * s, this.z.x));
      this.z.y = Math.min(0, Math.max(h - h * s, this.z.y));
    }
    zoomAt(scale, px, py) {
      const s = this.z.s;
      scale = Math.min(MAX_ZOOM, Math.max(1, scale));
      this.z.x = px - (px - this.z.x) * scale / s;
      this.z.y = py - (py - this.z.y) * scale / s;
      this.z.s = scale;
      this.clampPan();
      this.applyZoom();
    }
    zoomCenter(f) { this.zoomAt(this.z.s * f, this.view.clientWidth / 2, this.view.clientHeight / 2); }

    bindZoom() {
      const v = this.view;
      const local = e => { const r = v.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      const playing = () => "playing" in this.el.dataset;
      v.addEventListener("wheel", e => {
        if (!playing()) return;
        e.preventDefault();
        this.zoomAt(this.z.s * Math.exp(-e.deltaY * 0.0015), ...local(e));
      }, { passive: false });
      v.addEventListener("dblclick", e => {
        if (!playing()) return;
        if (this.z.s > 1) this.zoomAt(1, 0, 0); else this.zoomAt(2.5, ...local(e));
      });
      v.addEventListener("pointerdown", e => { v.setPointerCapture(e.pointerId); this.pointers.set(e.pointerId, local(e)); });
      v.addEventListener("pointermove", e => {
        if (!this.pointers.has(e.pointerId)) return;
        const prev = this.pointers.get(e.pointerId), cur = local(e);
        if (this.pointers.size === 2) {
          const other = [...this.pointers].find(([id]) => id !== e.pointerId)[1];
          const d0 = Math.hypot(prev[0] - other[0], prev[1] - other[1]);
          const d1 = Math.hypot(cur[0] - other[0], cur[1] - other[1]);
          if (d0 > 0) this.zoomAt(this.z.s * d1 / d0, (cur[0] + other[0]) / 2, (cur[1] + other[1]) / 2);
        } else if (this.z.s > 1) {
          this.z.x += cur[0] - prev[0];
          this.z.y += cur[1] - prev[1];
          this.clampPan();
          this.applyZoom();
        }
        this.pointers.set(e.pointerId, cur);
      });
      const release = e => this.pointers.delete(e.pointerId);
      v.addEventListener("pointerup", release);
      v.addEventListener("pointercancel", release);
      new ResizeObserver(() => { this.clampPan(); this.applyZoom(); }).observe(v);
    }

    /* ---------- Capture de la zone affichée ---------- */
    capture() {
      const vid = this.video, vw = vid.videoWidth, vh = vid.videoHeight;
      if (!vw || !vh) return this.flash("Aucune image à capturer");
      const w = this.view.clientWidth, h = this.view.clientHeight, { s, x, y } = this.z;
      const k = Math.min(w / vw, h / vh), ox = (w - vw * k) / 2, oy = (h - vh * k) / 2;
      let sx = (-x / s - ox) / k, sy = (-y / s - oy) / k;
      const x2 = Math.min(vw, sx + w / s / k), y2 = Math.min(vh, sy + h / s / k);
      sx = Math.max(0, sx); sy = Math.max(0, sy);
      const sw = x2 - sx, sh = y2 - sy;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(sw); canvas.height = Math.round(sh);
      canvas.getContext("2d").drawImage(vid, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(blob => {
        if (!blob) return this.flash("Capture impossible");
        const a = document.createElement("a");
        const d = new Date(), p = n => String(n).padStart(2, "0");
        a.href = URL.createObjectURL(blob);
        a.download = `${this.id}_${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}.png`;
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        this.flash(`Image enregistrée (${canvas.width} × ${canvas.height})`);
      }, "image/png");
    }

    flash(text) {
      const f = this.el.querySelector(".flash");
      f.textContent = text;
      f.classList.add("show");
      clearTimeout(this.flashTimer);
      this.flashTimer = setTimeout(() => f.classList.remove("show"), 2200);
    }
  }

  /* ---------- Derniers passages ---------- */
  async function loadRecent() {
    const box = document.getElementById("recent");
    if (!box) return;
    try {
      const events = await RW.api("/api/events?limit=6");
      box.replaceChildren(...events.map(ev => {
        const fig = document.createElement("figure");
        fig.className = "thumb";
        const link = document.createElement("a");
        link.href = `/passages#${ev.id}`;
        const img = document.createElement("img");
        img.loading = "lazy";
        img.src = `/api/events/${ev.id}/thumb`;
        img.alt = `${ev.camera_name}, passage de ${RW.fmtTime(ev.start)}`;
        link.append(img);
        const cap = document.createElement("figcaption");
        const left = document.createElement("span");
        left.textContent = `${ev.camera_name} · ${RW.fmtTime(ev.start)}`;
        const right = document.createElement("span");
        right.className = "muted";
        right.textContent = `${RW.fmtNumber(ev.duration)} s`;
        cap.append(left, right);
        fig.append(link, cap);
        return fig;
      }));
      document.getElementById("recent-empty").hidden = events.length > 0;
    } catch { /* nouvel essai au prochain cycle */ }
  }

  const tiles = new Map();
  document.querySelectorAll(".tile").forEach(el => tiles.set(el.dataset.id, new Tile(el)));

  document.addEventListener("rtspweb:status", e => {
    e.detail.cameras.forEach(cam => tiles.get(cam.id)?.update(cam));
  });
  document.addEventListener("rtspweb:leave", () => tiles.forEach(t => t.player.disconnect()));

  document.addEventListener("click", e => {
    const btn = e.target.closest("button");
    const tileEl = btn?.closest(".tile");
    if (!tileEl) return;
    const t = tiles.get(tileEl.dataset.id);
    if (btn.dataset.mode) return t.setMode(btn.dataset.mode, btn);
    switch (btn.dataset.act) {
      case "zoom-in": t.zoomCenter(1.5); break;
      case "zoom-out": t.zoomCenter(1 / 1.5); break;
      case "zoom-reset": t.zoomAt(1, 0, 0); break;
      case "capture": t.capture(); break;
      case "sound":
        t.video.muted = !t.video.muted;
        btn.setAttribute("aria-pressed", String(!t.video.muted));
        btn.textContent = t.video.muted ? "Son" : "Couper le son";
        break;
      case "full":
        if (document.fullscreenElement) document.exitFullscreen();
        else if (t.el.requestFullscreen) t.el.requestFullscreen();
        else if (t.video.webkitEnterFullscreen) t.video.webkitEnterFullscreen();
        break;
    }
  });
  document.addEventListener("fullscreenchange", () => {
    document.querySelectorAll('[data-act="full"]').forEach(b => {
      b.textContent = b.closest(".tile") === document.fullscreenElement ? "Quitter le plein écran" : "Plein écran";
    });
  });

  loadRecent();
  setInterval(loadRecent, 15000);
})();
