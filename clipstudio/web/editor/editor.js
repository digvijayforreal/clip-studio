/* The ClipStudio editor: preview on the left, inspector on the right, timeline along the bottom.
   Changes are saved by themselves a moment after you make them; Render makes the final MP4. */
import { api, clock, h, state, toast } from "../lib.js";
import { inspector } from "./inspector.js";
import * as M from "./model.js";
import { Preview } from "./preview.js";
import { Timeline } from "./timeline.js";

const SAVE_AFTER = 700;
const OVERLAY_KINDS = new Set(["captions", "text", "hook", "sticker"]);

export async function openEditor(view, name, tab) {
  let data;
  try { data = await api("GET", `/api/projects/${encodeURIComponent(name)}`); }
  catch (e) {
    view.replaceChildren(h("h1", null, "Mission not found"), h("p", null, e.message), h("a", { class: "btn", href: "#/missions" }, "Back"));
    return null;
  }
  const ed = new Editor(name, data);
  view.replaceChildren(ed.el);
  ed.start();
  void tab;
  return ed;
}

class Editor {
  constructor(name, data) {
    this.name = name;
    this.meta = state.meta;
    this.data = data;
    this.fps = data.fps || 30;
    this.sourceDuration = data.source_duration || data.project.source.duration || 0;
    this.p = structuredClone(data.project);
    const fx = this.p.effects;
    this.pendingAuto = { zooms: typeof fx.zooms === "string" ? fx.zooms : null, emojis: typeof fx.emojis === "string" ? fx.emojis : null };
    if (this.pendingAuto.zooms) fx.zooms = [];
    if (this.pendingAuto.emojis) fx.emojis = [];
    M.normalize(this.p);
    this.history = new M.History();
    this.selection = null;
    this.version = 0;
    this.snapping = true;
    this.saved = JSON.stringify(this.p);
    this.saving = null;
    this.saveTimer = 0;
    this.clipVersion = data.clip_version;
    this.musicOk = data.music_ok;
    this.assets = { offset: 0, strip: null, stripImage: null, peaks: null, rate: 50, ready: false };
    this.memo = {};
    this.raf = 0;

    this.preview = new Preview(this);
    this.timeline = new Timeline(this);
    this.inspectorEl = h("div", { class: "ed-inspector panel", "aria-label": "Settings for the selection" });
    this.build();
    this.onJob = (job) => this.jobFinished(job);
    state.jobListeners.add(this.onJob);
  }

  // ---------------------------------------------------------------- layout
  build() {
    const tool = (label, onclick, title, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, title: title || label, onclick }, label);
    this.status = h("span", { class: "ed-status", "aria-live": "polite" }, "Saved");
    this.undoBtn = tool("↶ Undo", () => this.undo(), "Undo (Ctrl+Z)");
    this.redoBtn = tool("↷ Redo", () => this.redo(), "Redo (Ctrl+Shift+Z)");
    this.renderBtn = h("button", { type: "button", class: "btn go", onclick: () => this.render() }, "Render");
    this.watch = h("a", { class: "btn small alt", target: "_blank", rel: "noopener" }, "Watch render");
    this.download = h("a", { class: "btn small", download: `${this.name}.mp4` }, "Download");
    this.playBtn = h("button", { type: "button", class: "btn small go play", onclick: () => this.togglePlay(), "aria-label": "Play" }, "▶");
    this.timeLabel = h("span", { class: "ed-time" }, "0:00.0");
    this.safeBtn = tool("Safe zones", () => {
      this.preview.safe.hidden = !this.preview.safe.hidden;
      this.safeBtn.classList.toggle("on", !this.preview.safe.hidden);
    }, "Show where the Shorts and Reels buttons cover the video");
    this.snapBtn = tool("Snap", () => { this.snapping = !this.snapping; this.snapBtn.classList.toggle("on", this.snapping); },
      "Snap items to the playhead and to each other");
    this.snapBtn.classList.add("on");
    this.prepNote = h("span", { class: "muted small", hidden: true }, "Getting the editor ready…");
    this.updateLinks();

