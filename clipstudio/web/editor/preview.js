/* The preview: plays the video track clip after clip from the editing copy and paints it into the 9:16 frame
   the way the render will: the layout's regions (facecam, game, blurred fill) on one canvas, the template's
   graphics on another, then captions, hook, texts, stickers and the logo on top.

   The layout comes from the server (/frame), the same plan the renderer uses. Overlays are dragged to move
   them; the corner handle resizes them. Positions are shares of the 1080x1920 frame. */
import { clamp, emojiUrl, h } from "../lib.js";
import * as M from "./model.js";

const W = 1080, H = 1920;
const SCALE = 0.5;                 // the canvases are 540x960
const SNAP = 0.015;
const FONT_CSS = { "Poppins ExtraBold": '800 {size}px "Poppins Caption"', Anton: '{size}px Anton' };

export class Preview {
  constructor(ed) {
    this.ed = ed;
    this.T = 0;
    this.playing = false;
    this.clipId = null;
    this.offset = 0;
    this.pending = null;
    this.fp = null;
    this.video = h("video", { preload: "auto", playsinline: true, "aria-hidden": "true", class: "pv-video" });
    this.video.addEventListener("seeked", () => {
      if (this.pending != null) { const t = this.pending; this.pending = null; this.video.currentTime = t; }
    });
    this.music = new Audio();
    this.canvas = h("canvas", { class: "pv-canvas", width: W * SCALE, height: H * SCALE, role: "img", "aria-label": "Preview of the clip" });
    this.small = document.createElement("canvas");
    this.small.width = 27; this.small.height = 48;
    this.picture = h("div", { class: "pv-picture" }, this.video, this.canvas);
    this.gfx = h("canvas", { class: "pv-gfx", width: W * SCALE, height: H * SCALE, "aria-hidden": "true" });
    this.logo = h("img", { class: "pv-logo", alt: "", hidden: true, draggable: "false" });
    this.layer = h("div", { class: "pv-layer" });
    this.vGuide = h("div", { class: "pv-guide v", hidden: true });
    this.hGuide = h("div", { class: "pv-guide hz", hidden: true });
    this.safe = h("div", { class: "pv-safe", hidden: true, "aria-hidden": "true" },
      h("div", { class: "safe-top" }), h("div", { class: "safe-bottom" }, "Title and buttons area"), h("div", { class: "safe-right" }));
    this.frame = h("div", { class: "pv-frame" }, this.picture, this.gfx, this.logo, this.layer, this.vGuide, this.hGuide, this.safe);
    this.el = h("div", { class: "pv-wrap" }, this.frame);
    this.frame.addEventListener("pointerdown", (e) => this.down(e));
  }

  destroy() {
    this.pause();
    this.video.removeAttribute("src");
    this.video.load();
    this.music.removeAttribute("src");
  }

  // ---------------------------------------------------------------- media
  setVideo(url, offset) {
    if (this.url === url) return;
    this.url = url;
    this.offset = offset || 0;
    this.video.src = url;
    this.video.addEventListener("loadedmetadata", () => this.seek(this.T), { once: true });
  }

  setMusic(url) {
    if (this.musicUrl === url) return;
    this.musicUrl = url;
    if (url) this.music.src = url; else this.music.removeAttribute("src");
  }

  setLogo(url) {
    this.logoUrl = url;
    if (url) { if (this.logo.getAttribute("src") !== url) this.logo.src = url; } else this.logo.removeAttribute("src");
    this.key = null;
  }

  setPlan(fp) { this.fp = fp; this.gfxKey = null; this.key = null; }

  // ---------------------------------------------------------------- playback
  clip() { return this.ed.clipList().find((c) => c.id === this.clipId) || null; }

  enter(c, t) {
    this.clipId = c.id;
    const want = Math.max(0, t - this.offset);
    if (this.video.seeking) this.pending = want; else this.video.currentTime = want;
    const vol = c.seg.volume ?? 1;
    this.video.muted = vol === 0;
    this.video.volume = clamp(vol, 0, 1);
  }

