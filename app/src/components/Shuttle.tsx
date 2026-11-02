import { useRef, useState } from "react";

// Spring-loaded shuttle: drag the knob off centre and the picture runs that way, faster the further you
// go; let go and it snaps back to a stop. The same control shows the rate J / K / L chose.
export const DETENTS = [0.25, 0.5, 1, 2, 4, 8];
const DEAD = 0.08;

export function rateToX(rate: number): number {
  if (!rate) return 0;
  const i = DETENTS.findIndex((d) => d >= Math.abs(rate));
  const idx = i < 0 ? DETENTS.length - 1 : i;
  return Math.sign(rate) * (DEAD + ((idx + 0.5) / DETENTS.length) * (1 - DEAD));
}

export function xToRate(x: number): number {
  const a = Math.abs(x);
  if (a < DEAD) return 0;
  const i = Math.min(DETENTS.length - 1, Math.floor(((a - DEAD) / (1 - DEAD)) * DETENTS.length));
  return Math.sign(x) * DETENTS[i];
}

export function Shuttle({ rate, onRate, disabled }: { rate: number; onRate: (r: number) => void; disabled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const x = drag ?? rateToX(rate);
  const at = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
  };
  const down = (e: React.PointerEvent) => {
    if (disabled || e.button !== 0) return;
    e.preventDefault();
    ref.current!.setPointerCapture(e.pointerId);
    const nx = at(e); setDrag(nx); onRate(xToRate(nx));
  };
  const move = (e: React.PointerEvent) => { if (drag === null) return; const nx = at(e); setDrag(nx); onRate(xToRate(nx)); };
  const release = () => { if (drag === null) return; setDrag(null); onRate(0); };
  const shown = drag !== null ? xToRate(drag) : rate;
  return (
    <div className={`shuttle ${disabled ? "off" : ""}`} ref={ref} title="Shuttle: drag away from the centre to run the picture, further is faster, let go to stop"
      onPointerDown={down} onPointerMove={move} onPointerUp={release} onPointerCancel={release} onLostPointerCapture={release}>
      <div className="rail">
        {[...DETENTS.map((d) => -d), 0, ...DETENTS].map((d) => <i key={d} className={d === 0 ? "c" : ""} style={{ left: `${((rateToX(d) + 1) / 2) * 100}%` }} />)}
      </div>
      <div className={`knob ${shown ? "live" : ""}`} style={{ left: `${((x + 1) / 2) * 100}%` }}>
        <span>{shown ? `${shown < 0 ? "◀" : "▶"} ${Math.abs(shown)}×` : "▮▮"}</span>
      </div>
    </div>
  );
}
