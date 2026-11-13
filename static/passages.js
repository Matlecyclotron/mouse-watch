/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Page Passages : photos classées par nuit. */
(() => {
  const PAGE = 200;
  const nightsBox = document.getElementById("nights");
  const filter = document.getElementById("camera-filter");
  const tagFilter = document.getElementById("tag-filter");
  const TAG_LABELS = { mouse: "Souris", other: "Autre animal", false: "Fausse alerte" };
  const moreBtn = document.getElementById("more");
  const dialog = document.getElementById("viewer");
  const video = document.getElementById("viewer-video");
  const photo = document.getElementById("viewer-img");
  const switcher = document.getElementById("viewer-switch");
  let events = [];
  let current = null;

  function card(ev) {
    const fig = document.createElement("figure");
    fig.className = "thumb";
    fig.id = ev.id;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-label", `Ouvrir la photo de ${RW.fmtTime(ev.start)}`);
    const media = document.createElement("span");
    media.className = "media";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `/api/events/${ev.id}/thumb`;
    img.alt = "";
    media.append(img);
    if (ev.tag) {
      const tag = document.createElement("span");
      tag.className = "tag-badge";
      tag.textContent = TAG_LABELS[ev.tag];
      media.append(tag);
    }
    if (ev.clip_state === "ok") {
      const badge = document.createElement("span");
      badge.className = "clip-badge";
      badge.textContent = "▶ vidéo";
      media.append(badge);
    }
    btn.append(media);
    btn.addEventListener("click", () => open(ev));
    const cap = document.createElement("figcaption");
    const a = document.createElement("span");
    a.textContent = filter.value ? RW.fmtTime(ev.start) : `${ev.camera_name} · ${RW.fmtTime(ev.start)}`;
    const b = document.createElement("span");
    b.className = "muted";
    b.textContent = `${RW.fmtNumber(ev.duration)} s`;
    cap.append(a, b);
    fig.append(btn, cap);
    return fig;
  }

  function render() {
    const groups = new Map();
    for (const ev of events) {
      const key = RW.nightKey(ev.start);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(ev);
    }
    nightsBox.replaceChildren(...[...groups].map(([key, list]) => {
      const section = document.createElement("section");
      section.className = "stack";
      section.style.gap = ".6rem";
      const head = document.createElement("div");
      head.className = "row";
      const h2 = document.createElement("h2");
      h2.textContent = RW.nightLabel(key);
      const count = document.createElement("span");
      count.className = "muted small";
      count.textContent = `${list.length} passage${list.length > 1 ? "s" : ""}`;
      head.append(h2, count);
      const grid = document.createElement("div");
      grid.className = "thumbs";
      grid.append(...list.map(card));
      section.append(head, grid);
      return section;
    }));
    document.getElementById("empty").hidden = events.length > 0;
  }

  async function load(reset = true) {
    const params = new URLSearchParams({ limit: PAGE });
    if (filter.value) params.set("camera", filter.value);
    if (tagFilter.value) params.set("tag", tagFilter.value);
    if (!reset && events.length) params.set("before", events[events.length - 1].start);
    const batch = await RW.api(`/api/events?${params}`);
    events = reset ? batch : events.concat(batch);
    moreBtn.hidden = batch.length < PAGE;
    render();
    // Extraits encore en préparation : rafraîchir la liste un peu plus tard
    clearTimeout(refreshTimer);
    if (events.some(ev => ev.clip_state === "pending")) {
      refreshTimer = setTimeout(() => { if (!dialog.open) load(); }, 8000);
    }
  }
  let refreshTimer = null;

  function show(which) {
    const hasClip = current?.clip_state === "ok";
    which = hasClip ? which : "photo";
    video.hidden = which !== "video";
    photo.hidden = which !== "photo";
    if (which === "video") video.play().catch(() => {}); else video.pause();
    switcher.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.show === which)));
  }

  function open(ev) {
    current = ev;
    const d = new Date(ev.start * 1000);
    const hasClip = ev.clip_state === "ok";
    photo.src = `/api/events/${ev.id}/photo`;
    photo.alt = `Photo du passage, ${ev.camera_name}`;
    video.removeAttribute("src");
    if (hasClip) video.src = `/api/events/${ev.id}/clip`;
    video.load();
    switcher.hidden = !hasClip;
    const dl = document.getElementById("viewer-download-clip");
    dl.hidden = !hasClip;
    dl.href = `/api/events/${ev.id}/clip?download=1`;
    const clipNote = { pending: " · extrait vidéo en préparation", failed: " · vidéo indisponible" }[ev.clip_state] || "";
    document.getElementById("viewer-title").textContent =
      `${ev.camera_name} · ${d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" })} à ${RW.fmtTime(ev.start)}`;
    document.getElementById("viewer-info").textContent =
      `Durée ${RW.fmtNumber(ev.duration)} s · taille ${RW.fmtNumber(ev.size)} % de l'image` +
      clipNote + (ev.location === "pending" ? " · en attente de copie sur le NAS" : "");
    document.getElementById("viewer-download").href = `/api/events/${ev.id}/photo?download=1`;
    showTag();
    const i = events.indexOf(ev);
    document.getElementById("viewer-prev").disabled = i <= 0;
    document.getElementById("viewer-next").disabled = i < 0 || i >= events.length - 1;
    if (!dialog.open) dialog.showModal();
    show(hasClip ? "video" : "photo");
  }

  /* ---------- Marquage : souris, autre animal, fausse alerte ---------- */
  const tagButtons = document.querySelectorAll("#viewer-tags button");
  function showTag() {
    tagButtons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tag === (current?.tag || ""))));
  }
  async function setTag(tag) {
    if (!current) return;
    try {
      await RW.api(`/api/events/${current.id}/tag`, { method: "PUT", body: { tag } });
      current.tag = tag;
      showTag();
      render();
    } catch (err) {
      alert(`Marquage impossible : ${err.message}`);
    }
  }
  tagButtons.forEach(b => b.addEventListener("click", () => setTag(b.dataset.tag)));

  function step(delta) {
    const i = events.indexOf(current) + delta;
    if (current && i >= 0 && i < events.length) open(events[i]);
  }
  document.getElementById("viewer-prev").addEventListener("click", () => step(-1));
  document.getElementById("viewer-next").addEventListener("click", () => step(1));
  // Clavier : flèches pour passer d'un passage à l'autre, 1/2/3/0 pour marquer
  dialog.addEventListener("keydown", e => {
    if (e.target.closest("video")) return;
    const tags = { "1": "mouse", "2": "other", "3": "false", "0": "" };
    if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
    else if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
    else if (e.key in tags) { setTag(tags[e.key]); e.preventDefault(); }
  });

  switcher.querySelectorAll("button").forEach(b => b.addEventListener("click", () => show(b.dataset.show)));
  dialog.addEventListener("close", () => { video.pause(); });

  document.getElementById("viewer-close").addEventListener("click", () => dialog.close());
  document.getElementById("viewer-delete").addEventListener("click", async () => {
    if (!current || !confirm("Supprimer définitivement cette photo ?")) return;
    try {
      await RW.api(`/api/events/${current.id}`, { method: "DELETE" });
      events = events.filter(ev => ev.id !== current.id);
      dialog.close();
      render();
    } catch (err) {
      alert(`Suppression impossible : ${err.message}`);
    }
  });
  filter.addEventListener("change", () => load());
  tagFilter.addEventListener("change", () => load());

  /* ---------- Export des données ---------- */
  const exportDialog = document.getElementById("export-dialog");
  const $ex = id => document.getElementById(id);
  const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  function exportUrl() {
    const params = new URLSearchParams();
    const period = $ex("ex-period").value;
    $ex("ex-dates").hidden = period !== "custom";
    if (period === "custom") {
      if ($ex("ex-from").value) params.set("depuis", $ex("ex-from").value);
      if ($ex("ex-to").value) params.set("jusqu", $ex("ex-to").value);
    } else if (period !== "all") {
      const d = new Date();
      d.setDate(d.getDate() - Number(period));
      params.set("depuis", isoDay(d));
    }
    if ($ex("ex-camera").value) params.set("camera", $ex("ex-camera").value);
    params.set("retenus", $ex("ex-keep").value);
    params.set("format", document.querySelector('input[name="ex-format"]:checked').value);
    $ex("ex-download").href = `/api/export?${params}`;
  }
  exportDialog.addEventListener("change", exportUrl);
  $ex("export-open").addEventListener("click", async () => {
    exportUrl();
    exportDialog.showModal();
    try {
      const st = (await RW.api("/api/status")).stats;
      $ex("ex-status").textContent =
        !st.site ? "Coordonnées du site absentes de config.yaml : les colonnes soleil, lune et météo resteront vides." :
        !st.weather.enabled ? "Météo désactivée dans config.yaml : les colonnes météo resteront vides." :
        st.weather.error ? `Attention, ${st.weather.error}` :
        st.weather.last ? `Météo à jour au ${new Date(st.weather.last * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })} (Open-Meteo).` :
        "Météo en cours de récupération.";
    } catch { $ex("ex-status").textContent = ""; }
  });
  $ex("ex-close").addEventListener("click", () => exportDialog.close());
  moreBtn.addEventListener("click", () => load(false));

  load().then(() => {
    const id = location.hash.slice(1);
    const ev = id && events.find(e => e.id === id);
    if (ev) open(ev);
  }).catch(err => {
    nightsBox.textContent = `Chargement impossible : ${err.message}`;
  });
})();