  play() {
    const total = this.ed.total();
    if (this.T >= total - 0.05) this.T = 0;
    const at = M.toSource(this.ed.clipList(), this.T);
    if (!at) return;
    this.enter(at.clip, at.t);
    this.playing = true;
    this.video.play().catch(() => { this.playing = false; this.ed.onPlayState(); });
    const m = this.ed.p.music;
    if (this.musicUrl && m.file) {
      const d = this.music.duration;
      this.music.currentTime = Number.isFinite(d) && d > 0 ? this.T % d : this.T;
      this.music.volume = clamp(m.volume, 0, 1);
      this.music.play().catch(() => {});
    }
    this.ed.onPlayState();
  }

  pause() {
    this.playing = false;
    this.video.pause();
    this.music.pause();
    this.ed.onPlayState();
  }

  seek(T) {
    this.T = clamp(T, 0, this.ed.total());
    const at = M.toSource(this.ed.clipList(), this.T);
    if (!at) return;
    if (this.playing) { this.enter(at.clip, at.t); return; }
    this.clipId = at.clip.id;
    const want = Math.max(0, at.t - this.offset);
    if (this.video.seeking) this.pending = want; else if (this.video.readyState) this.video.currentTime = want;
  }

  /** Called every animation frame: moves the playhead along, jumps from clip to clip, paints the picture. */
  tick() {
    this.paint();
    if (!this.playing || this.video.seeking || this.pending != null) return;
    const c = this.clip();
    if (!c) { this.pause(); return; }
    const t = this.video.currentTime + this.offset;
    const list = this.ed.clipList();
    if (t >= c.out - 0.5 / this.ed.fps || this.video.ended) {
      const next = list[c.index + 1];
      if (next) { this.T = next.start; this.enter(next, next.in); }
      else { this.T = this.ed.total(); this.pause(); }
    } else this.T = c.start + Math.max(0, t - c.in);
  }

  // ---------------------------------------------------------------- the layout
  /** The plan, or a stand-in for the plain layouts until the server's answer arrives. */
  plan() {
    if (this.fp) return this.fp;
    const p = this.ed.p;
    const sw = p.source.width || 1920, sh = p.source.height || 1080;
    if (sh < sw && p.frame.layout !== "crop") {
      const band = Math.min(H, Math.round((W * sh) / sw / 2) * 2);
      const top = (H - band) / 2;
      return { background: { t: "blur" }, regions: [{ box: [0, 0, 1, 1], dest: [0, top, W, band], shape: "rect", border: 0 }],
               shapes: [], cap_y: Math.min(H * 0.75, top + band + 160), hook_y: Math.max(200, top - 190), hook: true,
               template: "none", credit_y: H - 60, ticker_speed: 160 };
    }
    const cw = Math.min(1, (sh * W) / H / sw);
    return { background: null, regions: [{ box: [(1 - cw) * (p.frame.x ?? 0.5), 0, cw, 1], dest: [0, 0, W, H], shape: "rect", border: 0 }],
             shapes: [], cap_y: H * 0.7, hook_y: 300, hook: true, template: "none", credit_y: H - 60, ticker_speed: 160 };
  }

