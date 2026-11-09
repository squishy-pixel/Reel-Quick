import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Clip, FilmstripIndex, Marker } from "../api";
import { Tc } from "./Tc";

export const THUMB_MIN = 80, THUMB_MAX = 320;

type Snap = { frame: number; label: string };

export function Filmstrip({ index, nbFrames, fps, frame, markers, clips, selectedId, inPoint, outPoint, pendingStill, onSeek, follow, reveal, thumbW, onThumbW, tcOf, proxySeconds }: {
  index: FilmstripIndex; nbFrames: number; fps: number; frame: number; markers: Marker[]; clips: Clip[]; selectedId: string | null;
  inPoint: number | null; outPoint: number | null; pendingStill: number | null; onSeek: (frame: number) => void; follow: boolean;
  reveal: { frame: number; n: number } | null;    // an explicit jump (clip, marker, go-to): scroll there whatever "follow" says
  thumbW: number; onThumbW: (w: number) => void; tcOf: (f: number) => string;
  proxySeconds: number;                           // how far the proxy reaches; beyond it the video cannot show frames yet
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ left: 0, width: 1200 });
  const perThumb = index.interval_frames;
  const total = index.count * thumbW;
  const px = useCallback((f: number) => (f / perThumb) * thumbW, [perThumb, thumbW]);
  const programmatic = useRef(false);
  const userScrolledAt = useRef(0);

  useEffect(() => {
    const el = ref.current; if (!el) return;
    const update = () => {
      setView({ left: el.scrollLeft, width: el.clientWidth });
      if (!programmatic.current) userScrolledAt.current = performance.now();
      programmatic.current = false;
    };
    update();
    el.addEventListener("scroll", update);
    const ro = new ResizeObserver(update); ro.observe(el);
    return () => { el.removeEventListener("scroll", update); ro.disconnect(); };
  }, []);

  // Follow the playhead unless the user scrolled the strip themselves in the last few seconds.
  useEffect(() => {
    const el = ref.current; if (!el || !follow) return;
    if (performance.now() - userScrolledAt.current < 4000) return;
    const x = px(frame);
    if (x < el.scrollLeft + 40 || x > el.scrollLeft + el.clientWidth - 40) { programmatic.current = true; el.scrollLeft = x - el.clientWidth / 3; }
  }, [frame, follow, px]);

  // An explicit jump always brings its frame into view, centred.
  useEffect(() => {
    const el = ref.current; if (!el || !reveal) return;
    programmatic.current = true; userScrolledAt.current = 0;
    el.scrollLeft = px(reveal.frame) - el.clientWidth / 2;
  }, [reveal]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ctrl+wheel zooms the thumbnails around the cursor.
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const next = Math.max(THUMB_MIN, Math.min(THUMB_MAX, Math.round(thumbW * (e.deltaY < 0 ? 1.15 : 1 / 1.15))));
      if (next === thumbW) return;
      const atFrame = ((el.scrollLeft + e.offsetX) / thumbW) * perThumb;
      onThumbW(next);
      requestAnimationFrame(() => { programmatic.current = true; el.scrollLeft = (atFrame / perThumb) * next - e.offsetX; });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [thumbW, perThumb, onThumbW]);

  const first = Math.max(0, Math.floor(view.left / thumbW) - 3);
  const last = Math.min(index.count - 1, Math.ceil((view.left + view.width) / thumbW) + 3);
  const visible = useMemo(() => index.frames.slice(first, last + 1), [index, first, last]);
  const timed = useMemo(() => markers.filter((m) => m.frame !== null), [markers]);
  const stills = useMemo(() => clips.flatMap((c) => [c.still_frame, ...(c.extra_stills || [])]).filter((f): f is number => f !== null && f !== undefined), [clips]);
  const selected = useMemo(() => clips.find((c) => c.id === selectedId) ?? null, [clips, selectedId]);
  const thumbH = Math.round(thumbW * 9 / 16);
  const stripH = thumbH + 26;
  const proxyEnd = Math.min(nbFrames, Math.floor(proxySeconds * fps));

  // Everything on the timeline a click can land on exactly.
  const snaps = useMemo<Snap[]>(() => [
    ...timed.map((m) => ({ frame: m.frame!, label: `marker · ${[m.shell, m.camera, m.description].filter(Boolean).join(" · ") || m.tc}` })),
    ...stills.map((f) => ({ frame: f, label: "still" })),
    ...clips.flatMap((c, i) => [{ frame: c.in_frame, label: `clip ${String(i + 1).padStart(2, "0")} in` }, { frame: c.out_frame, label: `clip ${String(i + 1).padStart(2, "0")} out` }]),
  ], [timed, stills, clips]);

  // ---- timeline (overview) -----------------------------------------------
  const overview = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<{ x: number; frame: number; label?: string } | null>(null);
  const dragging = useRef(false);
  useEffect(() => {
    const c = overview.current; if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const g = c.getContext("2d")!; g.scale(dpr, dpr);
    g.fillStyle = "#15171a"; g.fillRect(0, 0, w, h);
    const x = (f: number) => (f / nbFrames) * w;
    // the stretch the proxy has not reached yet is hatched: the video cannot play there, only source frames show
    if (proxyEnd < nbFrames) {
      g.save(); g.beginPath(); g.rect(x(proxyEnd), 0, w - x(proxyEnd), h); g.clip();
      g.strokeStyle = "rgba(255,255,255,0.08)"; g.lineWidth = 1;
      for (let i = x(proxyEnd) - h; i < w; i += 7) { g.beginPath(); g.moveTo(i, h); g.lineTo(i + h, 0); g.stroke(); }
      g.restore();
      g.fillStyle = "rgba(254,180,4,0.6)"; g.fillRect(x(proxyEnd) - 0.5, 0, 1, h);
    }
    g.fillStyle = "rgba(255,255,255,0.07)";
    g.fillRect(x((view.left / thumbW) * perThumb), 0, Math.max(2, x((view.width / thumbW) * perThumb)), h);
    for (const cl of clips) { g.fillStyle = cl.id === selectedId ? "#F4F5F5" : "#FEB404"; g.fillRect(x(cl.in_frame), h - 7, Math.max(2, x(cl.out_frame) - x(cl.in_frame)), 6); }
    if (selected) { g.fillStyle = "rgba(244,245,245,0.18)"; g.fillRect(x(selected.in_frame), 0, Math.max(2, x(selected.out_frame) - x(selected.in_frame)), h); }
    for (const m of timed) { g.fillStyle = m.source === "user" ? "#F4F5F5" : "#FEB404"; g.fillRect(x(m.frame!) - 0.5, 2, 1.5, h - 10); }
    // still diamonds sit on the timeline's centre line so they read as belonging here, not to the strip below
    for (const f of stills) { const cy = h / 2; g.fillStyle = "#F4F5F5"; g.beginPath(); g.moveTo(x(f), cy - 6); g.lineTo(x(f) + 6, cy); g.lineTo(x(f), cy + 6); g.lineTo(x(f) - 6, cy); g.closePath(); g.fill(); g.strokeStyle = "#000"; g.lineWidth = 1; g.stroke(); }
    if (inPoint !== null) { g.fillStyle = "rgba(254,180,4,0.35)"; g.fillRect(x(inPoint), 0, Math.max(2, x(outPoint ?? frame) - x(inPoint)), h); }
    g.fillStyle = "#F4F5F5"; g.fillRect(x(frame) - 1.5, 0, 3, h);
  }, [frame, timed, stills, clips, selected, selectedId, nbFrames, view, perThumb, thumbW, inPoint, outPoint, proxyEnd]);

  // A click within a few pixels of a marker, still or clip edge lands on it exactly.
  const frameAt = (e: MouseEvent | React.MouseEvent, el: HTMLElement): { frame: number; label?: string } => {
    const r = el.getBoundingClientRect();
    const raw = Math.max(0, Math.min(nbFrames - 1, Math.round(((e.clientX - r.left) / r.width) * nbFrames)));
    const radius = (6 / r.width) * nbFrames;
    let best: Snap | null = null;
    for (const s of snaps) if (Math.abs(s.frame - raw) <= radius && (!best || Math.abs(s.frame - raw) < Math.abs(best.frame - raw))) best = s;
    return best ? { frame: best.frame, label: best.label } : { frame: raw };
  };
  const onDown = (e: React.MouseEvent<HTMLDivElement>) => {
    dragging.current = true;
    const el = e.currentTarget;
    onSeek(frameAt(e, el).frame);
    const move = (ev: MouseEvent) => { if (dragging.current) onSeek(frameAt(ev, el).frame); };
    const up = () => { dragging.current = false; window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  };

  return (
    <div>
      {/* The timeline sits on top, where editors put the viewer's scrub bar; the thumbnails hang below it. */}
      <div className="timeline" onMouseDown={onDown}
        onMouseMove={(e) => { const at = frameAt(e, e.currentTarget); setHover({ x: e.nativeEvent.offsetX, ...at }); }} onMouseLeave={() => setHover(null)}>
        <canvas ref={overview} />
        {hover && <div className="hovertc" style={{ left: Math.min(Math.max(hover.x, 50), (overview.current?.clientWidth ?? 200) - 50) }}><Tc value={tcOf(hover.frame)} />{hover.label && <span className="dim"> {hover.label}</span>}</div>}
      </div>
      <div className="strip" ref={ref} style={{ height: stripH }}>
        <div className="track" style={{ width: total }}>
          {visible.map((t) => (
            <div key={t.i} className="thumb" style={{ left: t.i * thumbW, width: thumbW, height: thumbH }} title={t.tc}
              onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onSeek(t.frame + Math.round(((e.clientX - r.left) / r.width) * perThumb)); }}>
              <img src={`/strip/${t.file}`} loading="lazy" alt="" />
            </div>
          ))}
          {timed.filter((m) => px(m.frame!) > view.left - 50 && px(m.frame!) < view.left + view.width + 50).map((m) => (
            <div key={`${m.frame}-${m.source}`} className={`marker ${m.source}`} style={{ left: px(m.frame!) - 5, height: thumbH / 2 + 18 }}
              data-label={`${m.tc}  ${[m.shell, m.camera, m.description].filter(Boolean).join(" · ")}`} onClick={(e) => { e.stopPropagation(); onSeek(m.frame!); }}>
              <i />
            </div>
          ))}
          {stills.filter((f) => px(f) > view.left - 50 && px(f) < view.left + view.width + 50).map((f, i) => (
            <div key={`s${i}`} className="stillmark" style={{ left: px(f) - 6 }} title={`still ${tcOf(f)}`} onClick={(e) => { e.stopPropagation(); onSeek(f); }} />
          ))}
          {pendingStill !== null && <div className="stillmark pending" style={{ left: px(pendingStill) - 6 }} title="still, waiting for a clip" />}
          {clips.map((c) => <div key={c.id} className={`clipspan ${c.id === selectedId ? "selected" : ""}`} style={{ left: px(c.in_frame), width: Math.max(2, px(c.out_frame) - px(c.in_frame)), top: thumbH + 20 }} />)}
          {selected && (
            <div className="clipsel" style={{ left: px(selected.in_frame), width: Math.max(2, px(selected.out_frame) - px(selected.in_frame)), height: thumbH }}>
              <b className="l" /><b className="r" />
            </div>
          )}
          {inPoint !== null && <div className="inout" style={{ left: px(inPoint), width: Math.max(2, px((outPoint ?? frame)) - px(inPoint)), height: thumbH }} />}
          <div className="playhead" style={{ left: px(frame) - 1 }} />
        </div>
      </div>
      <div className="legend">
        <span><i className="sw mk" />log marker</span>
        <span><i className="sw mk user" />your marker</span>
        <span><i className="sw st" />still</span>
        <span><i className="sw cl" />clip</span>
        <span><i className="sw sel" />selected clip</span>
        <span><i className="sw io" />in–out</span>
        {proxyEnd < nbFrames && <span><i className="sw np" />proxy not there yet</span>}
        <span className="dim" style={{ marginLeft: "auto" }}>click near a mark to land on it</span>
      </div>
    </div>
  );
}
