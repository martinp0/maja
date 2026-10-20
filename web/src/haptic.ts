/** Krátký dotyk, kde to prohlížeč umí. Na iPhonu je vibrace tichá – tlačítko stejně změní měřítko. */
export function tick(ms = 10): void {
  if (typeof navigator === "undefined" || typeof navigator.vibrate !== "function") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  try {
    navigator.vibrate(ms);
  } catch {
    /* ignore */
  }
}
