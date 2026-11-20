/* The editor's model: pure functions on a project (the same JSON the server renders). No DOM in here.

   Two clocks:
     source time   seconds in the downloaded video (clip in/out points, caption words)
     timeline time seconds in the finished clip (texts, stickers, zooms, the playhead)
   The video track is "magnetic", like CapCut's main track: clips play one after the other in list order. */

export const MIN_CLIP = 0.1;
export const HOLD = 0.35;                      // a caption line stays up this long after its last word
const BREAK_AFTER = [".", "?", "!", "।", "॥", ",", ";", ":"];
const WEAK_END = new Set(("a an the of to in on at for and or but with from by as into is are was my your our their " +
  "his her its this i we so if और कि लेकिन या जो").split(" "));

export const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export const r4 = (x) => Math.round(x * 10000) / 10000;
export const snapFrame = (t, fps) => r4(Math.round(t * fps) / fps);

let counter = 0;
export function uid(prefix) {
  counter += 1;
  return `${prefix}${Date.now().toString(36).slice(-4)}${counter.toString(36)}${Math.random().toString(36).slice(2, 5)}`;
}

/** Fills in what older projects lack and gives every item a unique id. Mutates and returns p. */
export function normalize(p) {
  const seen = new Set();
  const fix = (item, prefix) => {
    if (!item.id || seen.has(item.id)) item.id = uid(prefix);
    seen.add(item.id);
  };
  for (const s of p.segments) { fix(s, "c"); if (s.volume == null) s.volume = 1; }
  p.texts = Array.isArray(p.texts) ? p.texts : [];
  p.texts.forEach((t) => fix(t, "t"));
  if (Array.isArray(p.effects.zooms)) p.effects.zooms.forEach((z) => fix(z, "z"));
  if (Array.isArray(p.effects.emojis)) p.effects.emojis.forEach((e) => fix(e, "e"));
  if (p.captions.x == null) p.captions.x = 0.5;
  if (p.captions.scale == null) p.captions.scale = 1;
  if (p.hook.y === undefined) p.hook.y = null;
  return p;
}

// ------------------------------------------------------------------ the video track

/** The video track's clips with where each sits on the timeline. */
export function clips(p) {
  let at = 0;
  return p.segments.map((s, index) => {
    const length = s.end - s.start;
    const c = { seg: s, id: s.id, index, start: at, end: at + length, in: s.start, out: s.end, length };
    at += length;
    return c;
  });
}

export const totalLength = (p) => p.segments.reduce((n, s) => n + (s.end - s.start), 0);

/** The clip playing at timeline time T (the last clip when T is past the end). */
export function clipAt(list, T) {
  if (!list.length) return null;
  for (const c of list) if (T < c.end) return c;
  return list[list.length - 1];
}

/** Timeline time -> {clip, t: source time}. */
export function toSource(list, T) {
  const clip = clipAt(list, T);
  if (!clip) return null;
  return { clip, t: r4(clamp(clip.in + (T - clip.start), clip.in, clip.out)) };
}

/** Source time -> every timeline time that shows it (a stretch can be played twice). */
export function toTimeline(list, t) {
  return list.filter((c) => t >= c.in && t < c.out).map((c) => r4(c.start + (t - c.in)));
}

/** Cuts the clip under timeline time T in two. Returns the new (right-hand) clip's id, or null. */
export function splitAt(p, T, fps) {
  const list = clips(p);
  const c = list.find((x) => T > x.start + 1e-6 && T < x.end - 1e-6);
  if (!c) return null;
  const cut = snapFrame(c.in + (T - c.start), fps);
  if (cut - c.in < 2 / fps || c.out - cut < 2 / fps) return null;
  const right = { ...c.seg, id: uid("c"), start: cut };
  c.seg.end = cut;
  p.segments.splice(c.index + 1, 0, right);
  return right.id;
}

export function trimIn(seg, value, fps) {
  seg.start = r4(clamp(snapFrame(value, fps), 0, seg.end - MIN_CLIP));
}

export function trimOut(seg, value, fps, sourceDuration) {
  seg.end = r4(clamp(snapFrame(value, fps), seg.start + MIN_CLIP, sourceDuration || Infinity));
}

