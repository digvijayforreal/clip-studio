/* The inspector: settings for whatever is selected, or for the whole project when nothing is.
   Every input changes the project through ed.change(), so it can be undone. */
import { api, check, clock, emojiUrl, h, radioChips, state, styleChips, toast, upload } from "../lib.js";
import { openBoxes } from "./boxes.js";
import * as M from "./model.js";

const CORNER_NAMES = [{ id: "tl", label: "↖ Top left" }, { id: "tr", label: "↗ Top right" },
                      { id: "bl", label: "↙ Bottom left" }, { id: "br", label: "↘ Bottom right" }];

let fieldId = 0;
const nextId = () => `f${++fieldId}`;

function field(label, input, hint) {
  const id = input.id || (input.id = nextId());
  return h("div", { class: "field" }, h("label", { for: id }, label, hint ? h("span", { class: "hint" }, hint) : null), input);
}

function num(ed, value, onSet, { step = 0.01, min = 0, max, key, label } = {}) {
  return h("input", { type: "number", step, min, max, value: M.r4(value), "aria-label": label,
    oninput: (e) => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) ed.change(() => onSet(v), { key, quiet: true }); } });
}

function slider(ed, label, value, min, max, step, onSet, show, key) {
  const out = h("output", null, show(value));
  const input = h("input", { type: "range", min, max, step, value,
    oninput: (e) => { const v = Number(e.target.value); out.textContent = show(v); ed.change(() => onSet(v), { key, quiet: true }); } });
  const id = nextId();
  input.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label, " ", out), input);
}

function color(ed, label, hex, onSet, key) {
  return field(label, h("input", { type: "color", value: "#" + hex,
    oninput: (e) => ed.change(() => onSet(e.target.value.slice(1).toUpperCase()), { key, quiet: true }) }));
}

const buttons = (...kids) => h("div", { class: "actions" }, ...kids);
const btn = (label, onclick, cls = "") => h("button", { type: "button", class: `btn small ${cls}`, onclick }, label);
const title = (text, sub) => h("div", { class: "insp-title" }, h("h2", null, text), sub ? h("span", { class: "muted small" }, sub) : null);

export function inspector(ed) {
  const sel = ed.selection;
  const panel = {
    clip: clipPanel, line: linePanel, captions: captionsPanel, text: textPanel, hook: hookPanel,
    sticker: stickerPanel, zoom: zoomPanel, music: musicPanel, layout: layoutPanel, template: templatePanel,
    logo: logoPanel,
  }[sel && sel.kind] || projectPanel;
  return panel(ed, sel && sel.id) || projectPanel(ed);
}

// ------------------------------------------------------------------ video clip
function clipPanel(ed, id) {
  const c = ed.clipList().find((x) => x.id === id);
  if (!c) return null;
  const seg = c.seg;
  const total = ed.clipList().length;
  return h("div", null,
    title(`Clip ${c.index + 1} of ${total}`, `${clock(c.length)} long, at ${clock(c.start)} in the edit`),
    h("div", { class: "grid-2" },
      field("Starts at (video seconds)", num(ed, seg.start, (v) => M.trimIn(seg, v, ed.fps), { key: `in:${id}`, max: seg.end })),
      field("Ends at (video seconds)", num(ed, seg.end, (v) => M.trimOut(seg, v, ed.fps, ed.sourceDuration), { key: `out:${id}`, min: seg.start }))),
    slider(ed, "Volume", seg.volume ?? 1, 0, 2, 0.05, (v) => { seg.volume = v; }, (v) => `${Math.round(v * 100)}%`, `vol:${id}`),
    (seg.volume ?? 1) > 1 ? h("p", { class: "hint" }, "The preview plays at most 100%; the render uses the louder volume.") : null,
    buttons(
      btn("Split at playhead (S)", () => ed.split()),
      btn("Duplicate (Ctrl+D)", () => ed.duplicate()),
      btn("◀ Earlier", () => ed.change(() => M.moveClip(ed.p, id, c.index - 1)), ""),
      btn("Later ▶", () => ed.change(() => M.moveClip(ed.p, id, c.index + 1)), ""),
      btn("Delete", () => ed.remove(), "warn")),
    h("p", { class: "hint" }, "Drag a clip's edges on the timeline to trim it, or its middle to move it."));
}

