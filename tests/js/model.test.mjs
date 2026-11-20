// Run with: node --test tests/js
import assert from "node:assert/strict";
import { test } from "node:test";
import * as M from "../../clipstudio/web/editor/model.js";

const style = { upper: true, max_words: 3, max_units: 13 };

function project(segments, words = []) {
  return M.normalize({
    segments: segments.map(([start, end], i) => ({ id: `c${i + 1}`, start, end })),
    captions: { words, x: 0.5, scale: 1 }, effects: { zooms: [], emojis: [] }, hook: { text: "" }, texts: [],
  });
}

test("clips sit one after the other", () => {
  const p = project([[10, 14], [2, 3]]);
  const list = M.clips(p);
  assert.deepEqual(list.map((c) => [c.start, c.end]), [[0, 4], [4, 5]]);
  assert.equal(M.totalLength(p), 5);
  assert.deepEqual(M.toSource(list, 4.5).t, 2.5);
  assert.equal(M.toSource(list, 99).clip.id, "c2");
  assert.deepEqual(M.toTimeline(list, 12), [2]);
});

test("split cuts on a frame and keeps the clip's settings", () => {
  const p = project([[10, 14]]);
  p.segments[0].volume = 0.5;
  const id = M.splitAt(p, 1.01, 30);
  assert.ok(id);
  assert.deepEqual(p.segments.map((s) => [s.start, s.end]), [[10, 11], [11, 14]]);
  assert.equal(p.segments[1].volume, 0.5);
  assert.equal(M.splitAt(p, 1.0, 30), null);                  // right on a cut: nothing to split
  assert.equal(M.splitAt(p, 0.02, 30), null);                 // too close to the edge
});

test("trims stay inside the source and keep a minimum length", () => {
  const seg = { start: 10, end: 14 };
  M.trimIn(seg, 13.99, 30);
  assert.equal(seg.start, 13.9);
  M.trimIn(seg, -5, 30);
  assert.equal(seg.start, 0);
  M.trimOut(seg, 99, 30, 20);
  assert.equal(seg.end, 20);
});

test("move, duplicate and delete clips", () => {
  const p = project([[0, 1], [1, 2], [2, 3]]);
  assert.equal(M.dropIndex(p, "c1", 2.6), 2);
  M.moveClip(p, "c1", 2);
  assert.deepEqual(p.segments.map((s) => s.id), ["c2", "c3", "c1"]);
  const copy = M.duplicateClip(p, "c3");
  assert.deepEqual(p.segments.map((s) => s.id), ["c2", "c3", copy, "c1"]);
  assert.ok(M.deleteClip(p, copy));
  const one = project([[0, 1]]);
  assert.equal(M.deleteClip(one, "c1"), false);               // the last clip stays
});

test("caption lines follow the clips and match the renderer's grouping", () => {
  const words = [
    { w: "regularly", s: 10.0, e: 10.5 }, { w: "on", s: 10.5, e: 10.7 }, { w: "a", s: 10.7, e: 10.8 },
    { w: "24-hour", s: 10.8, e: 11.2 }, { w: "cycle.", s: 11.2, e: 11.5 }, { w: "outside", s: 20, e: 20.4 },
  ];
  const p = project([[10, 12]], words);
  const lines = M.captionLines(p, style);
  assert.deepEqual(lines.map((l) => l.words.map((w) => w.t)), [["REGULARLY", "ON"], ["A", "24-HOUR", "CYCLE"]]);
  assert.equal(lines[1].start, 0.7);
  assert.equal(lines[1].shownEnd, 1.85);
  assert.equal(M.activeWord(lines[1], 1.0), 1);
});

test("a clip played twice shows its captions twice", () => {
  const p = project([[10, 11], [10, 11]], [{ w: "again", s: 10.2, e: 10.6 }]);
  assert.deepEqual(M.captionLines(p, style).map((l) => l.start), [0.2, 1.2]);
});

test("moving a line keeps it inside its clip", () => {
  const p = project([[10, 12]], [{ w: "hi", s: 10.2, e: 10.6 }, { w: "there", s: 10.6, e: 11.0 }]);
  const line = M.captionLines(p, style)[0];
  const orig = { 0: { s: 10.2, e: 10.6 }, 1: { s: 10.6, e: 11.0 } };
  assert.equal(M.shiftLine(p, line, orig, 0.5), 0.5);
  assert.equal(p.captions.words[0].s, 10.7);
  assert.equal(M.shiftLine(p, line, orig, 5), 1);              // stopped at the clip's end
  assert.equal(p.captions.words[1].e, 12);
});

test("editing a line's text", () => {
  const p = project([[0, 5]], [{ w: "helo", s: 1, e: 1.5 }, { w: "word", s: 1.5, e: 2 }, { w: "next", s: 3, e: 3.4 }]);
  let line = M.captionLines(p, style)[0];
  M.setLineText(p, line, "hello world");
  assert.deepEqual(p.captions.words.map((w) => [w.w, w.s]), [["hello", 1], ["world", 1.5], ["next", 3]]);
  line = M.captionLines(p, style)[0];
  M.setLineText(p, line, "hello big world");
  assert.deepEqual(p.captions.words.map((w) => w.w), ["hello", "big", "world", "next"]);
  assert.deepEqual(p.captions.words.slice(0, 3).map((w) => w.s), [1, 1.3333, 1.6667]);
  line = M.captionLines(p, style)[0];
  assert.equal(M.setLineText(p, line, "   "), -1);
  assert.deepEqual(p.captions.words.map((w) => w.w), ["next"]);
});

test("normalize gives every item a unique id", () => {
  const p = M.normalize({ segments: [{ start: 0, end: 1 }, { id: "x", start: 1, end: 2 }, { id: "x", start: 2, end: 3 }],
    captions: { words: [] }, effects: { zooms: [{ start: 0, end: 1 }], emojis: "auto" }, hook: {}, texts: [{ text: "a" }] });
  const ids = [...p.segments.map((s) => s.id), p.texts[0].id, p.effects.zooms[0].id];
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(p.segments[0].volume, 1);
});

test("long caption lines shrink like the renderer's", () => {
  const st = { size: 128, max_units: 13 };
  assert.equal(M.lineSize({ words: [{ t: "HI" }] }, st, 1), 128);
  assert.equal(M.lineSize({ words: [{ t: "HI" }] }, st, 1.5), 192);
  assert.equal(M.lineSize({ words: [{ t: "ANTIDISESTABLISHMENT" }] }, st, 1), 83);   // captions.py gives 83 too
});

test("the hook wraps like the renderer's", () => {
  assert.deepEqual(M.hookLines("WHY ASTRONAUTS CAN'T SLEEP", 17, 3), ["WHY ASTRONAUTS", "CAN'T SLEEP"]);
  assert.deepEqual(M.hookLines("a b c d", 1, 2), ["a", "b c d"]);
});

test("snapping and undo", () => {
  assert.equal(M.snapTo(1.04, [0, 1, 2], 0.1), 1);
  assert.equal(M.snapTo(1.5, [0, 1, 2], 0.1), 1.5);
  const hist = new M.History();
  hist.record("a");
  hist.record("b", "typing");
  hist.record("c", "typing");                                  // same gesture: no new step
  assert.equal(hist.undo("now"), "b");
  assert.equal(hist.undo("b"), "a");
  assert.equal(hist.undo("a"), null);
  assert.equal(hist.redo("a"), "b");
});
