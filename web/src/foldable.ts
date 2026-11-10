// Rozložení pro Galaxy Z Flip / Z Fold a běžné okno. CSS viewport segments
// (Chrome, TWA) i nativní WindowManager (WebView) končí ve stejných data atributech.
import { decideFold, type FoldInput, type FoldPreview, type NativePosture, type SegmentRect } from "./foldable-model";

type SegmentMedia = { matches?: boolean };

interface PostureNavigator {
  devicePosture?: { type?: string; addEventListener?: (type: string, cb: () => void) => void };
}

interface SegmentWindow extends Window {
  getWindowSegments?: () => DOMRect[];
  __MAJA_FOLD_PREVIEW__?: FoldPreview;
  __majaNativePosture?: NativePosture;
  __majaApplyNativePosture?: (posture: NativePosture | null) => void;
}

function segmentCount(query: string): number {
  const mq = window.matchMedia?.(query) as MediaQueryList & { matches: boolean };
  if (!mq) return 0;
  return (mq as SegmentMedia).matches ? 2 : 0;
}

function readPreview(): FoldPreview | null {
  const raw = (window as SegmentWindow).__MAJA_FOLD_PREVIEW__;
  if (!raw || (raw.fold !== "phone" && raw.fold !== "book" && raw.fold !== "flex" && raw.fold !== "cover")) return null;
  return raw;
}

function readSegments(): SegmentRect[] | null {
  const w = window as SegmentWindow;
  const fromFn = w.getWindowSegments?.();
  const vv = window.visualViewport as (VisualViewport & { segments?: DOMRect[] }) | null;
  const raw = fromFn && fromFn.length >= 2 ? fromFn : vv?.segments;
  if (!raw || raw.length < 2) return null;
  return raw.map((r) => ({ x: r.x, y: r.y, w: r.width, h: r.height }));
}

function readPosture(): FoldInput["posture"] {
  const type = (navigator as PostureNavigator).devicePosture?.type;
  if (type === "folded" || type === "continuous") return type;
  if (window.matchMedia?.("(device-posture: folded)").matches) return "folded";
  if (window.matchMedia?.("(device-posture: continuous)").matches) return "continuous";
  return "unknown";
}

let native: NativePosture | null = null;

function measure(): FoldInput {
  const screenW = window.screen?.width || window.innerWidth;
  const screenH = window.screen?.height || window.innerHeight;
  return {
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    screenWidth: screenW,
    screenHeight: screenH,
    verticalSegments: segmentCount("(vertical-viewport-segments: 2)"),
    horizontalSegments: segmentCount("(horizontal-viewport-segments: 2)"),
    posture: readPosture(),
    native,
    preview: readPreview(),
    windowSegments: readSegments(),
  };
}

function apply(): void {
  const d = decideFold(measure());
  const root = document.documentElement;
  root.dataset.fold = d.fold;
  root.dataset.segments = d.segments;
  const set = (name: string, value: number | null) => {
    if (value == null) root.style.removeProperty(name);
    else root.style.setProperty(name, `${Math.round(value)}px`);
  };
  set("--seg-top", d.segTop);
  set("--seg-bottom", d.segBottom);
  set("--seg-left", d.segLeft);
  set("--seg-right", d.segRight);
  set("--hinge-size", d.hingeSize);
  window.dispatchEvent(new Event("maja-fold"));
}

/** Zavolat jednou před prvním renderem. Sleduje resize, pant a nativní most. */
export function startFoldable(): void {
  const w = window as SegmentWindow;
  native = w.__majaNativePosture ?? null;
  w.__majaApplyNativePosture = (posture) => {
    native = posture;
    w.__majaNativePosture = posture ?? undefined;
    apply();
  };
  apply();
  window.addEventListener("resize", apply);
  window.visualViewport?.addEventListener("resize", apply);
  const queries = [
    "(vertical-viewport-segments: 2)",
    "(horizontal-viewport-segments: 2)",
    "(device-posture: folded)",
    "(min-width: 600px)",
    "(min-width: 900px)",
  ];
  for (const q of queries) window.matchMedia?.(q).addEventListener?.("change", apply);
  (navigator as PostureNavigator).devicePosture?.addEventListener?.("change", apply);
}