// ------------------------------------------------------------------ captions
function linePanel(ed, key) {
  const line = ed.lines().find((l) => l.key === key);
  if (!line) return null;
  const p = ed.p;
  const words = [...new Set(line.words.map((w) => w.i))];
  const text = h("input", { type: "text", value: words.map((i) => p.captions.words[i].w).join(" "),
    onchange: (e) => {
      let first = -1;
      ed.change(() => { first = M.setLineText(p, line, e.target.value); });
      ed.select(first >= 0 ? { kind: "line", id: `${line.words[0].clip.id}:${first}` } : null);
    } });
  return h("div", null,
    title("Caption line", `${clock(line.start)} – ${clock(line.shownEnd)}`),
    field("Text", text, "Press Enter to apply. Changing the number of words spreads them over the line's time."),
    h("h3", null, "Words"),
    h("table", { class: "list" },
      h("thead", null, h("tr", null, h("th", null, "Word"), h("th", null, "Start"), h("th", null, "End"), h("th", null, ""))),
      h("tbody", null, words.map((i) => {
        const w = p.captions.words[i];
        return h("tr", { class: (w.p ?? 1) < 0.5 ? "unsure" : "" },
          h("td", null, h("input", { type: "text", value: w.w, "aria-label": "Word",
            oninput: (e) => ed.change(() => { w.w = e.target.value; }, { key: `w:${i}`, quiet: true }) })),
          h("td", null, num(ed, w.s, (v) => { w.s = v; }, { key: `ws:${i}`, label: "Start" })),
          h("td", null, num(ed, w.e, (v) => { w.e = v; }, { key: `we:${i}`, label: "End" })),
          h("td", null, btn("✕", () => { ed.change(() => p.captions.words.splice(i, 1)); ed.select(null); }, "warn")));
      }))),
    h("p", { class: "hint" }, "Times are seconds in the video. Orange words are ones the speech model wasn't sure about."),
    buttons(btn("Caption style and position", () => ed.select({ kind: "captions", id: "captions" })),
      btn("Delete line", () => ed.remove(), "warn")));
}

function captionsPanel(ed) {
  const cap = ed.p.captions;
  return h("div", null,
    title("Captions", cap.language === "hi" ? "Hindi" : cap.language === "en" ? "English" : cap.language),
    check("cap-on", "Show captions", null, cap.enabled, (v) => ed.change(() => { cap.enabled = v; })),
    h("label", null, "Style"), styleChips(cap.style, (v) => ed.change(() => { cap.style = v; })),
    slider(ed, "Size", cap.scale ?? 1, 0.4, 2.5, 0.05, (v) => { cap.scale = v; }, (v) => `${Math.round(v * 100)}%`, "capscale"),
    buttons(
      btn("Back to the middle", () => ed.change(() => { cap.x = 0.5; })),
      btn("Automatic height", () => ed.change(() => { cap.y = null; }))),
    h("p", { class: "hint" }, "Drag the captions on the preview to move them; drag the corner to resize. Click a caption on the timeline to fix its words."),
    h("hr"),
    buttons(btn("AI fix all captions", () => ed.aiFix(), "alt")));
}

// ------------------------------------------------------------------ text, hook
function textPanel(ed, id) {
  const t = ed.p.texts.find((x) => x.id === id);
  if (!t) return null;
  return h("div", null,
    title("Text", `${clock(t.start)} – ${clock(t.end)}`),
    field("Text", h("textarea", { rows: 2, maxlength: 300, value: t.text,
      oninput: (e) => ed.change(() => { t.text = e.target.value; }, { key: `tt:${id}`, quiet: true }) }), "Enter starts a new line."),
    h("label", null, "Font"),
    radioChips(state.meta.fonts.map((f) => ({ id: f, label: f === "Anton" ? "Anton" : "Poppins" })), t.font,
      (v) => ed.change(() => { t.font = v; })),
    slider(ed, "Size", t.size, 20, 300, 1, (v) => { t.size = v; }, (v) => `${v}px`, `ts:${id}`),
    h("div", { class: "grid-2" },
      color(ed, "Colour", t.color, (v) => { t.color = v; }, `tc:${id}`),
      color(ed, t.box ? "Box colour" : "Outline colour", t.back, (v) => { t.back = v; }, `tb:${id}`)),
    check(`box-${id}`, "Solid box behind the text", null, t.box, (v) => { ed.change(() => { t.box = v; }); ed.refreshInspector(); }),
    h("div", { class: "grid-2" },
      field("From (s)", num(ed, t.start, (v) => { t.start = Math.max(0, Math.min(v, t.end - 0.1)); }, { key: `t0:${id}` })),
      field("To (s)", num(ed, t.end, (v) => { t.end = Math.max(t.start + 0.1, v); }, { key: `t1:${id}` }))),
    buttons(btn("Duplicate", () => ed.duplicate()), btn("Delete", () => ed.remove(), "warn")));
}

