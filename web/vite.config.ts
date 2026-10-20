import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Build:  npm ci && npm run build   -> web/dist (servíruje ho FastAPI na /)
// Dev:    npm run dev               -> http://localhost:5173, /api se proxuje na VITE_DEV_API (default 127.0.0.1:8090)
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "apple-touch-icon.png", "pwa-192.png", "pwa-512.png"],
      manifest: {
        name: "Mája – kde je pes",
        short_name: "Mája",
        description: "Poloha, aktivita a povely pro Májin Tractive tracker",
        lang: "cs",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#f2f2f7",
        theme_color: "#f2f2f7",
        icons: [
          { src: "pwa-192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512.png", sizes: "512x512", type: "image/png" },
          { src: "pwa-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      // Vlastní SW (src/sw.ts): precache + runtime cache + Web Push (push / notificationclick).
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectManifest: {
        globPatterns: ["**/*.{js,css,html,svg,webmanifest}", "pwa-192.png", "pwa-512.png", "apple-touch-icon.png"],
      },
    }),
  ],
  server: {
    proxy: {
      "/api": process.env.VITE_DEV_API ?? "http://127.0.0.1:8090",
    },
  },
});
