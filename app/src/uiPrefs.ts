// Small UI conveniences kept in <work root>/prefs.json ("ui"), because the app window loses its
// browser storage every launch (private mode, a new port each time). localStorage stays as a
// fallback for running in a plain browser before the first state arrives.
import { api } from "./api";

let cache: Record<string, unknown> = {};
const pending: Record<string, unknown> = {};
let timer: ReturnType<typeof setTimeout> | null = null;

/** Called with the server's prefs.ui whenever state arrives; the server copy wins over local storage. */
export function loadUiPrefs(ui: Record<string, unknown> | undefined) {
  if (ui) cache = { ...cache, ...ui, ...pending };
}

export function getUi<T>(key: string, fallback: T): T {
  if (key in cache) return cache[key] as T;
  try { const v = localStorage.getItem(`rq.${key}`); if (v !== null) return JSON.parse(v) as T; } catch { /* ignore */ }
  return fallback;
}

/** Saves a value; writes are batched so a slider drag sends one request, not hundreds. */
export function setUi(key: string, value: unknown) {
  cache[key] = value; pending[key] = value;
  try { localStorage.setItem(`rq.${key}`, JSON.stringify(value)); } catch { /* ignore */ }
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    const batch = { ...pending };
    for (const k of Object.keys(batch)) delete pending[k];
    timer = null;
    api.prefs({ ui: batch }).catch(() => { Object.assign(pending, batch); });
  }, 600);
}
