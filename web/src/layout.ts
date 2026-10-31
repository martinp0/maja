/** Široké okno (notebook, iPad na šířku): panel vlevo. Užší zůstává spodní list. */
export const DESKTOP_MQ = "(min-width: 900px)";

export type FoldName = "phone" | "book" | "flex" | "cover";

export function currentFold(): FoldName {
  const v = document.documentElement.dataset.fold;
  if (v === "book" || v === "flex" || v === "cover" || v === "phone") return v;
  return "phone";
}

export function isDesktop(): boolean {
  return window.matchMedia?.(DESKTOP_MQ).matches ?? false;
}

/** Mapa není překrytá spodním listem: knihovni Fold, Flex, kryt Flipu, nebo okno od 900 px. */
export function mapIsUncovered(): boolean {
  const fold = currentFold();
  if (fold === "book" || fold === "flex" || fold === "cover") return true;
  return isDesktop();
}
