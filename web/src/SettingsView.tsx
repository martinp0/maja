import { useEffect, useRef, useState } from "react";
import type { GeofenceState } from "./api";
import { fmtDistance } from "./format";
import { tick } from "./haptic";
import { NotificationsCard } from "./NotificationsCard";
import { SecurityCard } from "./SecurityCard";

interface Props {
  petName: string;
  model?: string | null;
  geo: GeofenceState | undefined;
  onClose: () => void;
  onLogout?: () => void;
}

function placeLabel(geo: GeofenceState): string {
  if (geo.state === "unknown") return "Zatím nevíme";
  if (geo.state === "home") return "Doma";
  return geo.escaped ? "Utekla" : "Venku";
}

export function SettingsView({ petName, model, geo, onClose, onLogout }: Props) {
  const [leaving, setLeaving] = useState(false);
  const doneRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const close = () => {
    tick(8);
    setLeaving(true);
    window.setTimeout(onClose, 280);
  };

  useEffect(() => {
    doneRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      if (e.key !== "Tab") return;
      const root = panelRef.current;
      if (!root) return;
      const items = [...root.querySelectorAll<HTMLElement>("button, a, input, select, textarea")].filter(
        (el) => !el.hidden && el.tabIndex !== -1 && !el.hasAttribute("disabled"),
      );
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !root.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !root.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // close is stable enough for the lifetime of the sheet
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={`settings ${leaving ? "leaving" : ""}`} role="dialog" aria-modal="true" aria-label="Nastavení">
      <button type="button" className="settings-scrim" tabIndex={-1} aria-label="Zavřít" onClick={close} />
      <div ref={panelRef} className="settings-panel">
      <div className="settings-bar">
        <span className="settings-side" />
        <button ref={doneRef} type="button" className="text-btn settings-side" onClick={close}>
          Hotovo
        </button>
      </div>
      <div className="settings-scroll">
        <h1>Nastavení</h1>
        <NotificationsCard petName={petName} />
        <SecurityCard />
        <section className="settings-section">
          <h2>Domácí zóna</h2>
          <div className="inset">
            {!geo ? (
              <p className="inset-note">Načítám…</p>
            ) : !geo.enabled ? (
              <p className="inset-note">Domácí zóna není zapnutá.</p>
            ) : (
              <dl className="facts">
                <div>
                  <dt>Stav</dt>
                  <dd>{placeLabel(geo)}</dd>
                </div>
                <div>
                  <dt>Poloměr</dt>
                  <dd>{Math.round(geo.home_radius_m)} m</dd>
                </div>
                <div>
                  <dt>Hlášení útěku</dt>
                  <dd>od {fmtDistance(geo.escape_m)}</dd>
                </div>
              </dl>
            )}
            <p className="inset-note">
              Zóna je na mapě přerušovaný kruh. Přesnou adresu domova appka neukazuje.
            </p>
          </div>
        </section>
        {model && <p className="footnote center">Obojek {model}</p>}
        {onLogout && (
          <button type="button" className="logout-btn" onClick={onLogout}>
            Odhlásit se
          </button>
        )}
      </div>
      </div>
    </div>
  );
}
