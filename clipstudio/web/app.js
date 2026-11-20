/* ClipStudio: the page shell (HUD, jobs, missions, new heist, settings). The editor lives in editor/. */
import { $, api, banner, check, clock, h, layoutChips, state, styleChips, toast, upload } from "./lib.js";
import { openEditor } from "./editor/editor.js";

const view = $("#view");
let editor = null;

// ------------------------------------------------------------------ HUD
function tickClock() {
  const d = new Date();
  $("#clock").textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function setStars(active) {
  const stars = $("#stars");
  stars.replaceChildren(...[0, 1, 2, 3, 4].map((i) => h("span", { class: "star" + (i < active ? " on" : "") }, "★")));
  stars.setAttribute("aria-label", active ? `${active} job${active > 1 ? "s" : ""} running or waiting` : "No jobs running");
}
function setCash() {
  $("#cash").textContent = String(state.projects.filter((p) => p.has_clip).length).padStart(8, "0");
}
async function refreshProjects() {
  try { state.projects = await api("GET", "/api/projects"); } catch { /* shown elsewhere */ }
  setCash();
  return state.projects;
}

// ------------------------------------------------------------------ jobs
const QUIET_JOBS = new Set(["prepare"]);

async function pollJobs() {
  let next = 4000;
  try {
    const jobs = await api("GET", "/api/jobs");
    state.jobs = jobs;
    const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
    if (active.length) next = 1000;
    setStars(active.filter((j) => !QUIET_JOBS.has(j.kind)).length);
    let finished = false;
    for (const job of jobs) {
      const before = state.seen.get(job.id);
      if (before && before !== job.status && (job.status === "done" || job.status === "error")) {
        finished = true;
        if (!QUIET_JOBS.has(job.kind)) {
          if (job.status === "done") banner("pass", "Mission passed!", job.kind === "make" ? "Clips ready" : "Respect +");
          else banner("fail", "Wasted", "The job failed");
        }
        for (const fn of state.jobListeners) { try { fn(job); } catch (e) { console.error(e); } }
      }
      state.seen.set(job.id, job.status);
    }
    if (finished) {
      await refreshProjects();
      if (currentPage() === "missions") renderMissions();
    }
    renderTray();
  } catch { next = 5000; }
  setTimeout(pollJobs, next);
}
state.pollSoon = () => setTimeout(pollJobs, 300);

function renderTray() {
  const tray = $("#tray");
  const now = Date.now() / 1000;
  const show = state.jobs.filter((j) => !state.dismissed.has(j.id) && !(QUIET_JOBS.has(j.kind) && j.status === "done") &&
    (j.status === "queued" || j.status === "running" || (j.finished && now - j.finished < (j.status === "error" ? 600 : 90)))).slice(0, 3);
  const open = new Set([...tray.querySelectorAll(".job.open")].map((el) => Number(el.dataset.id)));
  tray.replaceChildren(...show.map((job) => {
    const pct = Math.round(job.progress * 100);
    return h("div", { class: `job ${job.status}${open.has(job.id) ? " open" : ""}`, "data-id": job.id },
      h("div", { class: "row" },
        h("div", { class: "job-title", title: job.title }, job.title),
        job.status === "done" || job.status === "error"
          ? h("button", { class: "btn small", "aria-label": "Dismiss", onclick: () => { state.dismissed.add(job.id); renderTray(); } }, "✕")
          : null),
      h("div", { class: "job-stage" },
        job.status === "queued" ? "Waiting for the job before it" :
        job.status === "error" ? job.error : `${job.stage}${job.status === "running" ? ` — ${pct}%` : ""}`),
      job.status === "running" ? h("div", { class: "bar", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
        h("i", { style: { width: pct + "%" } })) : null,
      h("pre", { class: "job-log" }, job.log.join("\n")),
      h("div", { class: "job-actions" },
        h("button", { class: "btn small", onclick: (e) => e.target.closest(".job").classList.toggle("open") }, "Log"),
        job.status === "done" && job.result.length && job.kind === "make"
          ? job.result.slice(0, 3).map((name) => h("a", { class: "btn small alt", href: `#/edit/${encodeURIComponent(name)}` }, job.result.length > 1 ? `Open ${name.slice(-14)}` : "Open"))
          : null));
  }));
}

// ------------------------------------------------------------------ router
function currentPage() { return (location.hash || "#/missions").split("/")[1] || "missions"; }

async function route() {
  if (editor) { editor.destroy(); editor = null; }
  document.body.classList.remove("editing");
  const [, page, arg, sub] = (location.hash || "#/missions").split("/");
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === (page || "missions")));
  if (page === "new") renderNew();
  else if (page === "settings") renderSettings();
  else if (page === "edit" && arg) {
    document.body.classList.add("editing");
    editor = await openEditor(view, decodeURIComponent(arg), sub);
  } else renderMissions();
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);
window.addEventListener("beforeunload", (e) => {
  if (editor && editor.unsaved()) { editor.flush(); e.preventDefault(); e.returnValue = ""; }
});

