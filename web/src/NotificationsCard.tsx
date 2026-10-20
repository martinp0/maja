import { useEffect, useState } from "react";
import { api } from "./api";
import { tick } from "./haptic";
import { currentState, disablePush, enablePush, type PushState } from "./push";

const LABEL: Record<PushState, string> = {
  on: "Zapnuto",
  off: "Vypnuto",
  denied: "Zablokováno",
  "needs-install": "Nejdřív na plochu",
  "server-off": "Nedostupné",
  unsupported: "Nepodporováno",
};

export function NotificationsCard({ petName }: { petName: string }) {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    currentState()
      .then((s) => alive && setState(s))
      .catch(() => alive && setState("off"));
    return () => {
      alive = false;
    };
  }, []);

  const toggle = async () => {
    if (busy || !state) return;
    tick(8);
    setBusy(true);
    setMsg(null);
    try {
      // Žádný await před enablePush() -> Notification.requestPermission běží v rámci kliknutí
      const next = state === "on" ? await disablePush() : await enablePush();
      setState(next);
      if (next === "on") setMsg({ kind: "ok", text: "Upozornění jsou zapnutá. Můžeš poslat test." });
      else if (next === "denied")
        setMsg({ kind: "err", text: "Upozornění jsou zablokovaná. Povol je v Nastavení → Oznámení → Mája." });
    } catch (e) {
      setMsg({ kind: "err", text: `Nepovedlo se: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    tick(8);
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.pushTest();
      setMsg(
        r.sent > 0
          ? { kind: "ok", text: r.sent === 1 ? "Test odešel na tohle zařízení." : `Test odešel na ${r.sent} zařízení.` }
          : { kind: "err", text: "Žádné zařízení test nepřijalo. Vypni upozornění a zapni je znovu." },
      );
    } catch (e) {
      setMsg({ kind: "err", text: `Test selhal: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  const canToggle = state === "on" || state === "off";

  return (
    <section className="settings-section notify">
      <h2>Upozornění</h2>
      <div className="inset">
        <div className="inset-row">
          <div>
            <div className="row-title">Push</div>
            <div className="row-sub">
              {state ? LABEL[state] : "Načítám…"}
              {" · "}
              odchod, návrat, útěk a slabá baterie {petName === "Mája" ? "Máji" : petName}
            </div>
          </div>
          {canToggle && (
            <button
              type="button"
              className={`switch ${state === "on" ? "on" : ""}`}
              onClick={toggle}
              disabled={busy}
              role="switch"
              aria-checked={state === "on"}
              aria-label={state === "on" ? "Vypnout upozornění" : "Zapnout upozornění"}
            >
              <span className="knob" />
            </button>
          )}
        </div>
        {state === "needs-install" && (
          <p className="inset-note">Na iPhonu nejdřív přidej appku na plochu: Sdílet → Přidat na plochu. Pak se sem vrať.</p>
        )}
        {state === "denied" && (
          <p className="inset-note">Systém upozornění zablokoval. Povol je v Nastavení → Oznámení → Mája.</p>
        )}
        {state === "server-off" && <p className="inset-note">Server zatím nemá klíče pro upozornění.</p>}
        {state === "unsupported" && <p className="inset-note">Tenhle prohlížeč upozornění neumí.</p>}
        {state === "on" && (
          <button type="button" className="inset-btn" onClick={test} disabled={busy}>
            Poslat test
          </button>
        )}
        {msg && <p className={`msg ${msg.kind}`}>{msg.text}</p>}
      </div>
    </section>
  );
}
