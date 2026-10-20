import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { auth, UNAUTHORIZED_EVENT, webauthn } from "./api";
import App from "./App";
import { tick } from "./haptic";
import { IconFaceId } from "./icons";
import { loginWithPasskey, passkeysSupported, Prefetched } from "./passkeys";

type State = "checking" | "authed" | "login" | "offline";

/** Před mapou ověří session (GET /api/me); bez ní ukáže přihlášení (Face ID / heslo). */
export default function AuthGate() {
  const [state, setState] = useState<State>("checking");
  const [passkeyCount, setPasskeyCount] = useState<number | null>(null);
  const [justLoggedInWithPassword, setJustLoggedInWithPassword] = useState(false);

  const check = useCallback(async () => {
    try {
      const me = await auth.me();
      setPasskeyCount(me.passkey_count ?? null);
      setState(me.authed ? "authed" : "login");
    } catch {
      setState("offline");
    }
  }, []);

  useEffect(() => {
    check();
    const onUnauth = () => setState("login");
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauth);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauth);
  }, [check]);

  const logout = async () => {
    await auth.logout();
    setJustLoggedInWithPassword(false);
    await check();
  };

  if (state === "authed") return <App onLogout={logout} offerPasskey={justLoggedInWithPassword} />;
  if (state === "checking") {
    return (
      <div className="splash" role="status">
        <img src="/apple-touch-icon.png" alt="" width="76" height="76" />
      </div>
    );
  }
  if (state === "offline") {
    return (
      <div className="gate">
        <div className="gate-card">
          <img className="gate-mark" src="/apple-touch-icon.png" alt="" width="84" height="84" />
          <h1>Mája</h1>
          <p className="gate-sub">Server je teď nedostupný.</p>
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              tick(8);
              setState("checking");
              check();
            }}
          >
            Zkusit znovu
          </button>
        </div>
      </div>
    );
  }
  return (
    <Login
      passkeyCount={passkeyCount}
      onSuccess={async (method) => {
        setJustLoggedInWithPassword(method === "password");
        await check();
      }}
    />
  );
}

function Login({
  passkeyCount,
  onSuccess,
}: {
  passkeyCount: number | null;
  onSuccess: (method: "password" | "passkey") => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  // Face ID tlačítko: prohlížeč WebAuthn umí a na serveru je aspoň jeden passkey (null = nevíme -> ukázat)
  const showPasskey = passkeysSupported() && passkeyCount !== 0;
  const [showPassword, setShowPassword] = useState(!showPasskey);
  const busyRef = useRef(false);
  busyRef.current = busy;

  const options = useMemo(() => new Prefetched(webauthn.loginOptions, 100_000), []);
  useEffect(() => {
    if (!showPasskey) return;
    options.warm();
    const id = window.setInterval(() => !busyRef.current && options.warm(), 60_000);
    return () => window.clearInterval(id);
  }, [options, showPasskey]);

  const passkey = async () => {
    if (busy) return;
    tick(10);
    setBusy(true);
    setError(undefined);
    // žádný await před take() -> WebAuthn běží v rámci kliknutí (Safari)
    const { result, message } = await loginWithPasskey(options.take());
    setBusy(false);
    if (result === "ok") {
      await onSuccess("passkey");
      return;
    }
    options.warm();
    if (result === "none") setShowPassword(true);
    setError(
      result === "rate_limited"
        ? "Moc pokusů, zkus to za chvíli"
        : result === "bad_pin"
          ? "Tenhle passkey server nezná. Přihlas se heslem."
          : message || "Přihlášení selhalo, zkus to znovu",
    );
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password || busy) return;
    tick(10);
    setBusy(true);
    setError(undefined);
    const r = await auth.login(password);
    setBusy(false);
    if (r === "ok") {
      await onSuccess("password");
      return;
    }
    setPassword("");
    setError(
      r === "bad_pin" ? "Špatné heslo" : r === "rate_limited" ? "Moc pokusů, zkus to za chvíli" : "Přihlášení selhalo, zkus to znovu",
    );
  };

  return (
    <div className="gate">
      <div className="gate-card">
      <img className="gate-mark" src="/apple-touch-icon.png" alt="" width="84" height="84" />
      <h1>Mája</h1>
      <p className="gate-sub">Kde je teď</p>
      <form className="gate-form" onSubmit={submit}>
        {showPasskey && (
          <button type="button" className="btn primary" onClick={passkey} disabled={busy}>
            <IconFaceId />
            {busy && !password ? "Ověřuji…" : "Přihlásit přes Face ID"}
          </button>
        )}
        {showPasskey && !showPassword && (
          <button type="button" className="btn quiet" onClick={() => setShowPassword(true)}>
            Přihlásit heslem
          </button>
        )}
        {showPassword && (
          <>
            {showPasskey && <div className="or">nebo heslem</div>}
            {/* Skryté uživatelské jméno, aby iCloud Klíčenka / správci hesel nabídli uložení. */}
            <input type="text" name="username" autoComplete="username" value="maja" readOnly hidden />
            <label className="sr-only" htmlFor="maja-password">
              Heslo
            </label>
            <input
              id="maja-password"
              type="password"
              name="password"
              autoComplete="current-password"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              autoFocus={!showPasskey}
              placeholder="Heslo"
              aria-label="Heslo"
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button className={showPasskey ? "btn secondary" : "btn primary"} type="submit" disabled={!password || busy}>
              {busy && password ? "Ověřuji…" : "Odemknout"}
            </button>
          </>
        )}
        {error && (
          <p id="login-error" className="msg err" role="alert">
            {error}
          </p>
        )}
      </form>
      </div>
    </div>
  );
}
