/** Široké okno (notebook, iPad na šířku): panel vlevo. Užší zůstává spodní list. */
export const DESKTOP_MQ = "(min-width: 900px)";

export function isDesktop(): boolean {
  return window.matchMedia?.(DESKTOP_MQ).matches ?? false;
}