function hookPanel(ed) {
  const hook = ed.p.hook;
  const ideas = h("div", { class: "chips", "aria-live": "polite" });
  const input = h("input", { type: "text", maxlength: 120, value: hook.text || "",
    oninput: (e) => ed.change(() => { hook.text = e.target.value; }, { key: "hook", quiet: true }) });
  return h("div", null,
    title("Hook title", "A white box near the top"),
    field("Text", input),
    buttons(btn("AI ideas", async (e) => {
      e.target.disabled = true;
      ideas.replaceChildren(h("span", { class: "muted" }, "Thinking…"));
      try {
        await ed.flush();
        const r = await api("POST", `/api/projects/${encodeURIComponent(ed.name)}/ai/hook`);
        ideas.replaceChildren(...r.hooks.map((text) => h("button", { class: "chip", type: "button",
          onclick: () => { ed.change(() => { hook.text = text; }); input.value = text; } }, text)));
      } catch (err) { ideas.replaceChildren(); toast(err.message); }
      e.target.disabled = false;
    }, "alt")),
    ideas,
    field("Stays up for (seconds)", h("input", { type: "number", min: 0.5, step: 0.5, value: hook.seconds ?? "",
      oninput: (e) => { const v = parseFloat(e.target.value); ed.change(() => { hook.seconds = Number.isFinite(v) && v > 0 ? v : null; }, { key: "hooks", quiet: true }); } }),
      "Empty: the whole clip. You can also drag its end on the timeline."),
    buttons(btn("Automatic height", () => ed.change(() => { hook.y = null; })), btn("Remove hook", () => ed.remove(), "warn")));
}

// ------------------------------------------------------------------ stickers, zooms
function stickerPanel(ed, id) {
  const e = ed.p.effects.emojis.find((x) => x.id === id);
  if (!e) return null;
  return h("div", null,
    title("Sticker", `${clock(e.t)} for ${clock(e.seconds ?? 1.4)}`),
    h("div", { class: "emoji-grid", role: "radiogroup", "aria-label": "Emoji" }, state.meta.emojis.map((em) =>
      h("button", { type: "button", class: "emoji-pick" + (em === e.emoji ? " on" : ""), role: "radio", "aria-checked": em === e.emoji ? "true" : "false",
        "aria-label": em, onclick: () => { ed.change(() => { e.emoji = em; }); ed.refreshInspector(); } },
        h("img", { src: emojiUrl(em), alt: "", loading: "lazy" })))),
    slider(ed, "Size", e.size ?? ed.meta.emoji_size, 40, 600, 2, (v) => { e.size = v; }, (v) => `${v}px`, `es:${id}`),
    h("div", { class: "grid-2" },
      field("At (s)", num(ed, e.t, (v) => { e.t = Math.max(0, v); }, { key: `et:${id}` })),
      field("For (s)", num(ed, e.seconds ?? 1.4, (v) => { e.seconds = Math.max(0.3, v); }, { key: `ed:${id}`, step: 0.1 }))),
    buttons(btn("Duplicate", () => ed.duplicate()), btn("Delete", () => ed.remove(), "warn")));
}

