/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Fonctions communes à toutes les pages de rtsp-web. */
(() => {
  const NAS_LABELS = {
    ok: ["ok", "NAS disponible"],
    checking: ["warn", "Vérification du NAS…"],
    missing: ["", "NAS non monté"],
    unreachable: ["", "NAS injoignable"],
    denied: ["", "NAS : écriture refusée"],
    disabled: ["warn", "Stockage local uniquement"],
  };

  async function api(path, { method = "GET", body } = {}) {
    const opts = { method, headers: {} };
    if (method !== "GET") opts.headers["X-Requested-With"] = "rtsp-web";
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const data = res.headers.get("Content-Type")?.includes("json") ? await res.json() : null;
    if (!res.ok) throw new Error(data?.error || `Erreur ${res.status}`);
    return data;
  }

  const pad = n => String(n).padStart(2, "0");
  const fmtTime = ts => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  const fmtNumber = (n, digits = 1) => n.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });

  // Une « nuit » va de midi à midi : un passage à 2 h appartient à la nuit de la veille.
  function nightKey(ts) {
    const d = new Date(ts * 1000);
    if (d.getHours() < 12) d.setDate(d.getDate() - 1);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function nightLabel(key) {
    const [y, m, day] = key.split("-").map(Number);
    const a = new Date(y, m - 1, day), b = new Date(y, m - 1, day + 1);
    const f = d => d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric" });
    const month = d => d.toLocaleDateString("fr-FR", { month: "long" });
    const first = a.getMonth() === b.getMonth() ? f(a) : `${f(a)} ${month(a)}`;
    return `Nuit du ${first} au ${f(b)} ${month(b)} ${b.getFullYear()}`;
  }

  function updateHeader(status) {
    const pill = document.getElementById("nas-pill");
    if (pill) {
      const st = status.storage;
      const [cls, label] = NAS_LABELS[st.state] || ["", st.state];
      pill.querySelector(".dot").className = `dot ${cls}`;
      pill.querySelector(".txt").textContent =
        st.pending ? `${label} · ${st.pending} en attente` : label;
      pill.title = st.message || "";
    }
    const summary = document.getElementById("summary");
    if (summary) {
      const online = status.cameras.filter(c => c.state === "online").length;
      summary.textContent = `${online} caméra${online > 1 ? "s" : ""} en ligne sur ${status.cameras.length}`;
    }
  }

  async function poll() {
    try {
      const status = await api("/api/status");
      updateHeader(status);
      document.dispatchEvent(new CustomEvent("rtspweb:status", { detail: status }));
    } catch {
      const summary = document.getElementById("summary");
      if (summary) summary.textContent = "Serveur injoignable, nouvel essai…";
    }
  }

  window.RW = { api, fmtTime, fmtNumber, nightKey, nightLabel, poll };
  document.addEventListener("DOMContentLoaded", () => { poll(); setInterval(poll, 4000); });
})();
