// Drop-frame aware timecode, mirroring engine/reelquick/timecode.py.

export interface Rate { num: number; den: number }

export const fps = (r: Rate) => r.num / r.den;
export const nominal = (r: Rate) => Math.round(fps(r));
const dropCapable = (r: Rate) => r.den === 1001 && (nominal(r) === 30 || nominal(r) === 60);

export function framesToTc(frames: number, rate: Rate, drop: boolean): string {
  const n = nominal(rate);
  let f = Math.max(0, Math.floor(frames));
  if (drop && dropCapable(rate)) {
    const d = 2 * Math.floor(n / 30);
    const per10 = n * 600 - d * 9;
    const perMin = n * 60 - d;
    const tens = Math.floor(f / per10);
    const rem = f % per10;
    f += rem > d ? d * 9 * tens + d * Math.floor((rem - d) / perMin) : d * 9 * tens;
  }
  const ff = f % n;
  const ss = Math.floor(f / n) % 60;
  const mm = Math.floor(f / (n * 60)) % 60;
  const hh = Math.floor(f / (n * 3600));
  const p = (x: number) => String(x).padStart(2, "0");
  return `${p(hh)}:${p(mm)}:${p(ss)}${drop ? ";" : ":"}${p(ff)}`;
}

export function durationText(frames: number, rate: Rate): string {
  const s = frames / fps(rate);
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${(s - m * 60).toFixed(0)}s`;
}

export function clockText(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "--:--";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
