import { useEffect, useMemo, useRef, useState } from "react";
import type { Clip, FilmstripIndex, Marker } from "../api";

const THUMB_W = 160;

export function Filmstrip({ index, nbFrames, frame, markers, clips, inPoint, outPoint, onSeek, follow }: {
  index: FilmstripIndex; nbFrames: number; frame: number; markers: Marker[]; clips: Clip[];
  inPoint: number | null; outPoint: number | null; onSeek: (frame: number) => void; follow: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ left: 0, width: 1200 });
  const perThumb = index.interval_frames;
  const total = index.count * THUMB_W;
  const px = (f: number) => (f / perThumb) * THUMB_W;

  useEffect(() => {
    const el = ref.current; if (!el) return;
    const update = () => setView({ left: el.scrollLeft, width: el.clientWidth });
    update();
    el.addEventListener("scroll", update);
    const ro = new ResizeObserver(update); ro.observe(el);
    return () => { el.removeEventListener("scroll", update); ro.disconnect(); };
  }, []);

  // Keep the playhead in view while playing or after a jump.
  useEffect(() => {
    const el = ref.current; if (!el || !follow) return;
    const x = px(frame);
    if (x < el.scrollLeft + 40 || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = x - el.clientWidth / 3;
  }, [frame, follow]);

  const first = Math.max(0, Math.floor(view.left / THUMB_W) - 3);
  const last = Math.min(index.count - 1, Math.ceil((view.left + view.width) / THUMB_W) + 3);
  const visible = useMemo(() => index.frames.slice(first, last + 1), [index, first, last]);

  const overview = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = overview.current; if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const g = c.getContext("2d")!; g.scale(dpr, dpr);
    g.fillStyle = "#15171a"; g.fillRect(0, 0, w, h);
    const x = (f: number) => (f / nbFrames) * w;
    // viewport
    g.fillStyle = "rgba(255,255,255,0.08)";
    g.fillRect(x((view.left / THUMB_W) * perThumb), 0, Math.max(2, x((view.width / THUMB_W) * perThumb)), h);
    for (const cl of clips) { g.fillStyle = "#FEB404"; g.fillRect(x(cl.in_frame), h - 6, Math.max(2, x(cl.out_frame) - x(cl.in_frame)), 5); }
    for (const m of markers) { g.fillStyle = m.source === "user" ? "#F4F5F5" : "#FEB404"; g.fillRect(x(m.frame), 2, 1.5, h - 10); }
    g.fillStyle = "#F4F5F5"; g.fillRect(x(frame) - 1, 0, 2, h);
  }, [frame, markers, clips, nbFrames, view, perThumb]);

  const seekFromOverview = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    onSeek(Math.round(((e.clientX - r.left) / r.width) * nbFrames));
  };

  return (
    <div>
      <div className="strip" ref={ref}>
        <div className="track" style={{ width: total }}>
          {visible.map((t) => (
            <div key={t.i} className="thumb" style={{ left: t.i * THUMB_W, width: THUMB_W }} title={t.tc}
              onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onSeek(t.frame + Math.round(((e.clientX - r.left) / r.width) * perThumb)); }}>
              <img src={`/strip/${t.file}`} loading="lazy" alt="" />
            </div>
          ))}
          {markers.filter((m) => px(m.frame) > view.left - 50 && px(m.frame) < view.left + view.width + 50).map((m) => (
            <div key={`${m.frame}-${m.source}`} className={`marker ${m.source}`} style={{ left: px(m.frame) }}
              data-label={`${m.tc}  ${[m.kind, m.shell, m.description].filter(Boolean).join(" · ")}`} onClick={() => onSeek(m.frame)} />
          ))}
          {clips.map((c) => <div key={c.id} className="clipspan" style={{ left: px(c.in_frame), width: Math.max(2, px(c.out_frame) - px(c.in_frame)) }} />)}
          {inPoint !== null && <div className="inout" style={{ left: px(inPoint), width: Math.max(2, px((outPoint ?? frame)) - px(inPoint)) }} />}
          <div className="playhead" style={{ left: px(frame) }} />
        </div>
      </div>
      <div className="overview" onMouseDown={seekFromOverview} onMouseMove={(e) => { if (e.buttons === 1) seekFromOverview(e); }}>
        <canvas ref={overview} />
      </div>
    </div>
  );
}