function zoomPanel(ed, id) {
  const z = ed.p.effects.zooms.find((x) => x.id === id);
  if (!z) return null;
  const fx = ed.p.effects;
  return h("div", null,
    title("Zoom punch-in", `${clock(z.start)} – ${clock(z.end)}`),
    h("div", { class: "grid-2" },
      field("From (s)", num(ed, z.start, (v) => { z.start = Math.max(0, Math.min(v, z.end - 0.2)); }, { key: `z0:${id}` })),
      field("To (s)", num(ed, z.end, (v) => { z.end = Math.max(z.start + 0.2, v); }, { key: `z1:${id}` }))),
    slider(ed, "Strength (every zoom)", fx.zoom_scale, 1.05, 1.4, 0.01, (v) => { fx.zoom_scale = v; }, (v) => `${Math.round((v - 1) * 100)}%`, "zscale"),
    buttons(btn("Duplicate", () => ed.duplicate()), btn("Delete", () => ed.remove(), "warn")));
}

// ------------------------------------------------------------------ music
function musicPanel(ed) {
  const music = ed.p.music;
  const file = h("input", { type: "file", accept: "audio/*", class: "sr-only", id: nextId(),
    onchange: async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      try {
        await ed.flush();
        const data = await upload(`/api/projects/${encodeURIComponent(ed.name)}/music?filename=${encodeURIComponent(f.name)}`, f);
        ed.change(() => { music.file = data.project.music.file; });
        ed.setMusicOk(true);
        toast("Music added.", true);
        ed.refreshInspector();
      } catch (err) { toast(err.message); }
    } });
  return h("div", null,
    title("Music"),
    h("p", { class: "muted" }, music.file ? `Playing: ${music.file.replace(/^music_/, "")}` : "No music yet. Use music you have the rights to."),
    h("div", { class: "row" },
      h("label", { for: file.id, class: "btn small", style: { margin: 0 } }, music.file ? "Change music" : "Add music"), file,
      music.file ? btn("Remove", () => ed.change(() => { music.file = null; }), "warn") : null),
    slider(ed, "Volume", music.volume, 0, 0.6, 0.01, (v) => { music.volume = v; }, (v) => `${Math.round(v * 100)}%`, "mvol"),
    h("p", { class: "hint" }, "When rendering, it's turned down further whenever someone talks. The preview doesn't do that."));
}

// ------------------------------------------------------------------ layout and facecam
function layoutPanel(ed) {
  const p = ed.p, fr = p.frame;
  const cam = (state.meta.layouts.find((l) => l.id === fr.layout) || {}).cam;
  const templated = ["x_light", "x_dark", "card"].includes(p.template.id);
  const rerender = () => ed.refreshInspector();
  return h("div", null,
    title("Layout and facecam"),
    templated ? h("p", { class: "hint" }, `The ${state.meta.templates.find((t) => t.id === p.template.id).label} template places the video itself; it shows the gameplay box.`) : null,
    radioChips(state.meta.layouts.map((l) => ({ id: l.id, label: l.label })), fr.layout,
      (v) => { ed.change(() => { fr.layout = v; }); rerender(); }, "Layout", "layout-card"),
    fr.layout === "crop"
      ? slider(ed, "Keep this part of the picture", fr.x ?? 0.5, 0, 1, 0.01, (v) => { fr.x = v; },
               (v) => (v < 0.4 ? "left" : v > 0.6 ? "right" : "middle"), "framex") : null,
    cam || templated ? h("div", null,
      h("h3", null, "Facecam and game boxes"),
      buttons(h("button", { type: "button", class: "btn small alt", onclick: () => openBoxes(ed) }, "Draw the boxes on the video")),
      cam ? h("div", null,
        h("label", null, "The facecam is in the source's"),
        radioChips(CORNER_NAMES, fr.cam_corner, (c) => {
          ed.change(() => { fr.cam_corner = c; fr.face = { ...state.meta.face_presets[c] }; });
          rerender();
        }, "Facecam corner"),
        h("p", { class: "hint" }, "Picking a corner sets a starting box there. Draw the boxes for an exact fit.")) : null) : null,
    fr.layout === "stack" || fr.layout === "stack_bottom"
      ? slider(ed, "Facecam height", fr.split, 0.25, 0.6, 0.01, (v) => { fr.split = v; }, (v) => `${Math.round(v * 100)}% of the screen`, "split") : null,
    fr.layout === "corner" || fr.layout === "circle"
      ? slider(ed, "Facecam size", fr.cam_size, 0.2, 0.6, 0.01, (v) => { fr.cam_size = v; }, (v) => `${Math.round(v * 100)}% of the width`, "camsize") : null,
    h("p", { class: "hint" }, "Captions and the hook move out of the facecam's way by themselves, unless you've placed them."),
    buttons(h("button", { type: "button", class: "btn small", onclick: () => ed.change(() => { p.captions.y = null; p.hook.y = null; }) }, "Put captions and hook back to automatic")));
}

