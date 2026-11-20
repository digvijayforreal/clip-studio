/* The facecam and gameplay boxes: drawn on a still of the source video. Drag a box to move it, drag its
   corner to resize it. Boxes are shares of the source picture (0 to 1), the same numbers the renderer uses. */
import { clamp, h } from "../lib.js";
import * as M from "./model.js";

const MIN = 0.04;

export function openBoxes(ed) {
  const p = ed.p;
  const video = ed.preview.video;
  const vw = video.videoWidth || p.source.width || 1920;
  const vh = video.videoHeight || p.source.height || 1080;
  const still = h("canvas", { class: "bx-still", width: vw, height: vh, "aria-hidden": "true" });
  try { still.getContext("2d").drawImage(video, 0, 0, vw, vh); } catch { /* not loaded yet: the boxes still work */ }
  const stage = h("div", { class: "bx-stage", style: { aspectRatio: `${vw} / ${vh}` } }, still);
  const boxes = {};
  const names = { face: "Facecam", game: "Gameplay" };

  const current = (which) => which === "face"
    ? (p.frame.face || ed.meta.face_presets[p.frame.cam_corner] || ed.meta.face_presets.tl)
    : (p.frame.game || { x: 0, y: 0, w: 1, h: 1 });

  const place = () => {
    for (const which of ["game", "face"]) {
      const b = current(which);
      Object.assign(boxes[which].style, { left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` });
    }
  };

  for (const which of ["game", "face"]) {
    const el = h("div", { class: `bx-box ${which}`, tabindex: 0, role: "group", "aria-label": `${names[which]} box. Drag to move, drag the corner to resize.` },
      h("span", { class: "bx-label" }, names[which]), h("div", { class: "bx-handle", title: "Drag to resize" }));
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const rect = stage.getBoundingClientRect();
      const start = { ...current(which) };
      const resize = e.target.classList.contains("bx-handle");
      ed.beginGesture();
      const move = (ev) => {
        const dx = (ev.clientX - e.clientX) / rect.width, dy = (ev.clientY - e.clientY) / rect.height;
        const b = { ...start };
        if (resize) {
          b.w = clamp(start.w + dx, MIN, 1 - start.x);
          b.h = clamp(start.h + dy, MIN, 1 - start.y);
        } else {
          b.x = clamp(start.x + dx, 0, 1 - start.w);
          b.y = clamp(start.y + dy, 0, 1 - start.h);
        }
        for (const k of ["x", "y", "w", "h"]) b[k] = M.r4(b[k]);
        p.frame[which] = b;
        ed.touched();
        place();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        ed.endGesture();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
    boxes[which] = el;
    stage.append(el);
  }
  place();

  const preset = (corner) => {
    ed.change(() => { p.frame.face = { ...ed.meta.face_presets[corner] }; p.frame.cam_corner = corner; });
    place();
  };
  const dialog = h("dialog", { class: "bx-dialog", "aria-label": "Facecam and gameplay boxes" },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, "Where are the facecam and the game?"), h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn small go", onclick: () => dialog.close() }, "Done")),
    h("p", { class: "muted small" }, "Drag the pink box over the streamer's webcam and the teal box over the part of the game to show. Drag a box's corner to resize it. The preview behind updates as you go."),
    stage,
    h("div", { class: "actions" },
      h("span", { class: "muted small" }, "Facecam is in the:"),
      [["tl", "Top left"], ["tr", "Top right"], ["bl", "Bottom left"], ["br", "Bottom right"]].map(([c, label]) =>
        h("button", { type: "button", class: "btn small", onclick: () => preset(c) }, label)),
      h("button", { type: "button", class: "btn small", onclick: () => { ed.change(() => { p.frame.game = { x: 0, y: 0, w: 1, h: 1 }; }); place(); } }, "Game: whole picture")));
  dialog.addEventListener("close", () => { dialog.remove(); ed.refreshInspector(); });
  document.body.append(dialog);
  dialog.showModal();
}
