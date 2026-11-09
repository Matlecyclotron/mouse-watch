/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Page Passages : photos classées par nuit. */
(() => {
  const PAGE = 200;
  const nightsBox = document.getElementById("nights");
  const filter = document.getElementById("camera-filter");
  const moreBtn = document.getElementById("more");
  const dialog = document.getElementById("viewer");
  let events = [];
  let current = null;

  function card(ev) {
    const fig = document.createElement("figure");
    fig.className = "thumb";
    fig.id = ev.id;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-label", `Ouvrir la photo de ${RW.fmtTime(ev.start)}`);
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `/api/events/${ev.id}/thumb`;
    img.alt = "";
    btn.append(img);
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
    if (!reset && events.length) params.set("before", events[events.length - 1].start);
    const batch = await RW.api(`/api/events?${params}`);
    events = reset ? batch : events.concat(batch);
    moreBtn.hidden = batch.length < PAGE;
    render();
  }

  function open(ev) {
    current = ev;
    const d = new Date(ev.start * 1000);
    document.getElementById("viewer-img").src = `/api/events/${ev.id}/photo`;
    document.getElementById("viewer-img").alt = `Photo du passage, ${ev.camera_name}`;
    document.getElementById("viewer-title").textContent =
      `${ev.camera_name} · ${d.toLocaleDateString("fr-FR", { day: "numeric", month: "long" })} à ${RW.fmtTime(ev.start)}`;
    document.getElementById("viewer-info").textContent =
      `Durée ${RW.fmtNumber(ev.duration)} s · taille ${RW.fmtNumber(ev.size)} % de l'image` +
      (ev.location === "pending" ? " · en attente de copie sur le NAS" : "");
    document.getElementById("viewer-download").href = `/api/events/${ev.id}/photo?download=1`;
    dialog.showModal();
  }

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
  moreBtn.addEventListener("click", () => load(false));

  load().then(() => {
    const id = location.hash.slice(1);
    const ev = id && events.find(e => e.id === id);
    if (ev) open(ev);
  }).catch(err => {
    nightsBox.textContent = `Chargement impossible : ${err.message}`;
  });
})();