  paint() {
    const v = this.video;
    const ctx = this.canvas.getContext("2d");
    const fp = this.plan();
    const ready = v.readyState >= 2 && v.videoWidth;
    const vw = v.videoWidth, vh = v.videoHeight;
    ctx.setTransform(SCALE, 0, 0, SCALE, 0, 0);
    const bg = fp.background;
    if (bg && bg.t === "color") { ctx.fillStyle = "#" + bg.color; ctx.fillRect(0, 0, W, H); }
    else if (bg && bg.t === "blur" && ready) {
      const s = this.small.getContext("2d");
      const a = Math.min(vw / 27, vh / 48);              // cover the small canvas, like the render's 270x480 crop
      s.drawImage(v, (vw - 27 * a) / 2, (vh - 48 * a) / 2, 27 * a, 48 * a, 0, 0, 27, 48);
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(this.small, 0, 0, W, H);
      ctx.fillStyle = "rgba(0,0,0,0.12)";
      ctx.fillRect(0, 0, W, H);
    } else { ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H); }
    if (!ready) return;
    for (const r of fp.regions) {
      const [bx, by, bw, bh] = r.box;
      const [dx, dy, dw, dh] = r.dest;
      const b = r.border || 0;
      ctx.save();
      if (r.shape === "circle") {
        if (b) { ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(dx + dw / 2, dy + dh / 2, dw / 2 + b, 0, Math.PI * 2); ctx.fill(); }
        ctx.beginPath(); ctx.arc(dx + dw / 2, dy + dh / 2, dw / 2, 0, Math.PI * 2); ctx.clip();
      } else if (r.shape === "round") {
        ctx.beginPath(); ctx.roundRect(dx, dy, dw, dh, r.radius || 0); ctx.clip();
      } else if (b) {
        ctx.fillStyle = "#fff"; ctx.fillRect(dx - b, dy - b, dw + 2 * b, dh + 2 * b);
      }
      ctx.drawImage(v, bx * vw, by * vh, bw * vw, bh * vh, dx, dy, dw, dh);
      ctx.restore();
    }
    this.paintGraphics(fp);
  }

  /** The template's drawings and the credit line. Only redrawn when they change, or every frame for a ticker. */
  paintGraphics(fp) {
    const p = this.ed.p;
    const ticker = fp.shapes.some((s) => s.t === "ticker");
    const key = `${this.ed.version}|${JSON.stringify(fp.shapes).length}|${p.credit.text}`;
    if (!ticker && key === this.gfxKey) return;
    this.gfxKey = key;
    const ctx = this.gfx.getContext("2d");
    ctx.setTransform(SCALE, 0, 0, SCALE, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const em = this.ed.meta.em_scale;
    const font = (name, size) => FONT_CSS[name in FONT_CSS ? name : "Poppins ExtraBold"].replace("{size}", (size * (em[name] || 0.57)).toFixed(1));
    for (const s of fp.shapes) {
      if (s.t === "rect") { ctx.fillStyle = "#" + s.color; ctx.beginPath(); ctx.roundRect(s.x, s.y, s.w, s.h, s.r || 0); ctx.fill(); }
      else if (s.t === "circle") { ctx.fillStyle = "#" + s.color; ctx.beginPath(); ctx.arc(s.cx, s.cy, s.r, 0, Math.PI * 2); ctx.fill(); }
      else if (s.t === "check") {
        const n = s.size, pts = [[0.2, 0.52], [0.42, 0.74], [0.8, 0.32], [0.7, 0.22], [0.42, 0.54], [0.3, 0.42]];
        ctx.fillStyle = "#" + s.color; ctx.beginPath();
        pts.forEach(([x, y], i) => ctx[i ? "lineTo" : "moveTo"](s.cx - n / 2 + x * n, s.cy - n / 2 + y * n));
        ctx.fill();
      } else if (s.t === "text") {
        ctx.font = font(s.font, s.size);
        ctx.fillStyle = "#" + s.color;
        ctx.textAlign = s.anchor === "c" ? "center" : "left";
        ctx.textBaseline = s.anchor === "lt" ? "top" : "middle";
        String(s.text).split("\n").forEach((line, k) => ctx.fillText(line, s.x, s.y + k * (s.line || 0)));
      } else if (s.t === "ticker") {
        const travel = s.x1 - s.x0 + s.width;
        const speed = fp.ticker_speed || 160;
        const x = s.x1 - ((this.T % (travel / speed)) * speed);
        ctx.save();
        ctx.beginPath(); ctx.rect(s.x0, s.y - s.h / 2, s.x1 - s.x0, s.h); ctx.clip();
        ctx.font = font(s.font, s.size); ctx.fillStyle = "#" + s.color; ctx.textAlign = "left"; ctx.textBaseline = "middle";
        ctx.fillText(s.text, x, s.y);
        ctx.restore();
      }
    }
    const credit = (p.credit.text || "").trim();
    if (credit) {
      ctx.font = font("Poppins ExtraBold", this.ed.meta.credit_size);
      ctx.textAlign = "center"; ctx.textBaseline = "bottom";
      ctx.lineWidth = 8; ctx.lineJoin = "round"; ctx.strokeStyle = "rgba(0,0,0,.8)"; ctx.fillStyle = "rgba(255,255,255,.82)";
      ctx.strokeText(credit, W / 2, fp.credit_y); ctx.fillText(credit, W / 2, fp.credit_y);
    }
  }

  /** Where an overlay is: {x, y} shares of the frame and its size (pixels of the 1080 frame, or a scale). */
  geom(kind, id) {
    const p = this.ed.p, fp = this.plan();
    if (kind === "captions") return { x: p.captions.x ?? 0.5, y: p.captions.y ?? fp.cap_y / H, size: p.captions.scale ?? 1 };
    if (kind === "hook") return { x: 0.5, y: p.hook.y ?? fp.hook_y / H, size: 1 };
    if (kind === "text") { const t = p.texts.find((x) => x.id === id); return t && { x: t.x, y: t.y, size: t.size }; }
    if (kind === "sticker") {
      const e = p.effects.emojis.find((x) => x.id === id);
      const size = this.ed.meta.emoji_size;
      return e && { x: e.x ?? 0.5, y: e.y ?? (Math.max(40, fp.cap_y - 120 - size) + size / 2) / H, size: e.size ?? size };
    }
    return null;
  }

  place(kind, id, x, y, size) {
    const p = this.ed.p;
    const r4 = M.r4;
    if (kind === "captions") { p.captions.x = r4(x); p.captions.y = r4(clamp(y, 0.03, 0.97)); if (size != null) p.captions.scale = r4(clamp(size, 0.4, 2.5)); }
    else if (kind === "hook") p.hook.y = r4(clamp(y, 0.03, 0.97));
    else if (kind === "text") { const t = p.texts.find((v) => v.id === id); t.x = r4(x); t.y = r4(y); if (size != null) t.size = Math.round(clamp(size, 20, 400)); }
    else if (kind === "sticker") { const e = p.effects.emojis.find((v) => v.id === id); e.x = r4(x); e.y = r4(y); if (size != null) e.size = Math.round(clamp(size, 40, 800)); }
  }

  placeLogo() {
    const lg = this.ed.p.logo;
    this.logo.hidden = !(lg.file && this.logoUrl);
    if (this.logo.hidden) return;
    const left = lg.corner === "tl" || lg.corner === "bl";
    const top = lg.corner === "tl" || lg.corner === "tr";
    Object.assign(this.logo.style, {
      width: `${lg.size * 100}%`, opacity: lg.opacity,
      left: left ? `${(40 / W) * 100}%` : "auto", right: left ? "auto" : `${(40 / W) * 100}%`,
      top: top ? `${(70 / H) * 100}%` : "auto", bottom: top ? "auto" : `${(140 / H) * 100}%`,
    });
  }

  /** Redraws what's on screen at the playhead. Cheap when nothing changed. */
  draw(force = false) {
    const ed = this.ed, p = ed.p, T = this.T;
    const fx = p.effects;
    const fp = this.plan();
    const zoom = fx.zooms.some((z) => T >= z.start && T < z.end);
    this.picture.style.transform = zoom ? `scale(${fx.zoom_scale})` : "";
    const line = p.captions.enabled ? ed.lines().find((l) => T >= l.start && T < l.shownEnd) : null;
    const active = line ? M.activeWord(line, T) : -1;
    const hookOn = fp.hook && (p.hook.text || "").trim() && (!p.hook.seconds || T < p.hook.seconds);
    const texts = p.texts.filter((t) => T >= t.start && T < t.end);
    const stickers = fx.emojis.filter((e) => T >= e.t && T < e.t + (e.seconds ?? 1.4));
    const sel = ed.selection ? `${ed.selection.kind}:${ed.selection.id}` : "";
    const key = [ed.version, sel, line ? line.key : "", active, hookOn ? 1 : 0, texts.map((t) => t.id).join(","),
      stickers.map((e) => e.id).join(","), Math.round((T / Math.max(0.01, ed.total())) * 400), this.fp ? 1 : 0].join("|");
    if (!force && key === this.key) return;
    this.key = key;
    this.placeLogo();
    const em = ed.meta.em_scale;
    const pop = em["Poppins ExtraBold"] || 0.57;

    const kids = [];
    if (fx.progress) kids.push(h("div", { class: "pv-bar", style: { width: `${(T / Math.max(0.01, ed.total())) * 100}%` } }));
    if (hookOn) {
      const lines = M.hookLines(p.hook.text, ed.meta.hook.max_units, ed.meta.hook.max_lines);
      const parts = [];
      lines.forEach((l, i) => { if (i) parts.push(h("br")); parts.push(l); });
      kids.push(this.overlay("hook", "hook", this.geom("hook"), h("div", { class: "ov-hook-in" }, h("span", null, parts)), false,
        { width: "96%", fontSize: `${((ed.meta.hook.size * pop) / W) * 100}cqw` }));
    }
    for (const t of texts) {
      const scale = em[t.font] || pop;
      const style = {
        fontFamily: t.font === "Anton" ? "Anton, Impact, sans-serif" : '"Poppins Caption", sans-serif',
        fontSize: `${((t.size * scale) / W) * 100}cqw`, color: "#" + t.color,
      };
      if (t.box) Object.assign(style, { background: "#" + t.back, padding: ".1em .3em" });
      else Object.assign(style, { WebkitTextStroke: `${((t.size / 7.5) / W) * 100}cqw #${t.back}` });
      kids.push(this.overlay("text", t.id, t, h("div", { class: "ov-text-in", style }, t.text), true));
    }
    for (const e of stickers) {
      const pos = this.geom("sticker", e.id);
      kids.push(this.overlay("sticker", e.id, pos,
        h("img", { src: emojiUrl(e.emoji), alt: "", draggable: "false", style: { width: `${(pos.size / W) * 100}cqw` } }), true));
    }
    if (line) {
      const st = ed.style();
      const pos = this.geom("captions");
      const words = line.words.map((w, i) => h("span", { style: { color: "#" + (i === active ? st.active : st.text) } }, w.t));
      const joined = [];
      words.forEach((s, i) => { if (i) joined.push(" "); joined.push(s); });
      kids.push(this.overlay("captions", "captions", pos, h("div", { class: "ov-cap-in" + (st.box ? " boxed" : ""),
        style: { fontSize: `${((M.lineSize(line, st, pos.size) * pop) / W) * 100}cqw` } }, joined), true));
    }
    this.layer.replaceChildren(...kids);
  }

  overlay(kind, id, pos, inner, resizable, extra = {}) {
    const sel = this.ed.selection;
    const selected = sel && sel.kind === kind && (sel.id === id || kind === "captions");
    const el = h("div", { class: `ov ov-${kind}${selected ? " sel" : ""}`, "data-kind": kind, "data-id": id,
      style: { left: `${pos.x * 100}%`, top: `${pos.y * 100}%`, ...extra } }, inner);
    if (selected && resizable) el.append(h("div", { class: "ov-resize", title: "Drag to resize" }));
    return el;
  }

  // ---------------------------------------------------------------- dragging overlays
  down(e) {
    const el = e.target.closest(".ov");
    if (!el) {
      if (this.frame.contains(e.target)) this.ed.select(null);
      return;
    }
    e.preventDefault();
    const kind = el.dataset.kind, id = el.dataset.id;
    this.ed.select({ kind, id: kind === "captions" ? "captions" : id });
    const start = this.geom(kind, id);
    if (!start) return;
    const rect = this.frame.getBoundingClientRect();
    const resize = e.target.classList.contains("ov-resize");
    const cx = rect.left + start.x * rect.width, cy = rect.top + start.y * rect.height;
    const d0 = Math.hypot(e.clientX - cx, e.clientY - cy) || 1;
    this.ed.beginGesture();
    const move = (ev) => {
      if (resize) {
        const f = Math.hypot(ev.clientX - cx, ev.clientY - cy) / d0;
        this.place(kind, id, start.x, start.y, start.size * f);
      } else {
        let x = clamp(start.x + (ev.clientX - e.clientX) / rect.width, 0.02, 0.98);
        let y = clamp(start.y + (ev.clientY - e.clientY) / rect.height, 0.03, 0.97);
        if (kind === "hook") x = 0.5;
        const snapX = Math.abs(x - 0.5) < SNAP, snapY = Math.abs(y - 0.5) < SNAP * 0.6;
        if (snapX) x = 0.5;
        if (snapY) y = 0.5;
        this.vGuide.hidden = !snapX;
        this.hGuide.hidden = !snapY;
        this.place(kind, id, x, y, null);
      }
      this.ed.touched();
      this.draw(true);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      this.vGuide.hidden = this.hGuide.hidden = true;
      this.ed.endGesture();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  /** Arrow keys nudge the selected overlay. */
  nudge(dx, dy) {
    const sel = this.ed.selection;
    if (!sel) return false;
    const pos = this.geom(sel.kind, sel.id);
    if (!pos) return false;
    this.ed.change(() => this.place(sel.kind, sel.id, clamp(pos.x + dx, 0.02, 0.98), clamp(pos.y + dy, 0.03, 0.97), null),
      { key: `nudge:${sel.kind}:${sel.id}` });
    return true;
  }
}