// ------------------------------------------------------------------ templates
function templatePanel(ed) {
  const t = ed.p.template;
  const text = (label, key, max, placeholder, hint) => field(label, h("input", { type: "text", maxlength: max, value: t[key] || "", placeholder,
    oninput: (e) => ed.change(() => { t[key] = e.target.value; }, { key: `tpl:${key}`, quiet: true }) }), hint);
  const src = ed.p.source;
  return h("div", null,
    title("Template", "Wraps the clip in a ready-made design"),
    radioChips(state.meta.templates.map((x) => ({ id: x.id, label: x.label })), t.id,
      (v) => { ed.change(() => { t.id = v; }); ed.refreshInspector(); }, "Template", "layout-card"),
    t.id === "x_light" || t.id === "x_dark" ? h("div", null,
      text("Name", "name", 60, src.uploader || "Your Name"),
      text("Handle", "handle", 40, "yourhandle", "Without the @."),
      field("Post text", h("textarea", { rows: 3, maxlength: 400, value: t.text, placeholder: "Leave empty to use the hook title",
        oninput: (e) => ed.change(() => { t.text = e.target.value; }, { key: "tpl:text", quiet: true }) })),
      check("tpl-verified", "Blue check mark", null, t.verified, (v) => ed.change(() => { t.verified = v; })),
      h("p", { class: "hint" }, "The post text takes the hook's place, so the hook isn't shown twice.")) : null,
    t.id === "news" ? h("div", null,
      text("Label", "label", 30, "BREAKING NEWS"),
      text("Headline", "headline", 160, "Leave empty to use the hook title"),
      text("Ticker", "ticker", 300, `${src.title || ""}`, "Scrolls along the bottom. Empty: the video's title and channel.")) : null,
    t.id === "card" ? h("div", null,
      h("label", null, "Background"),
      radioChips([{ id: "blur", label: "Blurred video" }, { id: "color", label: "Colour" }], t.bg,
        (v) => { ed.change(() => { t.bg = v; }); ed.refreshInspector(); }, "Card background"),
      t.bg === "color" ? color(ed, "Colour", t.bg_color, (v) => { t.bg_color = v; }, "tpl:bg") : null) : null);
}

// ------------------------------------------------------------------ logo and credit
function logoPanel(ed) {
  const lg = ed.p.logo, credit = ed.p.credit;
  const file = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", class: "sr-only", id: nextId(),
    onchange: async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      try {
        await ed.flush();
        const data = await upload(`/api/projects/${encodeURIComponent(ed.name)}/logo?filename=${encodeURIComponent(f.name)}`, f);
        ed.change(() => { lg.file = data.project.logo.file; });
        toast("Logo added. Drag the sliders to place it.", true);
        ed.refreshInspector();
      } catch (err) { toast(err.message); }
    } });
  const src = ed.p.source;
  const who = src.uploader || src.title || "";
  return h("div", null,
    title("Logo and credit"),
    h("h3", null, "Logo / watermark"),
    h("p", { class: "muted" }, lg.file ? `Using: ${lg.file.replace(/^logo_/, "")}` : "No logo yet. A PNG with a see-through background looks best."),
    h("div", { class: "row" },
      h("label", { for: file.id, class: "btn small", style: { margin: 0 } }, lg.file ? "Change logo" : "Add logo"), file,
      lg.file ? btn("Remove", () => { ed.change(() => { lg.file = null; }); ed.refreshInspector(); }, "warn") : null),
    lg.file ? h("div", null,
      h("label", null, "Corner"),
      radioChips(CORNER_NAMES, lg.corner, (c) => ed.change(() => { lg.corner = c; }), "Logo corner"),
      slider(ed, "Size", lg.size, 0.04, 0.5, 0.01, (v) => { lg.size = v; }, (v) => `${Math.round(v * 100)}% of the width`, "logosize"),
      slider(ed, "Opacity", lg.opacity, 0.1, 1, 0.05, (v) => { lg.opacity = v; }, (v) => `${Math.round(v * 100)}%`, "logoop")) : null,
    h("h3", null, "Credit line"),
    field("Text", h("input", { type: "text", maxlength: 80, value: credit.text, placeholder: who ? `Clip: ${who}` : "Clip: channel name",
      oninput: (e) => ed.change(() => { credit.text = e.target.value; }, { key: "credit", quiet: true }) }), "Small text along the bottom. Empty: none."),
    who ? buttons(btn(`Use “Clip: ${who}”`, () => { ed.change(() => { credit.text = `Clip: ${who}`.slice(0, 80); }); ed.refreshInspector(); })) : null);
}

