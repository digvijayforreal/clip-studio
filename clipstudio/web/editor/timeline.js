/* The timeline: a ruler, the playhead and one lane per track. Everything can be dragged:
   the middle of an item moves it, its edges trim it. Ctrl + wheel zooms, Shift + wheel scrolls. */
import { clock, h } from "../lib.js";
import * as M from "./model.js";

export const TRACKS = [
  { id: "video", label: "Video", height: 70 },
  { id: "captions", label: "Captions", height: 32 },
  { id: "text", label: "Text", height: 32 },
  { id: "stickers", label: "Stickers", height: 32 },
  { id: "zooms", label: "Zooms", height: 24 },
  { id: "music", label: "Music", height: 26 },
];
const RULER = 26;
const STRIP_H = 46;
const SNAP_PX = 8;
const TICKS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

export class Timeline {
  constructor(ed) {
    this.ed = ed;
    this.pps = 60;                    // pixels per second
    this.fitted = false;
    this.lanes = {};
    this.ruler = h("canvas", { class: "tl-ruler", height: RULER, "aria-hidden": "true" });
    this.playhead = h("div", { class: "tl-playhead", "aria-hidden": "true" }, h("div", { class: "tl-knob" }));
    this.guide = h("div", { class: "tl-guide", hidden: true, "aria-hidden": "true" });
    this.content = h("div", { class: "tl-content" }, this.ruler,
      TRACKS.map((t) => (this.lanes[t.id] = h("div", { class: "tl-lane", "data-track": t.id, style: { height: t.height + "px" } }))),
      this.playhead, this.guide);
    this.scroll = h("div", { class: "tl-scroll" }, this.content);
    this.labels = h("div", { class: "tl-labels" }, h("div", { class: "tl-label", style: { height: RULER + "px" } }),
      TRACKS.map((t) => h("div", { class: "tl-label", style: { height: t.height + "px" } }, t.label)));
    this.el = h("div", { class: "tl", role: "region", "aria-label": "Timeline. Use the arrow keys to move the playhead, S to split." },
      this.labels, this.scroll);

    this.ruler.addEventListener("pointerdown", (e) => this.scrub(e));
    this.playhead.addEventListener("pointerdown", (e) => this.scrub(e));
    this.content.addEventListener("pointerdown", (e) => {
      if (e.target.classList.contains("tl-lane")) { this.ed.select(null); this.scrub(e); }
    });
    this.scroll.addEventListener("wheel", (e) => {
      if (e.ctrlKey) { e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1.25 : 0.8, e.clientX); }
      else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) { /* the browser scrolls sideways */ }
    }, { passive: false });
    this.resize = new ResizeObserver(() => { if (!this.fitted) this.fit(); else this.render(); });
    this.resize.observe(this.scroll);
  }

  destroy() { this.resize.disconnect(); }

  // ---------------------------------------------------------------- geometry
  x(T) { return T * this.pps; }
  timeAt(clientX) { return Math.max(0, (clientX - this.content.getBoundingClientRect().left) / this.pps); }

  fit() {
    const width = this.scroll.clientWidth - 30;
    if (width <= 0) return;
    this.pps = M.clamp(width / Math.max(1, this.ed.total()), 8, 400);
    this.fitted = true;
    this.render();
  }

  zoomBy(factor, clientX) {
    const anchor = clientX ?? this.scroll.getBoundingClientRect().left + this.scroll.clientWidth / 2;
    const T = this.timeAt(anchor);
    const offset = anchor - this.scroll.getBoundingClientRect().left;
    this.pps = M.clamp(this.pps * factor, 8, 400);
    this.render();
    this.scroll.scrollLeft = Math.max(0, this.x(T) - offset);
  }

  // ---------------------------------------------------------------- drawing
  render() {
    const ed = this.ed;
    const total = ed.total();
    const width = Math.max(this.scroll.clientWidth, this.x(total) + 200);
    this.content.style.width = width + "px";
    this.drawRuler(width);
    const sel = ed.selection;
    const isSel = (kind, id) => sel && sel.kind === kind && sel.id === id;
    for (const lane of Object.values(this.lanes)) lane.replaceChildren();

    // video
    for (const c of ed.clipList()) {
      const muted = (c.seg.volume ?? 1) === 0;
      const el = this.item("clip", c.id, c.start, c.end, isSel("clip", c.id), "clip",
        h("canvas", { class: "clip-strip", height: this.lanes.video.clientHeight || TRACKS[0].height }),
        h("span", { class: "clip-label" }, `${clock(c.length)}${muted ? "  🔇" : ""}`));
      el.title = `Clip ${c.index + 1}: ${clock(c.in)} – ${clock(c.out)} of the video`;
      this.lanes.video.append(el);
      this.drawClip(el, c);
    }
    // captions
    if (ed.p.captions.enabled) {
      for (const line of ed.lines()) {
        const el = this.item("line", line.key, line.start, line.shownEnd, isSel("line", line.key), "line",
          h("span", null, line.words.map((w) => w.t).join(" ")));
        this.lanes.captions.append(el);
      }
    }
    // text, with the hook first
    const hook = ed.p.hook;
    if ((hook.text || "").trim()) {
      const end = hook.seconds ? Math.min(total, hook.seconds) : total;
      this.lanes.text.append(this.item("hook", "hook", 0, end, isSel("hook", "hook"), "hook", h("span", null, `HOOK · ${hook.text}`)));
    }
    for (const t of ed.p.texts) {
      this.lanes.text.append(this.item("text", t.id, t.start, t.end, isSel("text", t.id), "text", h("span", null, t.text || "Text")));
    }
    for (const e of ed.p.effects.emojis) {
      this.lanes.stickers.append(this.item("sticker", e.id, e.t, e.t + (e.seconds ?? 1.4), isSel("sticker", e.id), "sticker",
        h("span", null, e.emoji)));
    }
    for (const z of ed.p.effects.zooms) {
      this.lanes.zooms.append(this.item("zoom", z.id, z.start, z.end, isSel("zoom", z.id), "zoom", h("span", null, "ZOOM")));
    }
    if (ed.p.music.file) {
      const el = this.item("music", "music", 0, total, isSel("music", "music"), "music",
        h("span", null, `♪ ${ed.p.music.file.replace(/^music_/, "")} · ${Math.round(ed.p.music.volume * 100)}%`));
      el.querySelectorAll(".h").forEach((x) => x.remove());
      this.lanes.music.append(el);
    }
    this.placePlayhead();
  }

  item(kind, id, start, end, selected, cls, ...kids) {
    const el = h("div", {
      class: `tl-item ${cls}${selected ? " sel" : ""}`, "data-kind": kind, "data-id": id, tabindex: 0, role: "button",
      "aria-pressed": selected ? "true" : "false",
      style: { left: this.x(start) + "px", width: Math.max(4, this.x(end) - this.x(start)) + "px" },
    }, h("div", { class: "h h-l", "aria-hidden": "true" }), ...kids, h("div", { class: "h h-r", "aria-hidden": "true" }));
    if (kind === "hook") el.querySelector(".h-l").remove();
    el.setAttribute("aria-label", `${kind} ${clock(start)} to ${clock(end)}`);
    el.addEventListener("pointerdown", (e) => this.grab(e, el, kind, id));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); this.ed.select({ kind, id }); } });
    return el;
  }

  drawRuler(width) {
    const dpr = window.devicePixelRatio || 1;
    const cv = this.ruler;
    cv.width = Math.round(width * dpr);
    cv.height = Math.round(RULER * dpr);
    cv.style.width = width + "px";
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, RULER);
    const step = TICKS.find((s) => s * this.pps >= 70) || 600;
    const minor = step / 5;
    ctx.fillStyle = "rgba(255,255,255,.35)";
    for (let t = 0; this.x(t) < width; t += minor) ctx.fillRect(Math.round(this.x(t)), RULER - 6, 1, 6);
    ctx.fillStyle = "#e8dcf5";
    ctx.font = "11px Segoe UI, sans-serif";
    for (let t = 0; this.x(t) < width; t += step) {
      const x = Math.round(this.x(t));
      ctx.fillRect(x, RULER - 12, 1, 12);
      ctx.fillText(clock(t, step < 1 ? 1 : 0), x + 3, 11);
    }
    const end = this.x(this.ed.total());
    ctx.fillStyle = "#ff2e88";
    ctx.fillRect(Math.round(end), 0, 2, RULER);
  }

  drawClip(el, c) {
    const cv = el.querySelector("canvas");
    const w = parseFloat(el.style.width);
    const hgt = TRACKS[0].height - 4;
    const scale = Math.min(1, 8000 / Math.max(1, w));          // very long clips: draw at lower detail
    cv.width = Math.max(1, Math.round(w * scale));
    cv.height = hgt;
    const ctx = cv.getContext("2d");
    ctx.setTransform(scale, 0, 0, 1, 0, 0);
    const a = this.ed.assets;
    if (a.strip && a.stripImage && a.stripImage.complete && a.stripImage.naturalWidth) {
      const s = a.strip;
      const tw = (s.w * STRIP_H) / s.h;
      for (let x = 0; x < w; x += tw) {
        const t = c.in + (x + tw / 2) / this.pps - a.offset;
        const i = M.clamp(Math.floor(t / s.interval), 0, s.count - 1);
        ctx.drawImage(a.stripImage, i * s.w, 0, s.w, s.h, x, 0, tw, STRIP_H);
      }
    } else {
      ctx.fillStyle = "rgba(255,255,255,.07)";
      ctx.fillRect(0, 0, w, STRIP_H);
    }
    ctx.fillStyle = "rgba(0,0,0,.55)";
    ctx.fillRect(0, STRIP_H, w, hgt - STRIP_H);
    if (a.peaks && a.peaks.length) {
      const mid = STRIP_H + (hgt - STRIP_H) / 2;
      const amp = (hgt - STRIP_H) / 2 - 1;
      const vol = Math.min(1, c.seg.volume ?? 1);
      ctx.fillStyle = vol === 0 ? "rgba(255,255,255,.2)" : "#19e3c9";
      for (let x = 0; x < w; x += 2) {
        const i = Math.floor((c.in + x / this.pps - a.offset) * a.rate);
        const v = (a.peaks[i] || 0) * Math.max(0.15, vol);
        ctx.fillRect(x, mid - v * amp, 1.5, Math.max(1, 2 * v * amp));
      }
    }
  }

  placePlayhead() {
    this.playhead.style.left = this.x(this.ed.T) + "px";
  }

  /** Keeps the playhead in view while playing. */
  follow() {
    const x = this.x(this.ed.T);
    const left = this.scroll.scrollLeft;
    const right = left + this.scroll.clientWidth;
    if (x < left || x > right - 40) this.scroll.scrollLeft = Math.max(0, x - 80);
  }

  // ---------------------------------------------------------------- scrubbing
  scrub(e) {
    e.preventDefault();
    const ed = this.ed;
    const wasPlaying = ed.preview.playing;
    if (wasPlaying) ed.preview.pause();
    const move = (ev) => ed.seek(Math.min(ed.total(), this.timeAt(ev.clientX)));
    move(e);
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  // ---------------------------------------------------------------- dragging items
  snapPoints(exceptId) {
    const ed = this.ed;
    const pts = [0, ed.total(), ed.T];
    for (const c of ed.clipList()) pts.push(c.start, c.end);
    for (const t of ed.p.texts) if (t.id !== exceptId) pts.push(t.start, t.end);
    for (const z of ed.p.effects.zooms) if (z.id !== exceptId) pts.push(z.start, z.end);
    for (const e of ed.p.effects.emojis) if (e.id !== exceptId) pts.push(e.t);
    return pts;
  }

  grab(e, el, kind, id) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const ed = this.ed;
    ed.select({ kind, id });
    if (kind === "music") return;
    const mode = e.target.classList.contains("h-l") ? "left" : e.target.classList.contains("h-r") ? "right" : "move";
    const x0 = e.clientX;
    const snap = ed.snapping ? this.snapPoints(id) : [];
    const threshold = SNAP_PX / this.pps;
    const fps = ed.fps;
    const snapT = (t) => {
      const v = ed.snapping ? M.snapTo(t, snap, threshold) : t;
      this.showGuide(v !== t ? v : null);
      return v;
    };
    let moved = false;
    let frame = 0;
    const p = ed.p;
    // what the item was before the drag, so each move starts from there
    const clip = kind === "clip" ? ed.clipList().find((c) => c.id === id) : null;
    const seg = clip ? clip.seg : null;
    const orig = seg ? { start: seg.start, end: seg.end }
      : kind === "line" ? ed.lines().find((l) => l.key === id)
      : kind === "text" ? { ...p.texts.find((t) => t.id === id) }
      : kind === "sticker" ? { ...p.effects.emojis.find((x) => x.id === id) }
      : kind === "zoom" ? { ...p.effects.zooms.find((x) => x.id === id) }
      : kind === "hook" ? { seconds: p.hook.seconds } : null;
    if (!orig) return;
    const lineOrig = {};
    if (kind === "line") for (const w of orig.words) lineOrig[w.i] = { s: p.captions.words[w.i].s, e: p.captions.words[w.i].e };
    ed.beginGesture();

    const apply = (dt) => {
      if (kind === "clip") {
        if (mode === "move") { el.style.transform = `translateX(${dt * this.pps}px)`; el.classList.add("dragging"); return false; }
        if (mode === "left") M.trimIn(seg, orig.start + dt, fps);
        else {
          const endT = snapT(clip.start + (orig.end + dt - orig.start));
          M.trimOut(seg, orig.start + (endT - clip.start), fps, ed.sourceDuration);
        }
      } else if (kind === "line") {
        const words = p.captions.words;
        const first = orig.words[0];
        const last = orig.words[orig.words.length - 1];
        if (mode === "move") {
          const startT = snapT(orig.start + dt);
          M.shiftLine(p, orig, lineOrig, startT - orig.start);
        } else if (mode === "left") {
          const o = lineOrig[first.i];
          words[first.i].s = M.r4(M.clamp(o.s + dt, first.clip.in, o.e - 0.05));
        } else {
          const o = lineOrig[last.i];
          words[last.i].e = M.r4(M.clamp(o.e + dt, o.s + 0.05, last.clip.out));
        }
      } else if (kind === "text" || kind === "zoom") {
        const item = kind === "text" ? p.texts.find((t) => t.id === id) : p.effects.zooms.find((z) => z.id === id);
        const len = orig.end - orig.start;
        if (mode === "move") {
          let s = Math.max(0, orig.start + dt);
          const sS = snapT(s);
          if (sS !== s) s = sS; else { const eS = snapT(s + len); s = Math.max(0, eS - len); }
          item.start = M.r4(s); item.end = M.r4(s + len);
        } else if (mode === "left") item.start = M.r4(M.clamp(snapT(orig.start + dt), 0, orig.end - 0.1));
        else item.end = M.r4(Math.max(orig.start + 0.1, snapT(orig.end + dt)));
      } else if (kind === "sticker") {
        const item = p.effects.emojis.find((x) => x.id === id);
        const len = orig.seconds ?? 1.4;
        if (mode === "move") item.t = M.r4(Math.max(0, snapT(orig.t + dt)));
        else if (mode === "left") {
          const t = M.clamp(snapT(orig.t + dt), 0, orig.t + len - 0.3);
          item.t = M.r4(t); item.seconds = M.r4(orig.t + len - t);
        } else item.seconds = M.r4(Math.max(0.3, snapT(orig.t + len + dt) - orig.t));
      } else if (kind === "hook") {
        if (mode !== "right") return false;
        const total = ed.total();
        const end = snapT((orig.seconds ?? total) + dt);
        p.hook.seconds = end >= total - 0.05 ? null : M.r4(Math.max(0.5, end));
      }
      return true;
    };

    const move = (ev) => {
      const dt = (ev.clientX - x0) / this.pps;
      if (!moved && Math.abs(ev.clientX - x0) < 3) return;
      moved = true;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (apply(dt)) { ed.touched(); this.render(); }
      });
    };
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      cancelAnimationFrame(frame);
      this.showGuide(null);
      if (moved && kind === "clip" && mode === "move") {
        const dt = (ev.clientX - x0) / this.pps;
        M.moveClip(p, id, M.dropIndex(p, id, clip.start + clip.length / 2 + dt));
      } else if (moved) apply((ev.clientX - x0) / this.pps);
      ed.endGesture();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  showGuide(T) {
    this.guide.hidden = T == null;
    if (T != null) this.guide.style.left = this.x(T) + "px";
  }
}
