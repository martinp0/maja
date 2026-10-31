import { resolve } from "node:path";
import { defineConfig } from "vite";

/** Tizen 6.5 (The Serif 2022) má webový engine kolem Chrome 85. Balíček je relativní, ať funguje v .wgt. */
export default defineConfig({
  base: "./",
  // Nepřebírat web/public (ikony a splash iPhonu do .wgt nepatří).
  publicDir: false,
  build: {
    target: "chrome85",
    outDir: resolve(import.meta.dirname, "../tizen/package"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(import.meta.dirname, "tv.html"),
    },
  },
});