// ------------------------------------------------------------------ missions
async function renderMissions() {
  const list = await refreshProjects();
  if (currentPage() !== "missions") return;
  const cards = list.map((p) => {
    const thumb = p.has_clip ? `/api/projects/${encodeURIComponent(p.name)}/media/thumb?v=${Math.round(p.updated)}` : null;
    return h("article", { class: "mission" },
      h("a", { class: "mission-thumb", href: `#/edit/${encodeURIComponent(p.name)}`, "aria-label": `Edit ${p.title}`,
               style: thumb ? { backgroundImage: `url("${thumb}")` } : {} },
        p.busy ? h("span", { class: "tag busy" }, "In progress") : !p.has_clip ? h("span", { class: "tag" }, "Not rendered") : null),
      h("div", { class: "mission-body" },
        h("a", { class: "mission-title", href: `#/edit/${encodeURIComponent(p.name)}` }, p.title),
        p.broken ? h("div", { class: "mission-meta" }, "This project file can't be read.") :
          h("div", { class: "mission-meta" },
            `${clock(p.length)} · ${p.parts} clip${p.parts > 1 ? "s" : ""} · ${p.style} · ${p.language === "hi" ? "Hindi" : p.language === "en" ? "English" : p.language}`),
        p.hook ? h("div", { class: "mission-meta" }, `“${p.hook}”`) : null,
        h("div", { class: "mission-actions" },
          h("a", { class: "btn small go", href: `#/edit/${encodeURIComponent(p.name)}` }, "Edit"),
          p.has_clip ? h("a", { class: "btn small", href: `/api/projects/${encodeURIComponent(p.name)}/media/clip`, download: `${p.name}.mp4` }, "Download") : null,
          h("button", { class: "btn small warn", onclick: () => deleteProject(p) }, "Delete"))));
  });
  view.replaceChildren(
    h("h1", null, "Missions"),
    h("p", { class: "lead" }, "Every clip you've made. Open one in the editor to trim, rearrange, move captions and add text, then render it again."),
    list.length ? h("div", { class: "missions" }, cards) :
      h("div", { class: "panel empty" },
        h("span", { class: "big-word" }, "No missions yet"),
        h("p", { class: "muted" }, "Start your first heist: paste a YouTube link and the parts you want."),
        h("a", { class: "btn go big", href: "#/new" }, "New heist")));
}