export function moveClip(p, id, index) {
  const from = p.segments.findIndex((s) => s.id === id);
  if (from < 0) return false;
  const [seg] = p.segments.splice(from, 1);
  p.segments.splice(clamp(index, 0, p.segments.length), 0, seg);
  return from !== p.segments.indexOf(seg);
}

/** Where a dragged clip lands: the number of other clips whose middle is left of the drop point. */
export function dropIndex(p, id, centerT) {
  return clips(p).filter((c) => c.id !== id && c.start + c.length / 2 < centerT).length;
}

export function duplicateClip(p, id) {
  const i = p.segments.findIndex((s) => s.id === id);
  if (i < 0) return null;
  const copy = { ...p.segments[i], id: uid("c") };
  p.segments.splice(i + 1, 0, copy);
  return copy.id;
}

export function deleteClip(p, id) {
  if (p.segments.length <= 1) return false;
  const i = p.segments.findIndex((s) => s.id === id);
  if (i < 0) return false;
  p.segments.splice(i, 1);
  return true;
}

// ------------------------------------------------------------------ captions

export function shown(word, upper) {
  let t = word.replace(/[{}\\]/g, "").trim();
  t = t.replace(/[.,;:।॥]+$/u, "") || t;
  return upper ? t.toUpperCase() : t;
}

/** Roughly how many letters wide a word is (Hindi vowel signs above or below a letter add nothing). */
export const units = (t) => [...t].filter((ch) => !/[\p{M}\p{Cf}]/u.test(ch)).length;

/** The hook title in lines, the way the renderer splits it (captions.hook_lines). */
export function hookLines(text, maxUnits, maxLines) {
  const lines = [];
  for (const word of String(text || "").replace(/[{}\\]/g, "").trim().split(/\s+/).filter(Boolean)) {
    if (lines.length && units(lines[lines.length - 1]) + 1 + units(word) <= maxUnits) lines[lines.length - 1] += " " + word;
    else lines.push(word);
  }
  return lines.length > maxLines ? [...lines.slice(0, maxLines - 1), lines.slice(maxLines - 1).join(" ")] : lines;
}

/** The caption size for a line, shrunk like the renderer does when the line is too wide. */
export function lineSize(line, style, scale) {
  const base = Math.max(48, Math.round(style.size * (scale || 1)));
  const width = line.words.reduce((n, w) => n + units(w.t), 0);
  return width > style.max_units ? Math.max(48, Math.floor((base * style.max_units) / width)) : base;
}
const keyOf = (w) => w.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/** Words into caption lines, the same way the renderer does it (captions.group_words). */
export function groupWords(words, maxWords, maxUnits) {
  const groups = [];
  let cur = [];
  let width = 0;
  for (const w of words) {
    const u = units(w.t);
    const last = cur[cur.length - 1];
    const natural = cur.length && (w.s - last.e > 0.5 || BREAK_AFTER.some((p) => last.w.trimEnd().endsWith(p)));
    const full = cur.length && (cur.length >= maxWords || width + u > maxUnits);
    if (natural || full) {
      let carry = [];
      if (full && !natural && cur.length > 1 && WEAK_END.has(keyOf(last.w))) carry = [cur.pop()];
      groups.push(cur);
      cur = carry;
      width = carry.reduce((n, x) => n + units(x.t), 0);
      if (cur.length && (cur.length >= maxWords || width + u > maxUnits)) { groups.push(cur); cur = []; width = 0; }
    }
    cur.push(w);
    width += u;
  }
  if (cur.length) groups.push(cur);
  return groups;
}

