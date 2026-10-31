import assert from "node:assert/strict";
import { test } from "node:test";
import { decideFold, type FoldInput } from "./foldable-model.ts";

function base(over: Partial<FoldInput> = {}): FoldInput {
  return {
    innerWidth: 390,
    innerHeight: 844,
    screenWidth: 390,
    screenHeight: 844,
    verticalSegments: 0,
    horizontalSegments: 0,
    posture: "unknown",
    native: null,
    preview: null,
    windowSegments: null,
    ...over,
  };
}

test("phone stays a phone", () => {
  assert.equal(decideFold(base()).fold, "phone");
});

test("iPad portrait stays a phone sheet", () => {
  const d = decideFold(base({ innerWidth: 834, innerHeight: 1194, screenWidth: 834, screenHeight: 1194 }));
  assert.equal(d.fold, "phone");
});

test("Fold inner portrait is a book without a hinge API", () => {
  const d = decideFold(base({ innerWidth: 794, innerHeight: 924, screenWidth: 794, screenHeight: 924 }));
  assert.equal(d.fold, "book");
});

test("wide flat window is not forced into a book", () => {
  const d = decideFold(base({ innerWidth: 1280, innerHeight: 800, screenWidth: 1280, screenHeight: 800 }));
  assert.equal(d.fold, "phone");
});

test("vertical viewport segments are flex", () => {
  const d = decideFold(base({ verticalSegments: 2, innerWidth: 412, innerHeight: 880 }));
  assert.equal(d.fold, "flex");
  assert.equal(d.segments, "css");
});

test("horizontal viewport segments are a book", () => {
  const d = decideFold(base({ horizontalSegments: 2, innerWidth: 884, innerHeight: 1100 }));
  assert.equal(d.fold, "book");
  assert.equal(d.segments, "css");
});

test("native half-open horizontal hinge is flex and splits around the hinge", () => {
  const d = decideFold(
    base({
      innerWidth: 412,
      innerHeight: 880,
      native: {
        state: "HALF_OPENED",
        orientation: "HORIZONTAL",
        hingeTop: 420,
        hingeHeight: 28,
        hingeLeft: 0,
        hingeWidth: 412,
        separating: true,
      },
    }),
  );
  assert.equal(d.fold, "flex");
  assert.equal(d.segments, "native");
  assert.equal(d.segTop, 420);
  assert.equal(d.hingeSize, 28);
  assert.equal(d.segBottom, 432);
});

test("native flat vertical hinge is still a book so the crease stays clear", () => {
  const d = decideFold(
    base({
      innerWidth: 884,
      innerHeight: 1100,
      native: {
        state: "FLAT",
        orientation: "VERTICAL",
        hingeTop: 0,
        hingeHeight: 1100,
        hingeLeft: 430,
        hingeWidth: 24,
        separating: false,
      },
    }),
  );
  assert.equal(d.fold, "book");
  assert.equal(d.segLeft, 430);
  assert.equal(d.hingeSize, 24);
  assert.equal(d.segRight, 430);
});

test("native flat horizontal hinge does not split an open Flip", () => {
  const d = decideFold(
    base({
      native: {
        state: "FLAT",
        orientation: "HORIZONTAL",
        hingeTop: 400,
        hingeHeight: 0,
        hingeLeft: 0,
        hingeWidth: 0,
        separating: false,
      },
    }),
  );
  assert.equal(d.fold, "phone");
});

test("folded tall Flip without segments is flex", () => {
  const d = decideFold(base({ posture: "folded", innerWidth: 412, innerHeight: 860 }));
  assert.equal(d.fold, "flex");
});

test("folded squarish Fold without segments is a book", () => {
  const d = decideFold(base({ posture: "folded", innerWidth: 800, innerHeight: 920 }));
  assert.equal(d.fold, "book");
});

test("cover screen is the small square display, not a split window", () => {
  const cover = decideFold(
    base({ innerWidth: 360, innerHeight: 392, screenWidth: 360, screenHeight: 392 }),
  );
  assert.equal(cover.fold, "cover");
  const split = decideFold(
    base({ innerWidth: 390, innerHeight: 420, screenWidth: 390, screenHeight: 844 }),
  );
  assert.equal(split.fold, "phone");
});

test("stacked window segments are flex", () => {
  const d = decideFold(
    base({
      innerWidth: 400,
      innerHeight: 800,
      windowSegments: [
        { x: 0, y: 0, w: 400, h: 380 },
        { x: 0, y: 408, w: 400, h: 392 },
      ],
    }),
  );
  assert.equal(d.fold, "flex");
  assert.equal(d.segTop, 380);
  assert.equal(d.hingeSize, 28);
});

test("preview wins over the phone size", () => {
  const d = decideFold(base({ preview: { fold: "cover" } }));
  assert.equal(d.fold, "cover");
});
