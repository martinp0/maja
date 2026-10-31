import { PointerEvent, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { tick } from "./haptic";
import { currentFold, DESKTOP_MQ, isDesktop, type FoldName } from "./layout";

export type Snap = "peek" | "mid" | "full";

interface Props {
  snap: Snap;
  onSnap: (snap: Snap) => void;
  head: ReactNode;
  children: ReactNode;
}

const ORDER: Snap[] = ["peek", "mid", "full"];

function shellOf(node: HTMLElement | null): HTMLElement | null {
  return node?.closest(".shell") ?? null;
}

function useChrome(): { pinned: boolean; fold: FoldName } {
  const read = () => {
    const fold = currentFold();
    const wide = window.matchMedia?.(DESKTOP_MQ).matches ?? isDesktop();
    return { fold, pinned: fold !== "phone" || wide };
  };
  const [chrome, setChrome] = useState(read);
  useEffect(() => {
    const on = () => setChrome(read());
    const mq = window.matchMedia(DESKTOP_MQ);
    on();
    mq.addEventListener("change", on);
    window.addEventListener("maja-fold", on);
    window.addEventListener("resize", on);
    return () => {
      mq.removeEventListener("change", on);
      window.removeEventListener("maja-fold", on);
      window.removeEventListener("resize", on);
    };
  }, []);
  return chrome;
}

/** Spodní list ve stylu Apple Maps: tři polohy, táhnutí za hlavičku, zbytek se scrolluje.
 *  Od 900 px je z něj pevný levý panel — bez táhla a bez překryvu mapy. */
export function BottomSheet({ snap, onSnap, head, children }: Props) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);
  const [peek, setPeek] = useState(260);
  const [drag, setDrag] = useState<number | null>(null);
  const [stuck, setStuck] = useState(false);
  const dragRef = useRef<{ y: number; vis: number; t: number } | null>(null);
  const { pinned, fold } = useChrome();

  useLayoutEffect(() => {
    const sheet = sheetRef.current;
    const headEl = headRef.current;
    if (!sheet) return;
    const measure = () => {
      setHeight(sheet.clientHeight);
      if (headEl) setPeek(Math.min(headEl.offsetHeight, Math.round(sheet.clientHeight * 0.72)));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(sheet);
    if (headEl) ro.observe(headEl);
    return () => ro.disconnect();
  }, []);

  const mid = height ? Math.min(Math.round(height * 0.56), Math.max(peek + 80, 420)) : 0;
  const points: Record<Snap, number> = {
    peek,
    mid: Math.max(mid, peek),
    full: height || peek,
  };
  const visible = drag ?? points[snap] ?? peek;

  useLayoutEffect(() => {
    const shell = shellOf(sheetRef.current);
    if (!shell) return;
    if (pinned) {
      shell.style.setProperty("--sheet-visible", "0px");
      shell.dataset.layout = fold === "phone" ? "desktop" : fold;
      delete shell.dataset.snap;
      shell.classList.remove("dragging");
      return;
    }
    delete shell.dataset.layout;
    if (!visible) return;
    shell.style.setProperty("--sheet-visible", `${Math.round(visible)}px`);
    shell.dataset.snap = snap;
    shell.classList.toggle("dragging", drag != null);
  }, [visible, snap, drag, pinned, fold]);

  useEffect(() => {
    if (!pinned) return;
    dragRef.current = null;
    setDrag(null);
  }, [pinned]);

  const finish = (clientY: number, time: number) => {
    const start = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!start || !height) return;
    const moved = start.y - clientY;
    const dt = Math.max(16, time - start.t);
    const vel = moved / dt;
    if (Math.abs(moved) < 8) {
      const i = ORDER.indexOf(snap);
      onSnap(ORDER[(i + 1) % ORDER.length]);
      tick(8);
      return;
    }
    const current = start.vis + moved;
    let next: Snap;
    if (vel > 0.45) next = snap === "peek" ? "mid" : "full";
    else if (vel < -0.45) next = snap === "full" ? "mid" : "peek";
    else {
      next = ORDER.reduce((best, name) =>
        Math.abs(points[name] - current) < Math.abs(points[best] - current) ? name : best,
      );
    }
    if (next !== snap) tick(8);
    onSnap(next);
  };

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (pinned) return;
    if ((e.target as HTMLElement).closest("button, a, input")) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { y: e.clientY, vis: visible, t: e.timeStamp };
    setDrag(visible);
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const start = dragRef.current;
    if (!start || !height) return;
    const next = Math.min(height, Math.max(peek * 0.72, start.vis + (start.y - e.clientY)));
    setDrag(next);
  };
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    finish(e.clientY, e.timeStamp);
  };

  const translate = height ? Math.max(0, height - visible) : 0;

  return (
    <div
      ref={sheetRef}
      className="sheet"
      style={
        pinned
          ? undefined
          : {
              transform: height ? `translate3d(0, ${translate}px, 0)` : undefined,
              transition: drag != null ? "none" : undefined,
            }
      }
    >
      <div
        ref={headRef}
        className={`sheet-head ${stuck ? "stuck" : ""}`}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      >
        <div className="grabber" aria-hidden />
        {head}
      </div>
      <div
        ref={scrollRef}
        className="sheet-scroll"
        onScroll={(e) => setStuck(e.currentTarget.scrollTop > 2)}
      >
        {children}
      </div>
    </div>
  );
}
