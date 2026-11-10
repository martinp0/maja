/** Čisté rozhodnutí rozložení pro Galaxy (telefon, Fold, Flip, kryt). Bez DOM, ať jde otestovat. */

export type FoldName = "phone" | "book" | "flex" | "cover";

export interface SegmentRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Postoj z WindowManageru ve WebView (CSS pixely). */
export interface NativePosture {
  state: string;
  orientation: string;
  hingeTop: number;
  hingeHeight: number;
  hingeLeft: number;
  hingeWidth: number;
  separating: boolean;
}

export interface FoldPreview {
  fold: FoldName;
  hingeTop?: number;
  hingeHeight?: number;
  hingeLeft?: number;
  hingeWidth?: number;
}

export interface FoldInput {
  innerWidth: number;
  innerHeight: number;
  screenWidth: number;
  screenHeight: number;
  verticalSegments: number;
  horizontalSegments: number;
  /** `folded` z Device Posture API, jinak `continuous` / `unknown`. */
  posture: "continuous" | "folded" | "unknown";
  native: NativePosture | null;
  preview: FoldPreview | null;
  windowSegments: SegmentRect[] | null;
}

export interface FoldDecision {
  fold: FoldName;
  /** `css` = env(viewport-segment-*), `native` = proměnné z pantu, `none` = bez mezery. */
  segments: "css" | "native" | "none";
  segTop: number | null;
  segBottom: number | null;
  segLeft: number | null;
  segRight: number | null;
  hingeSize: number | null;
}

function blank(fold: FoldName, segments: FoldDecision["segments"] = "none"): FoldDecision {
  return {
    fold,
    segments,
    segTop: null,
    segBottom: null,
    segLeft: null,
    segRight: null,
    hingeSize: null,
  };
}

function withHinge(fold: FoldName, input: FoldInput, hinge: { top: number; height: number; left: number; width: number }): FoldDecision {
  const d = blank(fold, "native");
  if (fold === "flex") {
    const top = Math.max(0, hinge.top);
    const size = Math.max(0, hinge.height);
    d.segTop = top;
    d.hingeSize = size;
    d.segBottom = Math.max(0, input.innerHeight - top - size);
    return d;
  }
  const left = Math.max(0, hinge.left);
  const size = Math.max(0, hinge.width);
  d.segLeft = left;
  d.hingeSize = size;
  d.segRight = Math.max(0, input.innerWidth - left - size);
  return d;
}

/** Kryt Z Flip je skoro čtverec a okno ho vyplní. Rozdělené okno na velkém displeji ne. */
function isCoverScreen(input: FoldInput): boolean {
  const sw = input.screenWidth;
  const sh = input.screenHeight;
  if (sw <= 0 || sh <= 0) return false;
  const longer = Math.max(sw, sh);
  const shorter = Math.min(sw, sh);
  if (longer > 520 || shorter / longer < 0.85) return false;
  const fills =
    input.innerWidth >= sw * 0.9 && input.innerHeight >= sh * 0.9;
  return fills;
}

function fromWindowSegments(input: FoldInput): FoldDecision | null {
  const segs = input.windowSegments;
  if (!segs || segs.length < 2) return null;
  const [a, b] = segs;
  const horizontalGap = Math.abs(a.y - b.y) < 8 && a.x !== b.x;
  const verticalGap = Math.abs(a.x - b.x) < 8 && a.y !== b.y;
  if (verticalGap) {
    const top = a.y <= b.y ? a : b;
    const bottom = a.y <= b.y ? b : a;
    const hingeTop = top.y + top.h;
    const hingeHeight = Math.max(0, bottom.y - hingeTop);
    return withHinge("flex", input, { top: hingeTop, height: hingeHeight, left: 0, width: 0 });
  }
  if (horizontalGap) {
    const left = a.x <= b.x ? a : b;
    const right = a.x <= b.x ? b : a;
    const hingeLeft = left.x + left.w;
    const hingeWidth = Math.max(0, right.x - hingeLeft);
    return withHinge("book", input, { top: 0, height: 0, left: hingeLeft, width: hingeWidth });
  }
  return null;
}

function fromNative(input: FoldInput): FoldDecision | null {
  const n = input.native;
  if (!n || n.orientation === "NONE") return null;
  const verticalHinge = n.orientation === "VERTICAL";
  const half = n.state === "HALF_OPENED" || n.separating;
  if (!verticalHinge && !half && n.state !== "FLAT") return null;
  // Vodorovný pant na rozevřeném Flipu je vysoký telefon, ne stůl. Půlit jen při Flex Mode.
  if (!verticalHinge && n.state !== "HALF_OPENED" && !n.separating) return null;
  if (verticalHinge) {
    return withHinge("book", input, {
      top: n.hingeTop,
      height: n.hingeHeight,
      left: n.hingeLeft,
      width: n.hingeWidth,
    });
  }
  return withHinge("flex", input, {
    top: n.hingeTop,
    height: n.hingeHeight,
    left: n.hingeLeft,
    width: n.hingeWidth,
  });
}

/**
 * Pořadí: náhled (snímky) → nativní pant → CSS segmenty → getWindowSegments →
 * kryt → Device Posture → skoro čtvercový Fold (i bez API) → telefon.
 * Široké ploché okno (>= 900) nechává na stávajícím desktopovém CSS.
 */
export function decideFold(input: FoldInput): FoldDecision {
  if (input.preview) {
    const p = input.preview;
    if (p.fold === "flex" && p.hingeTop != null) {
      return withHinge("flex", input, {
        top: p.hingeTop,
        height: p.hingeHeight ?? 0,
        left: 0,
        width: 0,
      });
    }
    if (p.fold === "book" && p.hingeLeft != null) {
      return withHinge("book", input, {
        top: 0,
        height: 0,
        left: p.hingeLeft,
        width: p.hingeWidth ?? 0,
      });
    }
    return blank(p.fold);
  }

  const native = fromNative(input);
  if (native) return native;

  if (input.verticalSegments >= 2) return blank("flex", "css");
  if (input.horizontalSegments >= 2) return blank("book", "css");

  const fromSegs = fromWindowSegments(input);
  if (fromSegs) return fromSegs;

  if (isCoverScreen(input)) return blank("cover");

  if (input.posture === "folded") {
    const { innerWidth: w, innerHeight: h } = input;
    if (w >= 600 && h > 0 && w / h >= 0.8) return blank("book");
    return blank("flex");
  }

  // Vnitřní displej Foldu na výšku je skoro čtverec (pod 900 px, takže starý desktop breakpoint neplatí).
  // iPad na výšku je vyšší (poměr šířka/výška ~0,7) a zůstane u spodního listu.
  if (input.innerWidth >= 600 && input.innerWidth < 900 && input.innerHeight > 0) {
    if (input.innerWidth / input.innerHeight >= 0.82) return blank("book");
  }

  return blank("phone");
}
