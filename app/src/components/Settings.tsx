import { useEffect, useState } from "react";
import type { Vocab } from "../api";
import { ACTIONS, type ActionId, type Bindings, comboOf, isMac, showKey } from "../shortcuts";
import { ListsPane } from "./Panels";

type Tab = "shortcuts" | "lists";

/** Application settings: keyboard shortcuts and the lists, with room for more pages (locations, defaults). */
export function SettingsDialog({ initialTab, bindings, vocab, onSaveBindings, onSaveVocab, onClose }: {
  initialTab?: Tab; bindings: Bindings; vocab: Vocab;
  onSaveBindings: (b: Bindings) => void; onSaveVocab: (v: Vocab) => void; onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>(initialTab ?? "shortcuts");
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal settings" style={{ width: 860, gridTemplateRows: "auto auto minmax(0, 1fr)" }} onClick={(e) => e.stopPropagation()}>
        <header><h2>Settings</h2><button className="btn small" style={{ marginLeft: "auto" }} onClick={onClose}>Close</button></header>
        <div className="tabs">
          <button className={`tab ${tab === "shortcuts" ? "on" : ""}`} onClick={() => setTab("shortcuts")}>Keyboard</button>
          <button className={`tab ${tab === "lists" ? "on" : ""}`} onClick={() => setTab("lists")}>Lists</button>
        </div>
        <div className="settings-body">
          {tab === "shortcuts" && <ShortcutsPane bindings={bindings} onSave={onSaveBindings} />}
          {tab === "lists" && <ListsPane vocab={vocab} onSave={onSaveVocab} />}
        </div>
      </div>
    </div>
  );
}

function ShortcutsPane({ bindings, onSave }: { bindings: Bindings; onSave: (b: Bindings) => void }) {
  const [draft, setDraft] = useState<Bindings>(bindings);
  const [listening, setListening] = useState<ActionId | null>(null);
  const [note, setNote] = useState<string>("");
  const dirty = JSON.stringify(draft) !== JSON.stringify(bindings);

  // While a row listens, the next key press becomes its binding and reaches nothing else in the app.
  useEffect(() => {
    if (!listening) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      const combo = comboOf(e);
      if (!combo) return;                                   // a lone modifier: keep listening
      if (combo === "Escape" && draft[listening] !== "Escape") { setListening(null); setNote(""); return; }
      const clash = ACTIONS.find((a) => a.id !== listening && draft[a.id] === combo);
      const next = { ...draft, [listening]: combo };
      if (clash) next[clash.id] = "";
      setDraft(next);
      setNote(clash ? `${showKey(combo)} was on “${clash.label}”, which is now unbound.` : "");
      setListening(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [listening, draft]);

  const groups = [...new Set(ACTIONS.map((a) => a.group))];
  return (
    <>
      <div className="dim" style={{ fontSize: 11, padding: "10px 14px 0" }}>
        Click a key, then press the new combination{isMac ? " (⌘ ⌥ ⇧ ⌃ work)" : " (Ctrl, Alt and Shift work)"}. Esc cancels.
        Saved in the work folder, so they follow every game.
        {note && <div className="accent" style={{ marginTop: 6 }}>{note}</div>}
      </div>
      <div className="keys">
        {groups.map((g) => (
          <div key={g} className="keygroup">
            <h3>{g}</h3>
            {ACTIONS.filter((a) => a.group === g).map((a) => (
              <div key={a.id} className="keyrow">
                <span>{a.label}</span>
                <button className={`kbdbtn ${listening === a.id ? "listening" : ""} ${draft[a.id] !== a.key ? "changed" : ""}`}
                  title={draft[a.id] !== a.key ? `Default: ${showKey(a.key)}` : "Click, then press the new keys"}
                  onClick={() => setListening(listening === a.id ? null : a.id)}>
                  {listening === a.id ? "press keys…" : showKey(draft[a.id] || undefined)}
                </button>
                <span className="rowtools">
                  {draft[a.id] !== a.key && <i title={`Back to ${showKey(a.key)}`} onClick={() => setDraft({ ...draft, [a.id]: a.key })}>↺</i>}
                  {draft[a.id] && <i title="Unbind" onClick={() => setDraft({ ...draft, [a.id]: "" })}>✕</i>}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <footer>
        <button className="btn" onClick={() => { const d = {} as Bindings; for (const a of ACTIONS) d[a.id] = a.key; setDraft(d); setNote("All keys back to their defaults. Save to keep."); }}>Reset all</button>
        <button className="btn primary" disabled={!dirty} onClick={() => onSave(draft)}>Save keys</button>
      </footer>
    </>
  );
}
