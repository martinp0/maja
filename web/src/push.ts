// Web Push v prohlížeči: oprávnění, odběr přes VAPID klíč, registrace na serveru.
import { api } from "./api";

export type PushState =
  | "unsupported" // prohlížeč push neumí
  | "needs-install" // iPhone/iPad v Safari – nejdřív přidat na plochu
  | "server-off" // server nemá VAPID klíče
  | "denied" // uživatel zablokoval
  | "off"
  | "on";

export function isIos(): boolean {
  const ua = navigator.userAgent;
  // iPadOS se hlásí jako Mac – pozná se podle dotyku
  return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
}

export function isStandalone(): boolean {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function urlB64ToUint8Array(b64: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  return navigator.serviceWorker.ready;
}

export async function currentState(): Promise<PushState> {
  if (isIos() && !isStandalone()) return "needs-install";
  if (!pushSupported()) return "unsupported";
  const key = await api.pushKey();
  if (!key.enabled || !key.public_key) return "server-off";
  if (Notification.permission === "denied") return "denied";
  const sub = await (await registration()).pushManager.getSubscription();
  if (sub && Notification.permission === "granted") {
    // server mohl odběr smazat (410/404) – tiše obnovit
    api.pushSubscribe(sub.toJSON()).catch(() => undefined);
    return "on";
  }
  return "off";
}

/** MUSÍ se volat přímo z kliknutí (iOS jinak oprávnění nenabídne). */
export async function enablePush(): Promise<PushState> {
  // requestPermission jako první await – před ním nic asynchronního (user gesture)
  const perm = await Notification.requestPermission();
  if (perm === "denied") return "denied";
  if (perm !== "granted") return "off";
  const key = await api.pushKey();
  if (!key.enabled || !key.public_key) return "server-off";
  const reg = await registration();
  const appKey = urlB64ToUint8Array(key.public_key);
  let sub = await reg.pushManager.getSubscription();
  if (sub) {
    // odběr s jiným (starým) VAPID klíčem by server nemohl použít
    const cur = sub.options.applicationServerKey;
    const same = cur && new Uint8Array(cur).every((b, i) => b === appKey[i]) && cur.byteLength === appKey.length;
    if (!same) {
      await sub.unsubscribe().catch(() => false);
      sub = null;
    }
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appKey });
  await api.pushSubscribe(sub.toJSON());
  return "on";
}

export async function disablePush(): Promise<PushState> {
  const sub = await (await registration()).pushManager.getSubscription();
  if (sub) {
    await api.pushUnsubscribe(sub.endpoint).catch(() => undefined);
    await sub.unsubscribe().catch(() => false);
  }
  return "off";
}
