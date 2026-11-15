/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Page Passages : photos classées par nuit. */
(() => {
  const PAGE = 200;
  const nightsBox = document.getElementById("nights");
  const filter = document.getElementById("camera-filter");
  const moreBtn = document.getElementById("more");
  const dialog = document.getElementById("viewer");
  const video = document.getElementById("viewer-video");
  const photo = document.getElementById("viewer-img");
  const switcher = document.getElementById("viewer-switch");
  const BATCH_MAX = 500;
  let events = [];
  let current = null;
  let selecting = false;
  const selected = new Set();
  let lastClicked = null;                  // pour la sélection d'une plage (Maj + clic)

  function card(ev) {
    const fig = document.createElement("figure");
    fig.className = "thumb";
    fig.id = ev.id;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-label", `Passage de ${RW.fmtTime(ev.start)}`);
    if (selecting) btn.setAttribute("aria-pressed", String(selected.has(ev.id)));
    if (selected.has(ev.id)) fig.classList.add("selected");
    const media = document.createElement("span");
    media.className = "media";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `/api/events/${ev.id}/thumb`;
    img.alt = "";
    media.append(img);
    if (ev.clip_state === "ok") {
      const badge = document.createElement("span");
      badge.className = "clip-badge";
      badge.textContent = "▶ vidéo";
      media.append(badge);
    }
    const check = document.createElement("span");
    check.className = "check";
    check.setAttribute("aria-hidden", "true");
    check.textContent = selected.has(ev.id) ? "✓" : "";
    media.append(check);
    btn.append(media);
    btn.addEventListener("click", e => (selecting ? toggle(ev, e.shiftKey) : open(ev)));
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
      const nightLabel = document.createElement("label");
      nightLabel.className = "night-check";
      const nightBox = document.createElement("input");
      nightBox.type = "checkbox";
      const inNight = list.filter(ev => selected.has(ev.id)).length;
      nightBox.checked = inNight === list.length;
      nightBox.indeterminate = inNight > 0 && inNight < list.length;
      nightBox.addEventListener("change", () => {
        list.forEach(ev => (nightBox.checked ? selected.add(ev.id) : selected.delete(ev.id)));
        refreshSelection();
      });
      nightLabel.append(nightBox, "Toute la nuit");
      head.append(h2, count, nightLabel);
      const grid = document.createElement("div");
      grid.className = "thumbs";
      grid.append(...list.map(card));
      section.append(head, grid);
      return section;
    }));
    document.getElementById("empty").hidden = events.length > 0;
    nightsBox.classList.toggle("selecting", selecting);
    updateBar();
  }

  /* ---------- Sélection et actions groupées ---------- */
  const bar = document.getElementById("selbar");
  const status = document.getElementById("sel-status");
  const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

  function toggle(ev, range) {
    if (range && lastClicked && lastClicked !== ev.id) {
      const a = events.findIndex(e => e.id === lastClicked), b = events.indexOf(ev);
      if (a >= 0) {
        const add = !selected.has(ev.id);
        events.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(e => (add ? selected.add(e.id) : selected.delete(e.id)));
      }
    } else if (selected.has(ev.id)) {
      selected.delete(ev.id);
    } else {
      selected.add(ev.id);
    }
    lastClicked = ev.id;
    refreshSelection();
  }

  function refreshSelection() {
    const y = window.scrollY;
    render();
    window.scrollTo(0, y);
  }

  function updateBar() {
    // Les passages qui ne sont plus affichés (filtre, suppression) quittent la sélection
    const shown = new Set(events.map(e => e.id));
    [...selected].forEach(id => { if (!shown.has(id)) selected.delete(id); });
    bar.hidden = !selecting;
    const n = selected.size;
    const withClip = events.filter(e => selected.has(e.id) && e.clip_state === "ok").length;
    document.getElementById("sel-count").textContent =
      n ? `${plural(n, "passage")} sélectionné${n > 1 ? "s" : ""}` + (withClip ? ` (dont ${withClip} avec vidéo)` : "")
        : "Aucun passage sélectionné";
    const tooMany = n > BATCH_MAX;
    document.getElementById("sel-download").disabled = !n || tooMany;
    document.getElementById("sel-delete").disabled = !n || tooMany;
    document.getElementById("sel-none").disabled = !n;
    if (tooMany) status.textContent = `Au plus ${BATCH_MAX} passages par action : réduisez la sélection.`;
  }

  function setSelecting(on) {
    selecting = on;
    if (!on) { selected.clear(); lastClicked = null; status.textContent = ""; }
    document.getElementById("select-toggle").setAttribute("aria-pressed", String(on));
    document.getElementById("select-toggle").textContent = on ? "Terminer la sélection" : "Sélectionner";
    refreshSelection();
  }

  function busy(on) {
    bar.querySelectorAll("button, select").forEach(el => { el.disabled = on; });
    if (!on) updateBar();
  }

  const fmtSize = bytes => bytes >= 1e9 ? `${RW.fmtNumber(bytes / 1e9)} Go` : `${RW.fmtNumber(bytes / 1e6)} Mo`;

  async function downloadSelection() {
    const ids = [...selected];
    const videos = document.getElementById("sel-content").value === "all";
    busy(true);
    status.textContent = `Préparation de l'archive (${plural(ids.length, "passage")})…`;
    try {
      const res = await RW.api("/api/events/archive", { method: "POST", body: { ids, videos } });
      const a = document.createElement("a");
      a.href = res.url;
      a.download = res.name;
      document.body.append(a); a.click(); a.remove();
      status.textContent = `Archive prête : ${plural(res.files, "fichier")}, ${fmtSize(res.size)}.` +
        (res.missing ? ` ${plural(res.missing, "fichier")} n'a pu être lu (liste dans l'archive).` : "");
    } catch (err) {
      status.textContent = `Téléchargement impossible : ${err.message}`;
    } finally {
      busy(false);
    }
  }

  async function deleteSelection() {
    const ids = [...selected];
    const withClip = events.filter(e => selected.has(e.id) && e.clip_state === "ok").length;
    const what = withClip ? "les photos et les vidéos" : "les photos";
    if (!confirm(`Supprimer définitivement ${what} de ${plural(ids.length, "passage")} ?\nCette action est irréversible.`)) return;
    busy(true);
    status.textContent = "Suppression en cours…";
    try {
      const res = await RW.api("/api/events/delete", { method: "POST", body: { ids } });
      const gone = new Set(res.deleted);
      events = events.filter(e => !gone.has(e.id));
      gone.forEach(id => selected.delete(id));
      status.textContent = `${plural(res.deleted.length, "passage")} supprimé${res.deleted.length > 1 ? "s" : ""}.` +
        (res.failed.length ? ` ${plural(res.failed.length, "échec")} : ${res.failed[0].error} Les passages concernés restent sélectionnés.` : "");
      refreshSelection();
    } catch (err) {
      status.textContent = `Suppression impossible : ${err.message}`;
    } finally {
      busy(false);
    }
  }

  document.getElementById("select-toggle").addEventListener("click", () => setSelecting(!selecting));
  document.getElementById("sel-done").addEventListener("click", () => setSelecting(false));
  document.getElementById("sel-all").addEventListener("click", () => {
    events.forEach(e => selected.add(e.id));
    refreshSelection();
  });
  document.getElementById("sel-none").addEventListener("click", () => { selected.clear(); refreshSelection(); });
  document.getElementById("sel-download").addEventListener("click", downloadSelection);
  document.getElementById("sel-delete").addEventListener("click", deleteSelection);
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && selecting && !dialog.open) setSelecting(false);
  });

  async function load(reset = true) {
    const params = new URLSearchParams({ limit: PAGE });
    if (filter.value) params.set("camera", filter.value);
    if (!reset && events.length) params.set("before", events[events.length - 1].start);
    const batch = await RW.api(`/api/events?${params}`);
    events = reset ? batch : events.concat(batch);
    moreBtn.hidden = batch.length < PAGE;
    render();
    // Extraits encore en préparation : rafraîchir la liste un peu plus tard
    clearTimeout(refreshTimer);
    if (events.some(ev => ev.clip_state === "pending")) {
      refreshTimer = setTimeout(() => { if (!dialog.open && !selecting) load(); }, 8000);
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
    dialog.showModal();
    show(hasClip ? "video" : "photo");
  }

  switcher.querySelectorAll("button").forEach(b => b.addEventListener("click", () => show(b.dataset.show)));
  dialog.addEventListener("close", () => { video.pause(); });

  document.getElementById("viewer-close").addEventListener("click", () => dialog.close());
  document.getElementById("viewer-delete").addEventListener("click", async () => {
    if (!current || !confirm("Supprimer définitivement cette photo ?")) return;
    try {
      await RW.api(`/api/events/${current.id}`, { method: "DELETE" });
      events = events.filter(ev => ev.id !== current.id);
      selected.delete(current.id);
      dialog.close();
      render();
    } catch (err) {
      alert(`Suppression impossible : ${err.message}`);
    }
  });
  filter.addEventListener("change", () => load());
  moreBtn.addEventListener("click", () => load(false));

  load().then(() => {
    const id = location.hash.slice(1);
    const ev = id && events.find(e => e.id === id);
    if (ev) open(ev);
  }).catch(err => {
    nightsBox.textContent = `Chargement impossible : ${err.message}`;
  });
})();
