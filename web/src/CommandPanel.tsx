import { useState } from "react";
import { api, type CommandRow } from "./api";
import { COMMAND_LABEL, STATUS_LABEL, errorLabel, fmtAgo } from "./format";
import { tick } from "./haptic";
import { IconBell, IconLight, IconLightOff, IconStop } from "./icons";

interface Props {
  trackerId: string;
  petName: string;
  home: boolean | null | undefined;
  commands: CommandRow[] | undefined;
  onSent: () => void;
}

const BUTTONS: { command: string; action: "on" | "off"; label: string; icon: "stop" | "bell" | "light" | "off" }[] = [
  { command: "live", action: "off", label: "Ukončit živou", icon: "stop" },
  { command: "buzzer", action: "on", label: "Zvuk", icon: "bell" },
  { command: "led", action: "on", label: "Světlo", icon: "light" },
  { command: "led", action: "off", label: "Zhasnout", icon: "off" },
];

function ActionIcon({ name }: { name: (typeof BUTTONS)[number]["icon"] }) {
  if (name === "stop") return <IconStop />;
  if (name === "bell") return <IconBell />;
  if (name === "off") return <IconLightOff />;
  return <IconLight />;
}

export function CommandPanel({ trackerId, petName, home, commands, onSent }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);

  const send = async (command: string, action: "on" | "off") => {
    const key = `${command}:${action}`;
    tick(10);
    setBusy(key);
    setMsg(null);
    try {
      const row = await api.sendCommand(trackerId, command, action);
      setMsg(
        row.in_home_zone
          ? { kind: "info", text: `Zařazeno (${STATUS_LABEL[row.status] ?? row.status}). ${petName} je doma, povel se pošle, až bude venku.` }
          : { kind: "ok", text: `Zařazeno (${STATUS_LABEL[row.status] ?? row.status}).` },
      );
      onSent();
    } catch (e) {
      setMsg({ kind: "err", text: `Nepovedlo se: ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="block commands">
      <h2>Povely</h2>
      {home && (
        <p className="quiet-note">
          {petName} je doma na Wi‑Fi. Povely se odloží a odešlou se, až bude venku. Nejpozději za pár minut vyprší.
        </p>
      )}
      <div className="action-row">
        {BUTTONS.map((b) => {
          const key = `${b.command}:${b.action}`;
          return (
            <button
              key={key}
              type="button"
              onClick={() => send(b.command, b.action)}
              disabled={busy !== null}
              className={`action ${busy === key ? "busy" : ""}`}
            >
              <span className="action-icon">
                <ActionIcon name={b.icon} />
              </span>
              <span className="action-label">{b.label}</span>
            </button>
          );
        })}
      </div>
      {msg && <p className={`msg ${msg.kind}`}>{msg.text}</p>}
      {commands && commands.length > 0 && (
        <ul className="cmd-list">
          {commands.map((c) => (
            <li key={c.id}>
              <span className={`dot-status ${c.status}`} aria-hidden />
              <span className="cmd-name">
                {COMMAND_LABEL[c.command] ?? c.command}
                {c.command === "buzzer" ? "" : c.action === "on" ? " zapnout" : " vypnout"}
              </span>
              <span className="cmd-meta">
                {STATUS_LABEL[c.status] ?? c.status}
                <span className="muted"> · {fmtAgo(c.created_at)}</span>
              </span>
              {errorLabel(c.last_error) && <span className="cmd-err">{errorLabel(c.last_error)}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
