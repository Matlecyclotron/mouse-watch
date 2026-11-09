/* SPDX-License-Identifier: GPL-3.0-or-later
   Copyright (C) 2026 Les contributeurs de rtsp-web */
/* Lecteur en direct : WebRTC (WHEP) vers MediaMTX, repli HLS après deux échecs. */
(() => {
  const origin = port => `${location.protocol}//${location.hostname}:${port}`;

  function iceGathered(pc) {
    return new Promise(resolve => {
      if (pc.iceGatheringState === "complete") return resolve();
      const timer = setTimeout(resolve, 1500);
      pc.addEventListener("icegatheringstatechange", () => {
        if (pc.iceGatheringState === "complete") { clearTimeout(timer); resolve(); }
      });
    });
  }

  class LivePlayer {
    constructor(video, cameraId, { onPlaying, onStop } = {}) {
      this.video = video;
      this.id = cameraId;
      this.ports = window.RTSPWEB.player;
      this.onPlaying = onPlaying || (() => {});
      this.onStop = onStop || (() => {});
      this.pc = this.hls = this.session = null;
      this.active = false;
      this.webrtcFails = 0;
      video.addEventListener("playing", () => this.onPlaying());
    }

    async connect() {
      if (this.active) return;
      this.active = true;
      if (this.webrtcFails < 2 && "RTCPeerConnection" in window) {
        try { await this.startWebRTC(); }
        catch (err) { console.warn(this.id, "WebRTC :", err); this.webrtcFails++; this.disconnect(); }
      } else {
        this.startHLS();
      }
    }

    async startWebRTC() {
      const pc = new RTCPeerConnection();
      this.pc = pc;
      const stream = new MediaStream();
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });
      pc.ontrack = e => { stream.addTrack(e.track); this.video.srcObject = stream; };
      pc.onconnectionstatechange = () => {
        const s = pc.connectionState;
        if (s === "connected") this.webrtcFails = 0;
        else if (s === "failed") { this.webrtcFails++; this.disconnect(); }
        else if (s === "disconnected") setTimeout(() => {
          if (this.pc === pc && pc.connectionState === "disconnected") this.disconnect();
        }, 4000);
      };
      await pc.setLocalDescription(await pc.createOffer());
      await iceGathered(pc);
      const url = `${origin(this.ports.webrtcPort)}/${this.id}/whep`;
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/sdp" },
                                     body: pc.localDescription.sdp });
      if (res.status !== 201) throw new Error(`réponse ${res.status}`);
      const loc = res.headers.get("Location");
      this.session = loc ? new URL(loc, url).href : null;
      await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });
      this.video.play().catch(() => {});
    }

    startHLS() {
      const src = `${origin(this.ports.hlsPort)}/${this.id}/index.m3u8`;
      if (typeof Hls !== "undefined" && Hls.isSupported()) {
        const hls = new Hls({ lowLatencyMode: true, backBufferLength: 10 });
        hls.on(Hls.Events.ERROR, (_e, d) => {
          if (!d.fatal) return;
          if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
          else { this.webrtcFails = 0; this.disconnect(); }
        });
        hls.loadSource(src);
        hls.attachMedia(this.video);
        this.hls = hls;
      } else if (this.video.canPlayType("application/vnd.apple.mpegurl")) {
        this.video.src = src;
      }
      this.video.play().catch(() => {});
    }

    disconnect() {
      if (this.session) fetch(this.session, { method: "DELETE" }).catch(() => {});
      if (this.pc) { this.pc.onconnectionstatechange = null; this.pc.close(); }
      if (this.hls) this.hls.destroy();
      this.pc = this.hls = this.session = null;
      this.video.srcObject = null;
      this.video.removeAttribute("src");
      this.video.load();
      this.active = false;
      this.onStop();
    }

    // Suit l'état de la caméra renvoyé par /api/status
    follow(cameraState) {
      if (cameraState === "online") this.connect();
      else if (this.active) this.disconnect();
    }
  }

  window.LivePlayer = LivePlayer;
  window.addEventListener("pagehide", () => document.dispatchEvent(new Event("rtspweb:leave")));
})();