// ------------------------------------------------------------------ the whole project
function projectPanel(ed) {
  const p = ed.p, fx = p.effects;
  const layout = state.meta.layouts.find((l) => l.id === p.frame.layout);
  const tpl = state.meta.templates.find((t) => t.id === p.template.id);
  return h("div", null,
    title("Project", "Click anything on the timeline or the preview to edit it"),
    h("h3", null, "Look"),
    h("div", { class: "look-rows" },
      h("button", { type: "button", class: "btn small", onclick: () => ed.select({ kind: "layout", id: "layout" }) }, `Layout: ${layout ? layout.label : p.frame.layout}`),
      h("button", { type: "button", class: "btn small", onclick: () => ed.select({ kind: "template", id: "template" }) }, `Template: ${tpl ? tpl.label : "None"}`),
      h("button", { type: "button", class: "btn small", onclick: () => ed.select({ kind: "logo", id: "logo" }) },
        `Logo and credit: ${p.logo.file ? "logo" : "no logo"}${p.credit.text ? ", credit" : ""}`)),
    h("h3", null, "Add"),
    buttons(btn("+ Text", () => ed.addText()), btn("+ Sticker", () => ed.addSticker()), btn("+ Zoom", () => ed.addZoom()),
      !(p.hook.text || "").trim() ? btn("+ Hook title", () => ed.addHook()) : null,
      btn(p.music.file ? "Music" : "+ Music", () => ed.select({ kind: "music", id: "music" }))),
    h("h3", null, "Captions"),
    buttons(btn("Style, size and position", () => ed.select({ kind: "captions", id: "captions" })), btn("AI fix captions", () => ed.aiFix())),
    h("h3", null, "Pauses"),
    slider(ed, "Shortest pause to cut", p.edit.min_gap, 0.3, 2, 0.1, (v) => { p.edit.min_gap = v; }, (v) => `${v.toFixed(1)}s`, "mingap"),
    buttons(btn("Cut out pauses", () => ed.removePauses(), "alt")),
    h("p", { class: "hint" }, "Splits the video track around every silence. Undo (Ctrl+Z) brings them back."),
    h("h3", null, "Effects"),
    check("fx-sfx", "Whoosh on each zoom, pop on each sticker", null, fx.sfx, (v) => ed.change(() => { fx.sfx = v; })),
    check("fx-bar", "Progress bar along the top", null, fx.progress, (v) => ed.change(() => { fx.progress = v; })),
    buttons(btn("Choose zooms again", () => ed.autoEffects("zooms")), btn("Choose stickers again", () => ed.autoEffects("emojis"))),
    h("h3", null, "Shortcuts"),
    h("ul", { class: "keys" },
      h("li", null, h("kbd", null, "Space"), " play / pause"),
      h("li", null, h("kbd", null, "S"), " split the clip at the playhead"),
      h("li", null, h("kbd", null, "Delete"), " delete the selection"),
      h("li", null, h("kbd", null, "Ctrl+Z"), " undo, ", h("kbd", null, "Ctrl+Shift+Z"), " redo"),
      h("li", null, h("kbd", null, "Ctrl+D"), " duplicate"),
      h("li", null, h("kbd", null, "←"), " ", h("kbd", null, "→"), " one frame (with Shift: one second); with something on the preview selected: nudge it"),
      h("li", null, h("kbd", null, "Ctrl"), " + wheel: zoom the timeline")));
}