    this.el = h("div", { class: "ed" },
      h("div", { class: "ed-top" },
        h("a", { class: "btn small", href: "#/missions" }, "◀ Missions"),
        h("h1", null, "Edit"),
        h("span", { class: "ed-name muted", title: this.p.source.title || this.name }, this.p.source.title || this.name),
        this.status, this.prepNote,
        h("span", { class: "spacer" }),
        this.undoBtn, this.redoBtn,
        tool("Open folder", () => api("POST", `/api/projects/${encodeURIComponent(this.name)}/open`).catch((e) => toast(e.message))),
        this.download, this.watch, this.renderBtn),
      h("div", { class: "ed-main" },
        h("div", { class: "ed-stage" },
          this.preview.el,
          h("div", { class: "ed-transport" },
            tool("⏮", () => this.seek(0), "To the start (Home)"),
            tool("−1f", () => this.step(-1), "One frame back (←)"),
            this.playBtn,
            tool("+1f", () => this.step(1), "One frame forward (→)"),
            this.timeLabel, h("span", { class: "spacer" }), this.safeBtn)),
        this.inspectorEl),
      h("div", { class: "ed-tools" },
        tool("✂ Split", () => this.split(), "Split the clip at the playhead (S)"),
        tool("Delete", () => this.remove(), "Delete the selection (Delete)", "warn"),
        tool("Duplicate", () => this.duplicate(), "Duplicate the selection (Ctrl+D)"),
        h("span", { class: "sep" }),
        tool("+ Text", () => this.addText()), tool("+ Sticker", () => this.addSticker()), tool("+ Zoom", () => this.addZoom()),
        h("span", { class: "sep" }),
        tool("Cut pauses", () => this.removePauses(), "Split the video around every silence"),
        h("span", { class: "spacer" }),
        this.snapBtn,
        tool("−", () => this.timeline.zoomBy(0.7), "Zoom the timeline out"),
        tool("Fit", () => this.timeline.fit(), "Fit the whole clip"),
        tool("+", () => this.timeline.zoomBy(1.4), "Zoom the timeline in")),
      this.timeline.el);
  }

  start() {
    this.refreshInspector();
    this.useAssets(this.data);
    this.refreshPlan();
    this.updateLogo();
    this.timeline.fit();
    this.preview.draw(true);
    const loop = () => {
      const before = this.preview.T;
      this.preview.tick();
      if (this.preview.T !== before || this.preview.playing) {
        this.timeline.placePlayhead();
        if (this.preview.playing) this.timeline.follow();
      }
      this.timeLabel.textContent = `${clock(this.T)} / ${clock(this.total())}`;
      this.preview.draw();
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
    this.keys = (e) => this.key(e);
    document.addEventListener("keydown", this.keys);
    this.updateButtons();
    this.migrate();
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    document.removeEventListener("keydown", this.keys);
    state.jobListeners.delete(this.onJob);
    this.flush();
    this.preview.destroy();
    this.timeline.destroy();
  }

  // ---------------------------------------------------------------- derived values (worked out once per change)
  get T() { return this.preview.T; }
  memoize(key, fn) {
    if (this.memo.version !== this.version) this.memo = { version: this.version };
    if (!(key in this.memo)) this.memo[key] = fn();
    return this.memo[key];
  }
  clipList() { return this.memoize("clips", () => M.clips(this.p)); }
  total() { return this.memoize("total", () => M.totalLength(this.p)); }
  style() { return this.meta.styles.find((s) => s.id === this.p.captions.style) || this.meta.styles[0]; }
  lines() { return this.memoize("lines", () => M.captionLines(this.p, this.style())); }

  // ---------------------------------------------------------------- changing the project
  /** Runs fn on the project as one undoable step. quiet: the inspector isn't rebuilt (it made the change). */
  change(fn, { key = null, quiet = false } = {}) {
    const before = JSON.stringify(this.p);
    fn(this.p);
    if (JSON.stringify(this.p) === before) return;
    this.history.record(before, key);
    this.changed(quiet);
  }

  beginGesture() { this.gesture = JSON.stringify(this.p); }
  touched() { this.version += 1; }
  endGesture() {
    const before = this.gesture;
    this.gesture = null;
    if (before == null || before === JSON.stringify(this.p)) { this.version += 1; this.timeline.render(); return; }
    this.history.record(before);
    this.changed(false);
  }

  changed(quiet) {
    M.normalize(this.p);
    this.version += 1;
    if (this.selection && !this.exists(this.selection)) this.selection = null;
    if (this.T > this.total()) this.preview.seek(this.total());
    this.timeline.render();
    this.preview.draw(true);
    if (!quiet) this.refreshInspector();
    this.updateButtons();
    this.scheduleSave();
    this.checkCoverage();
    this.refreshPlan();
    this.updateLogo();
  }

  /** Asks the server where the video, facecam and template graphics go, when that might have changed. */
  refreshPlan() {
    const p = this.p;
    const key = JSON.stringify([p.frame, p.template, p.hook.text]);
    if (key === this.planKey) return;
    this.planKey = key;
    clearTimeout(this.planTimer);
    this.planTimer = setTimeout(async () => {
      try {
        const fp = await api("POST", `/api/projects/${encodeURIComponent(this.name)}/frame`, this.p);
        if (key === this.planKey) { this.preview.setPlan(fp); this.preview.draw(true); }
      } catch { this.planKey = null; /* a half-typed setting; the next change asks again */ }
    }, 120);
  }

  updateLogo() {
    const file = this.p.logo.file;
    this.preview.setLogo(file ? `/api/projects/${encodeURIComponent(this.name)}/media/logo?v=${encodeURIComponent(file)}` : null);
  }

  exists(sel) {
    const p = this.p;
    switch (sel.kind) {
      case "clip": return p.segments.some((s) => s.id === sel.id);
      case "line": return this.lines().some((l) => l.key === sel.id);
      case "text": return p.texts.some((t) => t.id === sel.id);
      case "sticker": return p.effects.emojis.some((e) => e.id === sel.id);
      case "zoom": return p.effects.zooms.some((z) => z.id === sel.id);
      case "hook": return !!(p.hook.text || "").trim();
      default: return true;             // music, captions, layout, template, logo: always there
    }
  }

  select(sel) {
    const same = (a, b) => (!a && !b) || (a && b && a.kind === b.kind && a.id === b.id);
    if (same(sel, this.selection)) return;
    this.selection = sel;
    this.timeline.render();
    this.preview.draw(true);
    this.refreshInspector();
  }

  refreshInspector() {
    const scroll = this.inspectorEl.scrollTop;
    this.inspectorEl.replaceChildren(inspector(this));
    this.inspectorEl.scrollTop = scroll;
  }

  undo() { this.restore(this.history.undo(JSON.stringify(this.p))); }
  redo() { this.restore(this.history.redo(JSON.stringify(this.p))); }
  restore(snapshot) {
    if (!snapshot) return;
    this.p = JSON.parse(snapshot);
    this.changed(false);
  }

  updateButtons() {
    this.undoBtn.disabled = !this.history.canUndo;
    this.redoBtn.disabled = !this.history.canRedo;
  }

  // ---------------------------------------------------------------- saving
  unsaved() { return JSON.stringify(this.p) !== this.saved || !!this.saving; }

  scheduleSave() {
    clearTimeout(this.saveTimer);
    this.status.textContent = "Unsaved changes";
    this.status.className = "ed-status dirty";
    this.saveTimer = setTimeout(() => this.flush(), SAVE_AFTER);
  }

  /** Saves now if anything changed. Resolves once the server has it. */
  async flush() {
    clearTimeout(this.saveTimer);
    if (this.saving) await this.saving.catch(() => {});
    const body = JSON.stringify(this.p);
    if (body === this.saved) return;
    this.status.textContent = "Saving…";
    this.saving = fetch(`/api/projects/${encodeURIComponent(this.name)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body, keepalive: body.length < 60000 })
      .then(async (res) => {
        if (!res.ok) {
          let detail = `HTTP ${res.status}`;
          try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
          throw new Error(detail);
        }
        this.saved = body;
        if (JSON.stringify(this.p) === body) { this.status.textContent = "All changes saved"; this.status.className = "ed-status"; }
      })
      .catch((e) => { this.status.textContent = "Not saved"; this.status.className = "ed-status bad"; toast(`Couldn't save: ${e.message}`); throw e; })
      .finally(() => { this.saving = null; });
    return this.saving;
  }

  // ---------------------------------------------------------------- playback
  togglePlay() { if (this.preview.playing) this.preview.pause(); else this.preview.play(); }
  onPlayState() {
    this.playBtn.textContent = this.preview.playing ? "❚❚" : "▶";
    this.playBtn.setAttribute("aria-label", this.preview.playing ? "Pause" : "Play");
  }
  seek(T) {
    this.preview.seek(T);
    this.timeline.placePlayhead();
    this.preview.draw();
  }
  step(frames) {
    if (this.preview.playing) this.preview.pause();
    this.seek(M.snapFrame(this.T + frames / this.fps, this.fps));
  }

  // ---------------------------------------------------------------- editing actions
  split() {
    let id = null;
    this.change(() => { id = M.splitAt(this.p, this.T, this.fps); });
    if (id) this.select({ kind: "clip", id }); else toast("Put the playhead inside a clip, away from its edges, to split it.");
  }

  remove() {
    const sel = this.selection;
    if (!sel) { toast("Select something first."); return; }
    const p = this.p;
    if (sel.kind === "clip" && p.segments.length <= 1) { toast("The video track needs at least one clip."); return; }
    this.change(() => {
      if (sel.kind === "clip") M.deleteClip(p, sel.id);
      else if (sel.kind === "line") { const line = this.lines().find((l) => l.key === sel.id); if (line) M.deleteLine(p, line); }
      else if (sel.kind === "text") p.texts = p.texts.filter((t) => t.id !== sel.id);
      else if (sel.kind === "sticker") p.effects.emojis = p.effects.emojis.filter((e) => e.id !== sel.id);
      else if (sel.kind === "zoom") p.effects.zooms = p.effects.zooms.filter((z) => z.id !== sel.id);
      else if (sel.kind === "hook") p.hook.text = "";
      else if (sel.kind === "music") p.music.file = null;
      else if (sel.kind === "captions") p.captions.enabled = false;
      else if (sel.kind === "template") p.template.id = "none";
      else if (sel.kind === "logo") p.logo.file = null;
    });
    this.select(null);
  }

  duplicate() {
    const sel = this.selection;
    if (!sel) return;
    const p = this.p;
    let id = null;
    this.change(() => {
      if (sel.kind === "clip") id = M.duplicateClip(p, sel.id);
      else if (sel.kind === "text") {
        const t = p.texts.find((x) => x.id === sel.id); const len = t.end - t.start;
        const copy = { ...t, id: M.uid("t"), start: t.end, end: M.r4(t.end + len) }; p.texts.push(copy); id = copy.id;
      } else if (sel.kind === "sticker") {
        const e = p.effects.emojis.find((x) => x.id === sel.id);
        const copy = { ...e, id: M.uid("e"), t: M.r4(e.t + (e.seconds ?? 1.4)) }; p.effects.emojis.push(copy); id = copy.id;
      } else if (sel.kind === "zoom") {
        const z = p.effects.zooms.find((x) => x.id === sel.id); const len = z.end - z.start;
        const copy = { ...z, id: M.uid("z"), start: z.end, end: M.r4(z.end + len) }; p.effects.zooms.push(copy); id = copy.id;
      }
    });
    if (id) this.select({ kind: sel.kind, id });
  }

  at(length) {
    const total = this.total();
    const start = this.T > total - 0.2 ? Math.max(0, total - length) : this.T;
    return [M.r4(start), M.r4(Math.min(Math.max(total, start + length), start + length))];
  }

  addText() {
    const [start, end] = this.at(2);
    const t = { id: M.uid("t"), text: "Your text", start, end, x: 0.5, y: 0.4, size: 90, font: "Poppins ExtraBold",
                color: "FFFFFF", box: false, back: "000000" };
    this.change(() => this.p.texts.push(t));
    this.select({ kind: "text", id: t.id });
  }

  addSticker() {
    const [t] = this.at(1.4);
    const e = { id: M.uid("e"), t, emoji: "🔥", seconds: 1.4, x: 0.5, y: 0.45, size: this.meta.emoji_size };
    this.change(() => this.p.effects.emojis.push(e));
    this.select({ kind: "sticker", id: e.id });
  }

  addZoom() {
    const [start, end] = this.at(1.5);
    const z = { id: M.uid("z"), start, end };
    this.change(() => this.p.effects.zooms.push(z));
    this.select({ kind: "zoom", id: z.id });
  }

  addHook() {
    this.change(() => { this.p.hook.text = "YOUR HOOK HERE"; });
    this.select({ kind: "hook", id: "hook" });
  }

  async removePauses() {
    try {
      const r = await api("POST", `/api/projects/${encodeURIComponent(this.name)}/pauses`, { project: this.p, min_gap: this.p.edit.min_gap });
      if (!r.cuts) { toast("No pauses long enough to cut.", true); return; }
      this.change(() => { this.p.segments = r.segments; });
      toast(`Cut ${r.cuts} pause${r.cuts > 1 ? "s" : ""} (${r.removed.toFixed(1)}s). Ctrl+Z brings them back.`, true);
    } catch (e) { toast(e.message); }
  }

  async autoEffects(kind) {
    try {
      const r = await api("POST", `/api/projects/${encodeURIComponent(this.name)}/autofx`, this.p);
      this.change(() => { this.p.effects[kind] = r[kind]; });
      toast(`${r[kind].length} ${kind === "zooms" ? "zooms" : "stickers"} chosen from the speech.`, true);
    } catch (e) { toast(e.message); }
  }

  async aiFix() {
    if (!this.meta.ai_ready) { toast("Add an AI key in Settings first. Groq's free plan works."); return; }
    if (!confirm("Let the AI fix spellings, names and numbers in the captions, then render? Ctrl+Z undoes it.")) return;
    try {
      await this.flush();
      this.aiBefore = JSON.stringify(this.p);
      await api("POST", `/api/projects/${encodeURIComponent(this.name)}/ai/fix`);
      toast("The AI is on it. The captions update when it's done.", true);
      state.pollSoon();
    } catch (e) { toast(e.message); }
  }

  /** Projects made before the editor: pause cutting and automatic effects become real items you can see. */
  async migrate() {
    const p = this.p;
    let changed = false;
    try {
      if (p.edit.cut_pauses) {
        const r = await api("POST", `/api/projects/${encodeURIComponent(this.name)}/pauses`, { project: p, min_gap: p.edit.min_gap });
        p.segments = r.segments;
        p.edit.cut_pauses = false;
        changed = true;
      }
      const auto = this.pendingAuto;
      if (auto.zooms || auto.emojis) {
        const r = auto.zooms === "auto" || auto.emojis === "auto"
          ? await api("POST", `/api/projects/${encodeURIComponent(this.name)}/autofx`, p) : { zooms: [], emojis: [] };
        if (auto.zooms) p.effects.zooms = auto.zooms === "auto" ? r.zooms : [];
        if (auto.emojis) p.effects.emojis = auto.emojis === "auto" ? r.emojis : [];
        changed = true;
      }
    } catch (e) { toast(`Couldn't set up the effects: ${e.message}`); }
    this.pendingAuto = {};
    if (changed) { this.changed(false); this.flush().catch(() => {}); }
  }

  // ---------------------------------------------------------------- editing copy, render, jobs
  useAssets(data) {
    const ed = data.editor || {};
    const base = `/api/projects/${encodeURIComponent(this.name)}/media`;
    this.setMusicOk(data.music_ok);
    if (ed.ready) {
      this.assets = { ...this.assets, ready: true, offset: ed.offset, end: ed.end, strip: ed.strip };
      this.preview.setVideo(`${base}/proxy`, ed.offset);
      const img = new Image();
      img.onload = () => this.timeline.render();
      img.src = `${base}/filmstrip?v=${Date.now()}`;
      this.assets.stripImage = img;
      fetch(`${base}/waveform`).then((r) => r.json()).then((w) => { this.assets.peaks = w.peaks; this.assets.rate = w.rate; this.timeline.render(); })
        .catch(() => {});
      this.prepNote.hidden = true;
    } else {
      this.preview.setVideo(`${base}/source`, 0);
      this.prepNote.hidden = false;
      if (!data.preparing) api("POST", `/api/projects/${encodeURIComponent(this.name)}/prepare`).then(() => state.pollSoon()).catch(() => {});
    }
  }

  setMusicOk(ok) {
    this.musicOk = ok;
    this.preview.setMusic(ok && this.p.music.file ? `/api/projects/${encodeURIComponent(this.name)}/media/music?v=${encodeURIComponent(this.p.music.file)}` : null);
  }

  /** A clip trimmed past the editing copy: play the full video until a new copy is made. */
  checkCoverage() {
    const a = this.assets;
    if (!a.ready) return;
    const outside = this.p.segments.some((s) => s.start < a.offset - 0.01 || s.end > a.end + 0.01);
    if (!outside) return;
    a.ready = false;
    this.preview.setVideo(`/api/projects/${encodeURIComponent(this.name)}/media/source`, 0);
    this.prepNote.hidden = false;
    this.flush().then(() => api("POST", `/api/projects/${encodeURIComponent(this.name)}/prepare`)).then(() => state.pollSoon()).catch(() => {});
  }

  updateLinks() {
    const url = `/api/projects/${encodeURIComponent(this.name)}/media/clip?v=${this.clipVersion}`;
    this.watch.href = url;
    this.download.href = url;
    this.watch.hidden = this.download.hidden = !this.clipVersion;
  }

  async render() {
    this.renderBtn.disabled = true;
    try {
      await this.flush();
      await api("POST", `/api/projects/${encodeURIComponent(this.name)}/render`);
      this.renderBtn.textContent = "Rendering…";
      state.pollSoon();
    } catch (e) { toast(e.message); this.renderBtn.disabled = false; }
  }

  async jobFinished(job) {
    if (job.project !== this.name) return;
    let data;
    try { data = await api("GET", `/api/projects/${encodeURIComponent(this.name)}`); } catch { return; }
    if (job.kind === "prepare") {
      if (job.status === "done") this.useAssets(data);
      else this.prepNote.textContent = "The editing copy couldn't be made; playing the full video.";
    } else if (job.kind === "render") {
      this.renderBtn.disabled = false;
      this.renderBtn.textContent = "Render";
      if (job.status === "done") { this.clipVersion = data.clip_version; this.updateLinks(); toast("Rendered. Press Watch render or Download.", true); }
    } else if (job.kind === "ai" && job.status === "done") {
      const before = this.aiBefore || JSON.stringify(this.p);
      this.p.captions.words = data.project.captions.words;
      this.saved = JSON.stringify({ ...JSON.parse(this.saved), captions: data.project.captions });
      this.history.record(before);
      this.clipVersion = data.clip_version;
      this.updateLinks();
      this.changed(false);
    }
  }

  // ---------------------------------------------------------------- keyboard
  key(e) {
    const t = e.target;
    const typing = t.closest && t.closest("input, textarea, select, [contenteditable]");
    if (typing) { if (e.key === "Escape") t.blur(); return; }
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (ctrl && k === "z") { e.preventDefault(); if (e.shiftKey) this.redo(); else this.undo(); return; }
    if (ctrl && k === "y") { e.preventDefault(); this.redo(); return; }
    if (ctrl && k === "d") { e.preventDefault(); this.duplicate(); return; }
    if (ctrl || e.altKey) return;
    if (e.key === " ") {
      if (t.closest && t.closest("button, a")) return;
      e.preventDefault(); this.togglePlay();
    } else if (k === "s") { e.preventDefault(); this.split(); }
    else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); this.remove(); }
    else if (e.key === "Escape") this.select(null);
    else if (e.key === "Home") { e.preventDefault(); this.seek(0); }
    else if (e.key === "End") { e.preventDefault(); this.seek(this.total()); }
    else if (e.key === "+" || e.key === "=") this.timeline.zoomBy(1.4);
    else if (e.key === "-") this.timeline.zoomBy(0.7);
    else if (e.key.startsWith("Arrow")) {
      const overlay = this.selection && OVERLAY_KINDS.has(this.selection.kind);
      const big = e.shiftKey ? 4 : 1;
      if (overlay && (e.key === "ArrowUp" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        const d = 0.005 * big;
        const dx = e.key === "ArrowLeft" ? -d : e.key === "ArrowRight" ? d : 0;
        const dy = e.key === "ArrowUp" ? -d : e.key === "ArrowDown" ? d : 0;
        if (this.preview.nudge(dx, dy)) e.preventDefault();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const dir = e.key === "ArrowLeft" ? -1 : 1;
        if (e.shiftKey) this.seek(this.T + dir); else this.step(dir);
      }
    }
  }
}
