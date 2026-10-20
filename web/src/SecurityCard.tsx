import { useCallback, useEffect, useMemo, useState } from "react";
import { webauthn, type Passkey } from "./api";
import { fmtAgo, fmtTime } from "./format";
import { tick } from "./haptic";
import { IconFaceId } from "./icons";
import {
  dismissOffer,
  localPasskeyId,
  offerDismissed,
  platformAuthenticatorAvailable,
  Prefetched,
  registerPasskey,
  setLocalPasskeyId,
} from "./passkeys";

const CHANGED = "maja:passkeys-changed"; // ať se karty (nabídka + nastavení) navzájem obnoví
const notifyChanged = () => window.dispatchEvent(new Event(CHANGED));

type Msg = { kind: "ok" | "err" | "info"; text: string } | null;

function usePasskeys() {
  const [list, setList] = useState<Passkey[]>();
  const reload = useCallback(() => {
    webauthn.list().then(setList).catch(() => setList(undefined));
  }, []);
  useEffect(() => {
    reload();
    window.addEventListener(CHANGED, reload);
    return () => window.removeEventListener(CHANGED, reload);
  }, [reload]);
  return { list, reload };
}

/** Má tohle zařízení svůj passkey? (lokální značka + passkey pořád existuje na serveru) */
function hasLocal(list: Passkey[] | undefined): boolean {
  const id = localPasskeyId();
  return !!id && !!list?.some((p) => p.credential_id === id);
}

function useRegister(onDone: () => void) {
  const options = useMemo(() => new Prefetched(webauthn.registerOptions, 100_000), []);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const enable = async () => {
    if (busy) return;
    tick(10);
    setBusy(true);
    setMsg(null);
    const r = await registerPasskey(options.take()); // bez await před take() – Safari user gesture
    setBusy(false);
    setMsg({ kind: r.ok ? "ok" : "err", text: r.message });
    options.warm();
    onDone();
    notifyChanged();
  };
  return { options, busy, msg, enable };
}

/** Nabídka po přihlášení heslem: „Zapnout Face ID“ (jen když zařízení umí a ještě ho nemá). */
export function PasskeyOffer({ show }: { show: boolean }) {
  const { list, reload } = usePasskeys();
  const [available, setAvailable] = useState(false);
  const [hidden, setHidden] = useState(offerDismissed());
  const reg = useRegister(reload);

  useEffect(() => {
    if (show) platformAuthenticatorAvailable().then(setAvailable);
  }, [show]);
  const visible = show && available && !hidden && list !== undefined && (!hasLocal(list) || reg.msg?.kind === "ok");
  useEffect(() => {
    if (visible) reg.options.warm();
  }, [visible, reg.options]);
  if (!visible) return null;

  return (
    <section className="offer">
      <div className="offer-icon" aria-hidden>
        <IconFaceId size={26} />
      </div>
      <div className="offer-copy">
        <h2>Zapnout Face ID?</h2>
        {reg.msg?.kind === "ok" ? (
          <p className="msg ok">{reg.msg.text}</p>
        ) : (
          <p>Příště se přihlásíš jedním pohledem. Heslo zůstane jako záloha.</p>
        )}
      </div>
      {reg.msg?.kind !== "ok" && (
        <div className="offer-actions">
          <button type="button" className="btn primary" onClick={reg.enable} disabled={reg.busy}>
            {reg.busy ? "Čekám na Face ID…" : "Zapnout"}
          </button>
          <button
            type="button"
            className="btn quiet"
            onClick={() => {
              dismissOffer();
              setHidden(true);
            }}
            disabled={reg.busy}
          >
            Teď ne
          </button>
          {reg.msg && <p className={`msg ${reg.msg.kind}`}>{reg.msg.text}</p>}
        </div>
      )}
    </section>
  );
}

/** Nastavení: seznam passkeys + smazání + přidání na tomhle zařízení. */
export function SecurityCard() {
  const { list, reload } = usePasskeys();
  const [available, setAvailable] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [pending, setPending] = useState<number | null>(null);
  const reg = useRegister(reload);
  const local = localPasskeyId();

  useEffect(() => {
    platformAuthenticatorAvailable().then((ok) => {
      setAvailable(ok);
      if (ok) reg.options.warm();
    });
  }, [reg.options]);

  const remove = async (p: Passkey) => {
    try {
      await webauthn.remove(p.id);
      if (p.credential_id === local) setLocalPasskeyId(null);
      setPending(null);
      setMsg({ kind: "ok", text: "Passkey je smazaný. Na tom zařízení se přihlásíš heslem." });
      notifyChanged();
    } catch (e) {
      setMsg({ kind: "err", text: `Nepovedlo se: ${(e as Error).message}` });
    }
  };

  return (
    <section className="settings-section security">
      <h2>Přihlášení</h2>
      <div className="inset">
        {list === undefined ? (
          <p className="inset-note">Načítám…</p>
        ) : list.length === 0 ? (
          <p className="inset-note">Zatím žádný passkey. Přihlašuješ se heslem.</p>
        ) : (
          <ul className="pk-list">
            {list.map((p) => (
              <li key={p.id}>
                <div className="pk-main">
                  <span className="row-title">
                    {p.name ?? "Zařízení"}
                    {p.credential_id === local && <span className="this-device">toto zařízení</span>}
                  </span>
                  <span className="row-sub">
                    přidáno {fmtTime(p.created_at)}
                    {p.last_used_at ? ` · naposledy ${fmtAgo(p.last_used_at)}` : ""}
                    {p.backed_up ? " · iCloud" : ""}
                  </span>
                </div>
                {pending === p.id ? (
                  <div className="pk-confirm">
                    <button type="button" className="text-btn danger" onClick={() => remove(p)}>
                      Smazat
                    </button>
                    <button type="button" className="text-btn" onClick={() => setPending(null)}>
                      Zrušit
                    </button>
                  </div>
                ) : (
                  <button type="button" className="text-btn danger" onClick={() => setPending(p.id)}>
                    Smazat
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        {available && !hasLocal(list) && (
          <button type="button" className="inset-btn" onClick={reg.enable} disabled={reg.busy}>
            {reg.busy ? "Čekám na Face ID…" : "Zapnout Face ID na tomhle zařízení"}
          </button>
        )}
        {(reg.msg ?? msg) && <p className={`msg ${(reg.msg ?? msg)!.kind}`}>{(reg.msg ?? msg)!.text}</p>}
      </div>
    </section>
  );
}
