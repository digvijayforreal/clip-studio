/* Shared helpers for the ClipStudio pages.
   Text from videos (titles, transcripts) is untrusted: it only ever reaches the page through textContent. */

export const state = {
  meta: null, projects: [], jobs: [], seen: new Map(), dismissed: new Set(),
  jobListeners: new Set(),          // functions called with each job that finishes
};

export const $ = (sel, root = document) => root.querySelector(sel);
export const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export const round3 = (x) => Math.round(x * 1000) / 1000;

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "disabled") el.disabled = !!v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

export async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* empty answer */ }
  if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
  return data;
}

let toastTimer = null;
export function toast(message, ok = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (ok ? " ok" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ok ? 3500 : 7000);
}

let bannerTimer = null;
export function banner(kind, word, sub) {
  const b = $("#banner");
  b.className = "banner " + kind;
  b.replaceChildren(h("div", { class: "inner" }, h("span", { class: "word" }, word), sub ? h("span", { class: "sub" }, sub) : null));
  b.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => { b.hidden = true; }, 2600);
}

/** 83.25 -> "1:23.2" */
export function clock(sec, digits = 1) {
  sec = Math.max(0, Number(sec) || 0);
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(digits).padStart(digits ? 3 + digits : 2, "0")}`;
}

export function check(id, label, hint, checked, onChange, disabled) {
  return h("label", { class: "check", for: id },
    h("input", { type: "checkbox", id, checked, disabled, onchange: (e) => onChange(e.target.checked) }),
    h("span", null, label, hint ? h("span", { class: "hint" }, hint) : null));
}

/** A row of buttons that behave like radio buttons. options: [{id, label, node?}] */
export function radioChips(options, current, onPick, label, extraClass = "") {
  const wrap = h("div", { class: "chips", role: "radiogroup", "aria-label": label });
  for (const opt of options) {
    const chip = h("button", { type: "button", class: `chip ${extraClass}` + (opt.id === current ? " on" : ""), role: "radio",
      "aria-checked": opt.id === current ? "true" : "false",
      onclick: () => {
        wrap.querySelectorAll(".chip").forEach((c) => { c.classList.remove("on"); c.setAttribute("aria-checked", "false"); });
        chip.classList.add("on"); chip.setAttribute("aria-checked", "true"); onPick(opt.id);
      } }, opt.node || null, opt.label);
    wrap.append(chip);
  }
  return wrap;
}

export function styleChips(current, onPick) {
  return radioChips(state.meta.styles.map((st) => ({
    id: st.id, label: st.label,
    node: h("span", { class: "style-sample" + (st.box ? " boxed" : "") },
      h("span", { style: { color: "#" + st.text } }, st.upper ? "SAY " : "Say "),
      h("span", { style: { color: "#" + st.active } }, st.upper ? "THIS" : "this")),
  })), current, onPick, "Caption style", "style-card");
}

export function layoutChips(current, onPick) {
  return radioChips(state.meta.layouts, current, onPick, "Layout");
}

export const emojiCode = (e) => [...e].map((c) => c.codePointAt(0).toString(16)).join("-");
export const emojiUrl = (e) => `/api/emoji/${emojiCode(e)}`;

/** PUT a file with upload progress. Resolves with the JSON answer. */
export function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 200) resolve(data); else reject(new Error(data.detail || "The upload failed."));
    };
    xhr.onerror = () => reject(new Error("The upload failed."));
    xhr.send(file);
  });
}