/** The caption lines on the timeline. Each word: {i (index in captions.words), w, t (as shown), s, e, clip}. */
export function captionLines(p, style) {
  const list = clips(p);
  const all = [];
  p.captions.words.forEach((w, i) => {
    const t = shown(w.w, style.upper);
    if (!t) return;
    for (const c of list) {
      if (w.s >= c.in - 0.05 && w.s < c.out) {
        const s = r4(c.start + Math.max(0, w.s - c.in));
        const e = r4(c.start + Math.min(c.length, Math.max(w.e, w.s) - c.in));
        all.push({ i, w: w.w, t, s, e: Math.max(s, e), clip: c });
      }
    }
  });
  all.sort((a, b) => a.s - b.s);
  const total = list.length ? list[list.length - 1].end : 0;
  const lines = groupWords(all, style.max_words, style.max_units).map((g) => ({
    key: `${g[0].clip.id}:${g[0].i}`, words: g, start: g[0].s, end: g[g.length - 1].e,
  }));
  lines.forEach((line, k) => {
    const next = k + 1 < lines.length ? lines[k + 1].start : total;
    line.shownEnd = r4(Math.min(line.end + HOLD, Math.max(next, line.end), total));
  });
  return lines;
}

/** The word being said at timeline time T in a line: the last one that has started. */
export function activeWord(line, T) {
  let k = 0;
  line.words.forEach((w, i) => { if (T >= w.s) k = i; });
  return k;
}

/** Moves a line's words by `delta` seconds, from their `orig` times ({i: {s, e}}), never out of their clip. */
export function shiftLine(p, line, orig, delta) {
  let low = -Infinity;
  let high = Infinity;
  for (const w of line.words) {
    const o = orig[w.i];
    low = Math.max(low, w.clip.in - o.s);
    high = Math.min(high, w.clip.out - o.e);
  }
  const d = clamp(delta, Math.min(0, low), Math.max(0, high));
  for (const i of Object.keys(orig)) {
    const w = p.captions.words[i];
    w.s = r4(orig[i].s + d);
    w.e = r4(orig[i].e + d);
  }
  return d;
}

/** Replaces a line's text. The same number of words keeps every word's timing; otherwise the new words share
    the line's time evenly. Returns the index of the line's first word afterwards (-1 when it was emptied). */
export function setLineText(p, line, text) {
  const words = p.captions.words;
  const parts = text.trim().split(/\s+/).filter(Boolean);
  const idx = [...new Set(line.words.map((w) => w.i))].sort((a, b) => words[a].s - words[b].s);
  if (parts.length === idx.length) {
    idx.forEach((i, k) => { words[i].w = parts[k]; });
    return idx[0];
  }
  const s0 = words[idx[0]].s;
  const e1 = Math.max(...idx.map((i) => words[i].e), s0 + 0.2 * Math.max(1, parts.length));
  const at = Math.min(...idx);
  [...idx].sort((a, b) => b - a).forEach((i) => words.splice(i, 1));
  if (!parts.length) return -1;
  const step = (e1 - s0) / parts.length;
  const fresh = parts.map((w, k) => ({ w, s: r4(s0 + k * step), e: r4(s0 + (k + 1) * step), p: 1 }));
  words.splice(at, 0, ...fresh);
  return at;
}

export function deleteLine(p, line) {
  const idx = [...new Set(line.words.map((w) => w.i))].sort((a, b) => b - a);
  idx.forEach((i) => p.captions.words.splice(i, 1));
}

// ------------------------------------------------------------------ snapping

/** The nearest of `points` within `threshold` of t, or t itself. */
export function snapTo(t, points, threshold) {
  let best = t;
  let gap = threshold;
  for (const x of points) {
    const d = Math.abs(x - t);
    if (d < gap) { gap = d; best = x; }
  }
  return best;
}

// ------------------------------------------------------------------ undo

export class History {
  constructor(limit = 150) { this.past = []; this.future = []; this.limit = limit; this.lastKey = null; this.lastAt = 0; }

  /** Remembers `snapshot` (the project before a change). Changes with the same key in quick succession, like
      typing or dragging a slider, are one step. */
  record(snapshot, key = null) {
    const now = Date.now();
    if (key && key === this.lastKey && now - this.lastAt < 1500) { this.lastAt = now; return; }
    this.past.push(snapshot);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
    this.lastKey = key;
    this.lastAt = now;
  }

  undo(current) {
    if (!this.past.length) return null;
    this.future.push(current);
    this.lastKey = null;
    return this.past.pop();
  }

  redo(current) {
    if (!this.future.length) return null;
    this.past.push(current);
    this.lastKey = null;
    return this.future.pop();
  }

  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
}
