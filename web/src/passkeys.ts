// Passkeys (Face ID / Touch ID) přes @simplewebauthn/browser.
import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
  WebAuthnError,
} from "@simplewebauthn/browser";
import { webauthn, type LoginResult } from "./api";

const LOCAL_KEY = "maja.passkey.id"; // credential_id passkey vytvořeného/použitého na TOMHLE zařízení
const DISMISS_KEY = "maja.passkey.dismissed";

export const passkeysSupported = (): boolean => typeof window !== "undefined" && browserSupportsWebAuthn();

export async function platformAuthenticatorAvailable(): Promise<boolean> {
  if (!passkeysSupported()) return false;
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

export const localPasskeyId = (): string | null => localStorage.getItem(LOCAL_KEY);
export const setLocalPasskeyId = (id: string | null) =>
  id ? localStorage.setItem(LOCAL_KEY, id) : localStorage.removeItem(LOCAL_KEY);
export const offerDismissed = (): boolean => localStorage.getItem(DISMISS_KEY) === "1";
export const dismissOffer = () => localStorage.setItem(DISMISS_KEY, "1");

/** Safari chce WebAuthn volat hned z kliknutí – options se proto stahují dopředu a drží čerstvé. */
export class Prefetched<T> {
  private value: Promise<T> | null = null;
  private at = 0;
  constructor(private load: () => Promise<T>, private maxAgeMs = 120_000) {}
  warm(): void {
    if (!this.value || Date.now() - this.at > this.maxAgeMs) {
      this.at = Date.now();
      this.value = this.load();
      this.value.catch(() => {
        this.value = null;
      });
    }
  }
  /** Vyzvedne (a spotřebuje) připravenou hodnotu – challenge jde použít jen jednou. */
  take(): Promise<T> {
    this.warm();
    const v = this.value!;
    this.value = null;
    return v;
  }
}

function describe(e: unknown): string {
  if (e instanceof WebAuthnError) {
    if (e.code === "ERROR_CEREMONY_ABORTED") return "Zrušeno.";
    if (e.code === "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED") return "Na tomhle zařízení už Face ID zapnuté je.";
  }
  const name = (e as Error)?.name;
  if (name === "NotAllowedError") return "Zrušeno nebo vypršel čas.";
  if (name === "InvalidStateError") return "Na tomhle zařízení už Face ID zapnuté je.";
  if (name === "SecurityError") return "Face ID funguje jen na nastavené doméně aplikace.";
  return (e as Error)?.message || "Neznámá chyba";
}

export type PasskeyLogin = LoginResult | "none" | "cancelled";

export async function loginWithPasskey(
  options: Promise<Record<string, unknown> | "none" | "rate_limited">,
): Promise<{ result: PasskeyLogin; message?: string }> {
  let opts: Record<string, unknown> | "none" | "rate_limited";
  try {
    opts = await options;
  } catch {
    return { result: "error", message: "Server je nedostupný." };
  }
  if (opts === "none") return { result: "none", message: "Face ID zatím není zapnuté – přihlas se heslem." };
  if (opts === "rate_limited") return { result: "rate_limited" };
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cred = await startAuthentication({ optionsJSON: opts as any });
    const r = await webauthn.loginVerify(cred);
    if (r === "ok") setLocalPasskeyId(cred.id);
    return { result: r };
  } catch (e) {
    return { result: "cancelled", message: describe(e) };
  }
}

export async function registerPasskey(
  options: Promise<Record<string, unknown>>,
): Promise<{ ok: boolean; message: string }> {
  try {
    const opts = await options;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cred = await startRegistration({ optionsJSON: opts as any });
    const r = await webauthn.registerVerify(cred);
    setLocalPasskeyId(r.credential.credential_id);
    return { ok: true, message: "Face ID zapnuté. Příště se přihlásíš jedním pohledem." };
  } catch (e) {
    return { ok: false, message: describe(e) };
  }
}