async function deleteProject(p) {
  if (!confirm(`Delete "${p.title}"?\n\nThis removes its folder, the downloaded video and the finished clip. It can't be undone.`)) return;
  try { await api("DELETE", `/api/projects/${encodeURIComponent(p.name)}`); toast("Deleted.", true); renderMissions(); }
  catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ new heist
function renderNew() {
  const form = { source: "", ranges: "", lang: "auto", layout: "blur", style: state.meta.styles[0].id, join: false,
                 cut_pauses: false, min_gap: 0.7, hook: "", hook_seconds: "", zoom: true, emoji: true, sfx: true,
                 progress: true, accurate: false, ai_hook: false, ai_fix: false };
  const aiOk = state.meta.ai_ready;
  const fileStatus = h("span", { class: "hint", id: "file-status" }, "");
  const sourceInput = h("input", { type: "url", id: "source", placeholder: "https://www.youtube.com/watch?v=...", autocomplete: "off",
                                   oninput: (e) => { form.source = e.target.value.trim(); } });
  const fileInput = h("input", { type: "file", id: "file", accept: "video/*", class: "sr-only",
    onchange: async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const data = await upload(`/api/upload?filename=${encodeURIComponent(file.name)}`, file,
          (f) => { fileStatus.textContent = `Uploading ${Math.round(f * 100)}%…`; });
        fileStatus.textContent = `Ready: ${file.name}`;
        form.source = data.path; sourceInput.value = data.path;
      } catch (err) { fileStatus.textContent = ""; toast(err.message); }
    } });
  const start = h("button", { class: "btn go big", type: "submit" }, "Start heist");

  const el = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      if (!form.source) { toast("Paste a YouTube link or choose a video file."); sourceInput.focus(); return; }
      if (!form.ranges.trim()) { toast("Write the parts to cut, like 1:23-2:05."); $("#ranges").focus(); return; }
      start.disabled = true;
      try {
        const body = { ...form, hook_seconds: form.hook_seconds === "" ? null : Number(form.hook_seconds) };
        await api("POST", "/api/make", body);
        toast("Heist started. Watch the progress in the bottom right.", true);
        location.hash = "#/missions";
      } catch (err) { toast(err.message); start.disabled = false; }
    } },
    h("h1", null, "New heist"),
    h("p", { class: "lead" }, "Paste a link, say which parts to cut, pick the look. ClipStudio downloads only those parts, writes word-by-word captions and renders 9:16 clips. Fine-tune everything in the editor afterwards."),
    h("div", { class: "grid2" },
      h("section", { class: "panel" },
        h("h2", null, "The target"),
        h("label", { for: "source" }, "YouTube link", h("span", { class: "hint" }, "Or a video file on this computer.")),
        sourceInput,
        h("div", { class: "row", style: { marginTop: "8px" } },
          h("label", { for: "file", class: "btn small", style: { margin: 0 } }, "Choose a video file"), fileInput, fileStatus),
        h("label", { for: "ranges" }, "Parts to cut",
          h("span", { class: "hint" }, "One per line or separated by commas: 1:23-2:05, 12:10-12:55. Hours work too: 1:02:03-1:02:45.")),
        h("textarea", { id: "ranges", placeholder: "1:23-2:05", oninput: (e) => { form.ranges = e.target.value; } }),
        check("join", "Join all parts into one clip", "Played one after the other. Off: one clip per part.", false, (v) => { form.join = v; }),
        h("label", { for: "lang" }, "Spoken language"),
        h("select", { id: "lang", onchange: (e) => { form.lang = e.target.value; } },
          h("option", { value: "auto" }, "Find out (Hindi, English or Hinglish)"),
          h("option", { value: "hi" }, "Hindi / Hinglish"),
          h("option", { value: "en" }, "English")),
        check("accurate", "Extra-careful speech to text", "About twice as slow. Can get a few more words right.", false, (v) => { form.accurate = v; })),
      h("section", { class: "panel" },
        h("h2", null, "The look"),
        h("label", null, "Caption style"), styleChips(form.style, (v) => { form.style = v; }),
        h("label", null, "Layout"), layoutChips(form.layout, (v) => { form.layout = v; }),
        h("label", { for: "hook" }, "Hook title", h("span", { class: "hint" }, "Shown in a white box at the top. Leave empty for none.")),
        h("input", { type: "text", id: "hook", maxlength: 120, placeholder: "WHY NOBODY TELLS YOU THIS", oninput: (e) => { form.hook = e.target.value; } }),
        h("label", { for: "hook_seconds" }, "Hook stays up for (seconds)", h("span", { class: "hint" }, "Empty: the whole clip.")),
        h("input", { type: "number", id: "hook_seconds", min: 0.5, step: 0.5, oninput: (e) => { form.hook_seconds = e.target.value; } }),
        h("h3", null, "Effects"),
        check("zoom", "Zoom punch-ins", "Jumps in on every other phrase.", true, (v) => { form.zoom = v; }),
        check("emoji", "Emoji on key words", "Money 💰, time ⏰, funny 😂… in English and Hindi.", true, (v) => { form.emoji = v; }),
        check("sfx", "Whoosh and pop sounds", null, true, (v) => { form.sfx = v; }),
        check("progress", "Progress bar", null, true, (v) => { form.progress = v; }),
        check("cut_pauses", "Cut out pauses (jump cuts)", "Removes silences longer than 0.7 s.", false, (v) => { form.cut_pauses = v; }),
        h("h3", null, "AI extras"),
        !aiOk ? h("p", { class: "hint" }, "Add a free AI key in ", h("a", { href: "#/settings" }, "Settings"), " to use these.") : null,
        check("ai_fix", "AI caption clean-up", "Fixes spellings, names and numbers.", false, (v) => { form.ai_fix = v; }, !aiOk),
        check("ai_hook", "AI writes the hook", "Only when you leave the hook empty.", false, (v) => { form.ai_hook = v; }, !aiOk))),
    h("div", { class: "actions" }, start));
  view.replaceChildren(el);
  sourceInput.focus();
}

