/// <reference lib="webworker" />
// Vlastní service worker (vite-plugin-pwa, strategie injectManifest):
// precache appky + OSM dlaždice v cache, /api/* vždy ze sítě + Web Push.
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { CacheFirst, NetworkOnly } from "workbox-strategies";
import { ExpirationPlugin } from "workbox-expiration";
import { CacheableResponsePlugin } from "workbox-cacheable-response";
import { clientsClaim } from "workbox-core";

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: Array<string | { url: string; revision: string | null }> };

// registerType "autoUpdate": nová verze převezme řízení hned
self.skipWaiting();
clientsClaim();

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

// SPA: navigace -> index.html z precache, kromě /api/*
registerRoute(new NavigationRoute(createHandlerBoundToURL("index.html"), { denylist: [/^\/api\//] }));

// /api/* NIKDY z cache: vždy síť (NetworkOnly). Offline radši chyba než stará poloha psa.
// Stará runtime cache "api" z dřívější verze (NetworkFirst) se při aktivaci smaže.
registerRoute(({ url }) => url.origin === self.location.origin && url.pathname.startsWith("/api/"), new NetworkOnly());
registerRoute(
  ({ url }) => url.origin === self.location.origin && url.pathname.startsWith("/api/"),
  new NetworkOnly(),
  "POST",
);
self.addEventListener("activate", (event: ExtendableEvent) => {
  event.waitUntil(caches.delete("api").then(() => undefined));
});

registerRoute(
  ({ url }) => url.hostname === "tile.openstreetmap.org",
  new CacheFirst({
    cacheName: "osm-tiles",
    plugins: [
      new ExpirationPlugin({ maxEntries: 500, maxAgeSeconds: 7 * 86400 }),
      new CacheableResponsePlugin({ statuses: [0, 200] }),
    ],
  }),
);

// ------------------------------------------------------------------ Web Push

interface PushPayload {
  title?: string;
  body?: string;
  tag?: string;
  url?: string;
  kind?: string;
  ts?: string;
  requireInteraction?: boolean;
}

self.addEventListener("push", (event: PushEvent) => {
  let p: PushPayload = {};
  try {
    p = event.data?.json() ?? {};
  } catch {
    p = { body: event.data?.text() };
  }
  const title = p.title || "Mája";
  // iOS: každý push MUSÍ zobrazit notifikaci, jinak Safari odběr po pár „tichých“ pushích zruší.
  event.waitUntil(
    self.registration.showNotification(title, {
      body: p.body ?? "",
      icon: "/pwa-192.png",
      badge: "/pwa-192.png",
      tag: p.tag || p.kind || "maja",
      // stejný tag nahradí starou notifikaci – ať to i tak pípne
      renotify: true,
      requireInteraction: !!p.requireInteraction,
      timestamp: p.ts ? Date.parse(p.ts) : Date.now(),
      data: { url: p.url || "/", kind: p.kind },
      lang: "cs",
    } as NotificationOptions),
  );
});

self.addEventListener("notificationclick", (event: NotificationEvent) => {
  event.notification.close();
  const target = new URL((event.notification.data?.url as string) || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of wins) {
        if (new URL(w.url).origin === self.location.origin) {
          await w.focus();
          if ("navigate" in w && w.url !== target) await (w as WindowClient).navigate(target).catch(() => null);
          return;
        }
      }
      await self.clients.openWindow(target);
    })(),
  );
});

// Prohlížeč odběr obnovil (nové klíče) -> nahlásit serveru; cookie session jde s requestem.
self.addEventListener("pushsubscriptionchange", ((event: Event & {
  waitUntil(p: Promise<unknown>): void;
  oldSubscription?: PushSubscription | null;
  newSubscription?: PushSubscription | null;
}) => {
  event.waitUntil(
    (async () => {
      let sub = event.newSubscription ?? null;
      if (!sub && event.oldSubscription?.options) {
        sub = await self.registration.pushManager.subscribe(event.oldSubscription.options).catch(() => null);
      }
      if (sub) {
        await fetch("/api/push/subscribe", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sub.toJSON()),
        }).catch(() => null);
      }
    })(),
  );
}) as EventListener);
