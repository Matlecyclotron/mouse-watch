/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Page Réglages d'une caméra : zones à main levée, position, seuils. */
(() => {
  const CAM = window.CAMERA;
  const MAX_ZOOM = 8;
  const MAX_POINTS = 200;
  const MODE_DOT = { off: "off", observe: "obs", record: "rec" };
  const RESULT_DOT = { saved: "rec", observed: "obs", cooldown: "warn", small: "off", brief: "off", global: "off" };
  const NAS_TEXT = {
    ok: "Disponible", checking: "Vérification…", missing: "Non monté : dossier introuvable",
    unreachable: "Injoignable", denied: "Écriture refusée (droits du partage)", disabled: "Non configuré : photos conservées sur le serveur",
  };
  const COLORS = {
    include: { stroke: "#ffffff", fill: "rgba(255,255,255,0.13)" },
    exclude: { stroke: "#e8a85a", fill: "rgba(232,168,90,0.28)" },
  };

  const $ = sel => document.querySelector(sel);
  const editor = $("#editor"), view = $("#view"), layer = $("#layer");
  const video = layer.querySelector("video"), canvas = $("#zones"), maskImg = $("#mask");
  const ctx = canvas.getContext("2d");
  let settings = CAM.settings;
  let zones = structuredClone(settings.zones);
  const history = [];
  let tool = "include";
  const z = { s: 1, x: 0, y: 0 };

  /* ------------------------------------------------------------ enregistrement */
  const saveState = $("#save-state");
  let saveTimer = null, pending = {};
  function save(patch, delay = 250) {
    Object.assign(pending, patch);
    clearTimeout(saveTimer);
    saveState.textContent = "Enregistrement…";
    saveTimer = setTimeout(async () => {
      const body = pending; pending = {};
      try {
        settings = await RW.api(`/api/cameras/${CAM.id}/settings`, { method: "PUT", body });
        saveState.textContent = "Enregistré.";
        if ("mode" in body) RW.poll();
      } catch (err) {
        saveState.textContent = `Échec de l'enregistrement : ${err.message}`;
      }
    }, delay);
  }

  /* ------------------------------------------------------------ vidéo */
  const player = new LivePlayer(video, CAM.id, {
    onPlaying: () => {
      editor.dataset.playing = "";
      view.dataset.playing = "";
      if (video.videoWidth && video.videoHeight) {
        view.style.setProperty("--ratio", `${video.videoWidth} / ${video.videoHeight}`);
        $("#cam-info").textContent = `${video.videoWidth} × ${video.videoHeight}`;
      }
    },
    onStop: () => { delete view.dataset.playing; },
  });
  document.addEventListener("rtspweb:leave", () => player.disconnect());

  /* ------------------------------------------------------------ zoom */
  function applyZoom() {
    layer.style.transform = z.s === 1 ? "" : `translate(${z.x}px, ${z.y}px) scale(${z.s})`;
    editor.querySelector(".level").textContent = `${Math.round(z.s * 100)} %`;
    draw();
  }
  function clampPan() {
    const w = view.clientWidth, h = view.clientHeight;
    z.x = Math.min(0, Math.max(w - w * z.s, z.x));
    z.y = Math.min(0, Math.max(h - h * z.s, z.y));
  }
  function zoomAt(scale, px, py) {
    scale = Math.min(MAX_ZOOM, Math.max(1, scale));
    z.x = px - (px - z.x) * scale / z.s;
    z.y = py - (py - z.y) * scale / z.s;
    z.s = scale;
    resizeCanvas();                                   // netteté du tracé selon le zoom
  }
  editor.querySelectorAll("[data-zoom]").forEach(b => b.addEventListener("click", () => {
    const cx = view.clientWidth / 2, cy = view.clientHeight / 2;
    if (b.dataset.zoom === "in") zoomAt(z.s * 1.5, cx, cy);
    else if (b.dataset.zoom === "out") zoomAt(z.s / 1.5, cx, cy);
    else zoomAt(1, 0, 0);
  }));
  view.addEventListener("wheel", e => {
    e.preventDefault();
    const r = view.getBoundingClientRect();
    zoomAt(z.s * Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  /* ------------------------------------------------------------ dessin */
  function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(view.clientWidth * dpr * Math.min(z.s, 3));
    canvas.height = Math.round(view.clientHeight * dpr * Math.min(z.s, 3));
    clampPan();
    applyZoom();
  }
  new ResizeObserver(resizeCanvas).observe(view);

  function drawPoly(points, kind, closed, withHandles) {
    const W = canvas.width, H = canvas.height;
    const px = W / view.clientWidth / z.s;           // pixels du canevas par pixel d'écran
    if (points.length < 2) return;
    ctx.beginPath();
    points.forEach(([x, y], i) => (i ? ctx.lineTo(x * W, y * H) : ctx.moveTo(x * W, y * H)));
    if (closed) {
      ctx.closePath();
      ctx.fillStyle = COLORS[kind].fill;
      ctx.fill();
      ctx.setLineDash([7 * px, 5 * px]);
    } else {
      ctx.setLineDash([]);
    }
    ctx.lineWidth = 2 * px;
    ctx.strokeStyle = COLORS[kind].stroke;
    ctx.stroke();
    if (withHandles) {
      ctx.setLineDash([]);
      for (const [x, y] of points) {
        ctx.beginPath();
        ctx.arc(x * W, y * H, 4 * px, 0, Math.PI * 2);
        ctx.fillStyle = COLORS[kind].stroke;
        ctx.fill();
      }
    }
  }

  let stroke = null, drag = null;
  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const kind of ["include", "exclude"]) {
      for (const poly of zones[kind]) drawPoly(poly, kind, true, true);
    }
    if (stroke) drawPoly(stroke.points, stroke.kind, false, false);
    const ni = zones.include.length, ne = zones.exclude.length;
    $("#zone-summary").textContent = ni === 0
      ? (ne ? `Toute l'image est analysée, sauf ${ne} zone${ne > 1 ? "s" : ""} d'exclusion.` : "Aucune zone tracée : toute l'image est analysée.")
      : `${ni} zone${ni > 1 ? "s" : ""} à surveiller` + (ne ? `, ${ne} zone${ne > 1 ? "s" : ""} d'exclusion.` : ".");
  }

  // Simplification du tracé (Ramer-Douglas-Peucker), en coordonnées isotropes
  function simplify(points, epsilon) {
    const ar = view.clientWidth / view.clientHeight;
    const P = points.map(([x, y]) => [x * ar, y]);
    const keep = new Uint8Array(P.length);
    keep[0] = keep[P.length - 1] = 1;
    const stack = [[0, P.length - 1]];
    while (stack.length) {
      const [a, b] = stack.pop();
      const [ax, ay] = P[a], [bx, by] = P[b];
      const len = Math.hypot(bx - ax, by - ay) || 1e-9;
      let best = -1, dmax = 0;
      for (let i = a + 1; i < b; i++) {
        const d = Math.abs((by - ay) * P[i][0] - (bx - ax) * P[i][1] + bx * ay - by * ax) / len;
        if (d > dmax) { dmax = d; best = i; }
      }
      if (dmax > epsilon && best > 0) { keep[best] = 1; stack.push([a, best], [best, b]); }
    }
    return points.filter((_, i) => keep[i]);
  }
  function area(points) {
    let s = 0;
    for (let i = 0; i < points.length; i++) {
      const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % points.length];
      s += x1 * y2 - x2 * y1;
    }
    return Math.abs(s) / 2;
  }

  function toNorm(e) {
    const r = view.getBoundingClientRect();
    const x = ((e.clientX - r.left) - z.x) / z.s / r.width;
    const y = ((e.clientY - r.top) - z.y) / z.s / r.height;
    return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
  }
  function hitVertex(e) {
    const r = view.getBoundingClientRect();
    const [nx, ny] = toNorm(e);
    let best = null, bestD = 12;                      // pixels d'écran
    for (const kind of ["include", "exclude"]) {
      zones[kind].forEach((poly, pi) => poly.forEach(([x, y], vi) => {
        const d = Math.hypot((x - nx) * r.width * z.s, (y - ny) * r.height * z.s);
        if (d < bestD) { bestD = d; best = { kind, pi, vi }; }
      }));
    }
    return best;
  }
  function snapshot() {
    history.push(JSON.stringify(zones));
    if (history.length > 50) history.shift();
  }

  const pointers = new Map();
  let pan = null;
  canvas.addEventListener("pointerdown", e => {
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 2) { stroke = drag = pan = null; draw(); return; }
    if (tool === "pan") { pan = [e.clientX, e.clientY]; return; }
    const hit = hitVertex(e);
    if (hit) { snapshot(); drag = hit; return; }
    stroke = { kind: tool, points: [toNorm(e)], last: [e.clientX, e.clientY] };
  });
  canvas.addEventListener("pointermove", e => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId);
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    const r = view.getBoundingClientRect();
    if (pointers.size === 2) {                        // pincement
      const other = [...pointers].find(([id]) => id !== e.pointerId)[1];
      const d0 = Math.hypot(prev[0] - other[0], prev[1] - other[1]);
      const d1 = Math.hypot(e.clientX - other[0], e.clientY - other[1]);
      if (d0 > 0) zoomAt(z.s * d1 / d0, (e.clientX + other[0]) / 2 - r.left, (e.clientY + other[1]) / 2 - r.top);
    } else if (pan) {
      z.x += e.clientX - pan[0]; z.y += e.clientY - pan[1];
      pan = [e.clientX, e.clientY];
      clampPan(); applyZoom();
    } else if (drag) {
      zones[drag.kind][drag.pi][drag.vi] = toNorm(e).map(v => Math.round(v * 10000) / 10000);
      draw();
    } else if (stroke && Math.hypot(e.clientX - stroke.last[0], e.clientY - stroke.last[1]) >= 3) {
      stroke.points.push(toNorm(e));
      stroke.last = [e.clientX, e.clientY];
      draw();
    }
  });
  function endPointer(e) {
    pointers.delete(e.pointerId);
    if (drag) { drag = null; save({ zones }); return; }
    pan = null;
    if (!stroke) return;
    const { kind, points } = stroke;
    stroke = null;
    let eps = 0.004, poly = simplify(points, eps);
    while (poly.length > MAX_POINTS) { eps *= 1.5; poly = simplify(points, eps); }
    if (poly.length >= 3 && area(poly) > 0.0004) {
      snapshot();
      zones[kind].push(poly.map(([x, y]) => [Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]));
      save({ zones });
    }
    draw();
  }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", e => { pointers.delete(e.pointerId); stroke = drag = pan = null; draw(); });

  editor.querySelectorAll(".seg [data-tool]").forEach(b => b.addEventListener("click", () => {
    tool = b.dataset.tool;
    editor.dataset.tool = tool;
    editor.querySelectorAll(".seg [data-tool]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  }));
  $("#undo").addEventListener("click", () => {
    if (!history.length) return;
    zones = JSON.parse(history.pop());
    draw(); save({ zones });
  });
  $("#clear").addEventListener("click", () => {
    if (!zones.include.length && !zones.exclude.length) return;
    if (!confirm("Effacer toutes les zones ? Toute l'image sera alors analysée.")) return;
    snapshot();
    zones = { include: [], exclude: [] };
    draw(); save({ zones });
  });

  /* ------------------------------------------------------------ masque */
  let maskTimer = null, maskUrl = null;
  async function refreshMask() {
    try {
      const res = await fetch(`/api/cameras/${CAM.id}/mask.png`, { cache: "no-store" });
      if (res.status === 204) { maskImg.hidden = true; return; }
      const url = URL.createObjectURL(await res.blob());
      maskImg.src = url;
      maskImg.hidden = false;
      if (maskUrl) URL.revokeObjectURL(maskUrl);
      maskUrl = url;
    } catch { maskImg.hidden = true; }
  }
  $("#show-mask").addEventListener("change", e => {
    clearInterval(maskTimer);
    maskImg.hidden = true;
    if (e.target.checked) { refreshMask(); maskTimer = setInterval(refreshMask, 400); }
  });

  /* ------------------------------------------------------------ position et seuils */
  const FORMAT = {
    min_size: v => `${RW.fmtNumber(Number(v), Number(v) % 1 ? 1 : 0)} % de l'image`,
    sensitivity: v => `${v <= 3 ? "faible" : v <= 7 ? "moyenne" : "élevée"} (${v}/10)`,
    persistence: v => `${v} image${v > 1 ? "s" : ""}`,
    cooldown: v => `${v} s`,
    pre_roll: v => `${v} s`,
    post_roll: v => `${v} s`,
  };
  function showSettings(s) {
    document.querySelectorAll('input[name="mode"]').forEach(r => { r.checked = r.value === s.mode; });
    $("#video").checked = s.video;
    $("#pre_roll").disabled = $("#post_roll").disabled = !s.video;
    for (const key of Object.keys(FORMAT)) {
      $(`#${key}`).value = s[key];
      document.querySelector(`[data-out="${key}"]`).textContent = FORMAT[key](s[key]);
    }
  }
  document.querySelectorAll('input[name="mode"]').forEach(r => r.addEventListener("change", () => save({ mode: r.value }, 0)));
  $("#video").addEventListener("change", e => {
    settings.video = e.target.checked;
    $("#pre_roll").disabled = $("#post_roll").disabled = !settings.video;
    save({ video: settings.video }, 0);
  });
  document.querySelectorAll("input[data-key]").forEach(input => {
    const key = input.dataset.key;
    input.addEventListener("input", () => {
      document.querySelector(`[data-out="${key}"]`).textContent = FORMAT[key](Number(input.value));
    });
    input.addEventListener("change", () => save({ [key]: Number(input.value) }));
  });
  $("#reset").addEventListener("click", async () => {
    try {
      settings = await RW.api(`/api/cameras/${CAM.id}/settings/reset`, { method: "POST" });
      showSettings(settings);
      saveState.textContent = "Valeurs par défaut rétablies.";
    } catch (err) { saveState.textContent = `Échec : ${err.message}`; }
  });

  /* ------------------------------------------------------------ détections */
  async function loadDetections() {
    try {
      const rows = (await RW.api(`/api/cameras/${CAM.id}/detections`)).slice(0, 12);
      $("#detections").replaceChildren(...rows.map(d => {
        const tr = document.createElement("tr");
        const cells = [RW.fmtTime(d.time), `${RW.fmtNumber(d.size)} %`, d.result === "global" ? "—" : `${RW.fmtNumber(d.duration)} s`];
        for (const text of cells) { const td = document.createElement("td"); td.textContent = text; tr.append(td); }
        const td = document.createElement("td");
        const span = document.createElement("span");
        span.style.cssText = "display:inline-flex;align-items:center;gap:.5rem";
        const dot = document.createElement("span");
        dot.className = `dot ${RESULT_DOT[d.result] || "off"}`;
        dot.setAttribute("aria-hidden", "true");
        span.append(dot, d.label);
        td.append(span);
        tr.append(td);
        return tr;
      }));
      $("#det-empty").textContent = rows.length ? "" :
        settings.mode === "off" ? "La détection est arrêtée." : "Aucune détection depuis le démarrage de l'analyse.";
    } catch { /* nouvel essai */ }
  }

  /* ------------------------------------------------------------ état général */
  document.addEventListener("rtspweb:status", e => {
    const { cameras, storage } = e.detail;
    for (const c of cameras) {
      const dot = document.querySelector(`.side a[data-id="${c.id}"] .side-dot`);
      if (dot && c.detection) dot.className = `dot side-dot ${MODE_DOT[c.detection.mode]}`;
    }
    const cam = cameras.find(c => c.id === CAM.id);
    if (cam) {
      player.follow(cam.state);
      view.querySelector(".notice strong").textContent =
        cam.state === "offline" ? "Caméra indisponible" : cam.state === "error" ? "Service vidéo indisponible" : "Connexion à la caméra…";
      view.querySelector(".notice small").textContent = cam.error || "";
      const det = cam.detection;
      if (det) {
        if (!Object.keys(pending).length && det.mode !== settings.mode) {
          settings.mode = det.mode;
          showSettings(settings);
        }
        $("#vid-state").textContent =
          !det.video ? "Extrait vidéo désactivé : seule la photo est enregistrée." :
          det.recording ? `Enregistrement continu actif · tampon ${det.buffer_mb} Mo sur le serveur.` +
                          (e.detail.video.error ? ` Problème : ${e.detail.video.error}` : "") :
          "L'enregistrement continu démarre en position Enregistrement.";
        $("#det-state").textContent =
          det.mode === "off" ? "" :
          det.state === "running" ? `Analyse en cours (${RW.fmtNumber(det.fps)} images par seconde).` :
          det.state === "error" ? `Analyse indisponible : ${det.error}` : "Démarrage de l'analyse…";
      }
    }
    $("#sto-state").textContent = NAS_TEXT[storage.state] || storage.state;
    $("#sto-pending").textContent = `${storage.pending} photo${storage.pending > 1 ? "s" : ""} sur le serveur`;
    $("#sto-last").textContent = storage.last_copy ? RW.fmtTime(storage.last_copy) : "—";
  });

  showSettings(settings);
  draw();
  loadDetections();
  setInterval(loadDetections, 3000);
})();