// ------------------------------------------------------------------ settings
async function renderSettings() {
  let s;
  try { s = await api("GET", "/api/settings"); } catch (e) { toast(e.message); return; }
  const fields = { base_url: s.ai.base_url, model: s.ai.model, key: "" };
  view.replaceChildren(
    h("h1", null, "Settings"),
    h("p", { class: "lead" }, "Everything works without AI. A key only adds caption clean-up and hook ideas."),
    h("section", { class: "panel" },
      h("h2", null, "AI service"),
      h("p", { class: "muted" }, "Any service that speaks the OpenAI chat format. The default is Groq, which has a free plan: make a key at ",
        h("a", { href: "https://console.groq.com/keys", target: "_blank", rel: "noopener" }, "console.groq.com/keys"), "."),
      h("label", { for: "ai-url" }, "Address"),
      h("input", { type: "url", id: "ai-url", value: fields.base_url, oninput: (e) => { fields.base_url = e.target.value; } }),
      h("label", { for: "ai-model" }, "Model", h("span", { class: "hint" }, "openai/gpt-oss-120b on Groq. For OpenAI: gpt-6-luna.")),
      h("input", { type: "text", id: "ai-model", value: fields.model, oninput: (e) => { fields.model = e.target.value; } }),
      h("label", { for: "ai-key" }, "Key", h("span", { class: "hint" }, "Kept in settings.json in the ClipStudio folder. Leave empty to keep the saved one.")),
      h("input", { type: "password", id: "ai-key", autocomplete: "off", oninput: (e) => { fields.key = e.target.value; } }),
      h("p", { class: "muted", "aria-live": "polite" },
        s.ai.has_key ? `A key is saved${s.ai.key_hint ? ` (ends in ${s.ai.key_hint})` : ""}.` : "No key saved yet."),
      h("div", { class: "actions" },
        h("button", { class: "btn go", onclick: async () => {
            try {
              const body = { ai: { base_url: fields.base_url, model: fields.model } };
              if (fields.key.trim()) body.ai.key = fields.key.trim();
              const r = await api("PUT", "/api/settings", body);
              state.meta.ai_ready = r.ai_ready;
              toast("Saved.", true);
              renderSettings();
            } catch (e) { toast(e.message); }
          } }, "Save"),
        h("button", { class: "btn", onclick: async (e) => {
            e.target.disabled = true;
            try { const r = await api("POST", "/api/settings/test"); toast(r.ok ? "The AI answered. You're set." : `Odd answer: ${r.answer}`, r.ok); }
            catch (err) { toast(err.message); }
            e.target.disabled = false;
          } }, "Test"),
        s.ai.has_key ? h("button", { class: "btn warn", onclick: async () => {
            if (!confirm("Remove the saved key?")) return;
            try { const r = await api("PUT", "/api/settings", { ai: { key: "" } }); state.meta.ai_ready = r.ai_ready; renderSettings(); }
            catch (e) { toast(e.message); }
          } }, "Remove key") : null)),
    h("section", { class: "panel" },
      h("h2", null, "Where things are"),
      h("p", null, "Projects and clips: ", h("code", null, state.meta.projects_dir)),
      h("p", { class: "muted" }, `ClipStudio ${state.meta.version}. It only answers on this computer (127.0.0.1) and has no sign-in, so don't open it to a network.`)));
}

// ------------------------------------------------------------------ start
(async function boot() {
  tickClock(); setInterval(tickClock, 15000);
  setStars(0);
  try { state.meta = await api("GET", "/api/meta"); }
  catch (e) { view.replaceChildren(h("h1", null, "Can't reach ClipStudio"), h("p", null, e.message)); return; }
  try { (await api("GET", "/api/jobs")).forEach((j) => state.seen.set(j.id, j.status)); } catch { /* first poll fills it */ }
  refreshProjects();
  route();
  pollJobs();
})();
