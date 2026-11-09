// Keyboard actions, their default keys, and the user's own bindings (stored in <work root>/prefs.json).
// Defaults follow the big editors where they agree: J/K/L, I/O, Alt+X to clear in and out (Resolve,
// Premiere and Final Cut all use Alt/Option+X), Shift+I / Shift+O to go to in and out.

export type ActionId =
  | "play" | "pause" | "shuttle_back" | "shuttle_fwd" | "shuttle_back_slow" | "shuttle_fwd_slow"
  | "step_back" | "step_fwd" | "back_1s" | "fwd_1s" | "back_10s" | "fwd_10s" | "go_start" | "go_end"
  | "mark_in" | "mark_out" | "clear_in" | "clear_out" | "clear_marks" | "go_in" | "go_out" | "add_clip"
  | "prev_marker" | "next_marker" | "add_marker" | "still" | "named_still" | "source_frame"
  | "delete_clip" | "undo" | "redo" | "escape" | "help";

export const ACTIONS: { id: ActionId; label: string; key: string; group: string }[] = [
  { id: "play", label: "Play / pause", key: "Space", group: "Playback" },
  { id: "shuttle_back", label: "Shuttle back (again for faster)", key: "J", group: "Playback" },
  { id: "pause", label: "Pause", key: "K", group: "Playback" },
  { id: "shuttle_fwd", label: "Shuttle forward (again for faster)", key: "L", group: "Playback" },
  { id: "shuttle_back_slow", label: "Slow shuttle back", key: "Shift+J", group: "Playback" },
  { id: "shuttle_fwd_slow", label: "Slow shuttle forward", key: "Shift+L", group: "Playback" },
  { id: "step_back", label: "Back one frame", key: "ArrowLeft", group: "Navigate" },
  { id: "step_fwd", label: "Forward one frame", key: "ArrowRight", group: "Navigate" },
  { id: "back_1s", label: "Back one second", key: "Shift+ArrowLeft", group: "Navigate" },
  { id: "fwd_1s", label: "Forward one second", key: "Shift+ArrowRight", group: "Navigate" },
  { id: "back_10s", label: "Back ten seconds", key: "Mod+ArrowLeft", group: "Navigate" },
  { id: "fwd_10s", label: "Forward ten seconds", key: "Mod+ArrowRight", group: "Navigate" },
  { id: "go_start", label: "Go to start", key: "Home", group: "Navigate" },
  { id: "go_end", label: "Go to end", key: "End", group: "Navigate" },
  { id: "prev_marker", label: "Previous marker", key: "[", group: "Navigate" },
  { id: "next_marker", label: "Next marker", key: "]", group: "Navigate" },
  { id: "go_in", label: "Go to in", key: "Shift+I", group: "Navigate" },
  { id: "go_out", label: "Go to out", key: "Shift+O", group: "Navigate" },
  { id: "mark_in", label: "Mark in", key: "I", group: "Mark" },
  { id: "mark_out", label: "Mark out", key: "O", group: "Mark" },
  { id: "clear_in", label: "Clear in", key: "Alt+I", group: "Mark" },
  { id: "clear_out", label: "Clear out", key: "Alt+O", group: "Mark" },
  { id: "clear_marks", label: "Clear in and out", key: "Alt+X", group: "Mark" },
  { id: "add_clip", label: "Add the in–out range as a clip", key: "Enter", group: "Mark" },
  { id: "add_marker", label: "Add marker", key: "M", group: "Mark" },
  { id: "still", label: "Still: set the clip's still, add another, or mark one for the next clip", key: "S", group: "Mark" },
  { id: "named_still", label: "Save this frame as a named still", key: "Shift+S", group: "Mark" },
  { id: "delete_clip", label: "Delete the selected clip", key: "Delete", group: "Mark" },
  { id: "escape", label: "Clear in, out and selection", key: "Escape", group: "Mark" },
  { id: "source_frame", label: "Show the exact source frame while paused", key: "F", group: "View" },
  { id: "undo", label: "Undo", key: "Mod+Z", group: "Edit" },
  { id: "redo", label: "Redo", key: "Mod+Shift+Z", group: "Edit" },
  { id: "help", label: "Keyboard shortcuts", key: "?", group: "Edit" },
];

export type Bindings = Record<ActionId, string>;

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export function bindingsFrom(overrides: Record<string, string> | undefined): Bindings {
  const b = {} as Bindings;
  for (const a of ACTIONS) b[a.id] = overrides?.[a.id] ?? a.key;
  return b;
}

/** Only the bindings that differ from the defaults, for prefs.json. */
export function overridesOf(b: Bindings): Record<string, string> {
  const out: Record<string, string> = {};
  for (const a of ACTIONS) if (b[a.id] !== a.key) out[a.id] = b[a.id];
  return out;
}

// Keys whose printed character already carries Shift ("?", "{") are matched without it.
const keyName = (e: KeyboardEvent): string => {
  if (e.code?.startsWith("Key")) return e.code.slice(3);          // layout-independent letters, and Option+X on a Mac
  if (e.code?.startsWith("Digit")) return e.code.slice(5);
  if (e.key === " ") return "Space";
  if (e.key.length === 1) return e.key.toUpperCase();
  return e.key;
};

/** The combination a key press makes, in the same form as the bindings ("Mod+Shift+Z", "Alt+X", "?"). */
export function comboOf(e: KeyboardEvent): string | null {
  if (["Shift", "Control", "Alt", "Meta", "OS"].includes(e.key)) return null;
  const k = keyName(e);
  const parts: string[] = [];
  if (isMac ? e.metaKey : e.ctrlKey) parts.push("Mod");
  if (isMac && e.ctrlKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  const printable = e.key.length === 1 && !/[a-z0-9]/i.test(e.key) && !e.code?.startsWith("Key") && !e.code?.startsWith("Digit");
  if (e.shiftKey && !printable) parts.push("Shift");
  parts.push(k);
  return parts.join("+");
}

const SHOW: Record<string, string> = {
  ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Escape: "Esc", Enter: "Enter", Delete: "Delete",
  Backspace: "Backspace", Space: "Space", Home: "Home", End: "End",
};

/** A binding as people read it: "⌘⇧Z" on a Mac, "Ctrl+Shift+Z" elsewhere. */
export function showKey(combo: string | undefined): string {
  if (!combo) return "unbound";
  const parts = combo.split("+").filter(Boolean);
  const key = parts.pop() ?? "";
  const k = SHOW[key] ?? key;
  if (isMac) {
    const m: Record<string, string> = { Mod: "⌘", Ctrl: "⌃", Alt: "⌥", Shift: "⇧" };
    return parts.map((p) => m[p] ?? p).join("") + k;
  }
  const m: Record<string, string> = { Mod: "Ctrl", Ctrl: "Ctrl", Alt: "Alt", Shift: "Shift" };
  return [...parts.map((p) => m[p] ?? p), k].join("+");
}

/** "Mark in (I)": a tooltip that carries the live binding. */
export function tip(label: string, b: Bindings, id: ActionId): string {
  return `${label}  (${showKey(b[id])})`;
}

export function actionFor(b: Bindings, combo: string): ActionId | null {
  for (const a of ACTIONS) if (b[a.id] === combo) return a.id;
  return null;
}
