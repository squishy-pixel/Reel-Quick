import { useState } from "react";
import type { AppState, Job, Marker, MediaInfo } from "../api";
import { clockText } from "../timecode";

export function JobsPanel({ state, onFilmstrip, onProxy, onCancel }: {
  state: AppState; onFilmstrip: (force: boolean) => void; onProxy: (height: number) => void; onCancel: (id: string) => void;
}) {
  const latest = (kind: string) => [...state.jobs].reverse().find((j) => j.kind === kind);
  const ws = state.workspace!;
  const [height, setHeight] = useState(1080);
  const Row = ({ name, job, ready, readyText, actions }: { name: string; job?: Job; ready: boolean; readyText: string; actions: React.ReactNode }) => {
    const running = job?.status === "running";
    return (
      <div className="jobrow">
        <span className="name">{name}</span>
        <div>
          {running ? (
            <>
              <div className="bar"><i style={{ width: `${job!.progress * 100}%` }} /></div>
              <div className="status num">{Math.round(job!.progress * 100)}% · {clockText(job!.elapsed)} elapsed{job!.eta != null ? ` · ${clockText(job!.eta)} left` : ""}{job!.detail?.stage === "join" ? " · joining" : ""}</div>
            </>
          ) : job?.status === "failed" ? (
            <div className="status" style={{ color: "var(--red)" }} title={job.error ?? ""}>failed: {job.error?.slice(0, 80)}</div>
          ) : ready ? <div className="status"><span className="ok" style={{ color: "var(--green)" }}>✓</span> {readyText}</div> : <div className="status dim">not built</div>}
        </div>
        <div style={{ display: "flex", gap: 4 }}>{running ? <button className="btn small danger" onClick={() => onCancel(job!.id)}>Stop</button> : actions}</div>
      </div>
    );
  };
  const fs = latest("filmstrip"), px = latest("proxy");
  return (
    <div className="panel">
      <div className="panel-head"><h2>Scan</h2><span className="dim" style={{ fontSize: 10 }}>{state.engine.lut ? `LUT ${state.engine.lut.name}` : "built-in SDR"}{state.engine.nvenc ? " · NVENC" : ""}</span></div>
      <Row name="Filmstrip" job={fs} ready={ws.has_filmstrip} readyText="ready" actions={<button className="btn small" onClick={() => onFilmstrip(ws.has_filmstrip)}>{ws.has_filmstrip ? "Rebuild" : "Build"}</button>} />
      <Row name="Proxy" job={px} ready={ws.has_proxy} readyText="ready" actions={<>
        <select className="text" style={{ padding: "2px 4px", fontSize: 10 }} value={height} onChange={(e) => setHeight(Number(e.target.value))}><option value={1080}>1080p</option><option value={720}>720p</option></select>
        <button className="btn small" onClick={() => onProxy(height)}>{ws.has_proxy ? "Rebuild" : "Build"}</button></>} />
    </div>
  );
}

export function MarkersPanel({ markers, info, onSeek, onImport, onDelete, offset, onOffset }: {
  markers: Marker[]; info: MediaInfo; onSeek: (f: number) => void; onImport: () => void; onDelete: (frame: number) => void;
  offset: number; onOffset: (s: number) => void;
}) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Markers <span className="dim" style={{ letterSpacing: 0 }}>{markers.length}</span></h2>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span className="dim" style={{ fontSize: 10 }}>log offset</span>
          <input className="text num" style={{ width: 64, padding: "2px 5px", fontSize: 11 }} type="number" step="1" defaultValue={offset / 3600} key={offset}
            onBlur={(e) => { const h = Number(e.target.value); if (h * 3600 !== offset) onOffset(h * 3600); }} title="Hours added to log times to reach file timecode (-3 for an Eastern log on Pacific timecode)" />
          <span className="dim" style={{ fontSize: 10 }}>h</span>
          <button className="btn small" onClick={onImport}>Import CSV</button>
        </div>
      </div>
      <div className="markers">
        {markers.length === 0 && <div className="dim" style={{ fontSize: 11 }}>Import the tech's log, or press <span className="kbd">M</span> to drop a marker at the playhead.</div>}
        {markers.map((m) => (
          <div key={`${m.frame}-${m.source}`} className="markerrow" onClick={() => onSeek(m.frame)}>
            <span className="tc">{m.tc}</span>
            <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {m.kind && <span className="accent" style={{ marginRight: 6 }}>{m.kind}</span>}{m.shell && <span className="muted" style={{ marginRight: 6 }}>{m.shell}</span>}{m.description}
            </span>
            <span className="del dim" onClick={(e) => { e.stopPropagation(); onDelete(m.frame); }}>✕</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function RenderPanel({ job, clipCount, selectedId, outDir, onRender, onRenderSelected, onCancel, onPickOut }: {
  job?: Job; clipCount: number; selectedId: string | null; outDir: string;
  onRender: () => void; onRenderSelected: () => void; onCancel: () => void; onPickOut: () => void;
}) {
  const running = job?.status === "running";
  const rows: { id: string; name: string; status: string; progress: number; error?: string }[] = job?.detail?.clips ?? [];
  return (
    <div className="renderpanel">
      <div className="overall">
        <h2>Export</h2>
        {job && <span className={`pct ${job.status === "failed" ? "" : ""}`}>{Math.round(job.progress * 100)}%</span>}
      </div>
      {job && (
        <>
          <div className={`bar big ${job.status === "done" ? "done" : job.status === "failed" ? "failed" : ""}`}><i style={{ width: `${job.progress * 100}%` }} /></div>
          <div className="status dim num" style={{ fontSize: 11, marginTop: 3 }}>
            {running ? `${clockText(job.elapsed)} elapsed${job.eta != null ? ` · about ${clockText(job.eta)} left` : ""}` : job.status === "done" ? `done · ${job.result?.rendered ?? 0} clip(s) in ${clockText(job.elapsed)}${job.result?.failed?.length ? ` · ${job.result.failed.length} failed` : ""}` : job.status}
          </div>
          {rows.length > 0 && (
            <div className="renderrows">
              {rows.map((r) => (
                <div key={r.id} className="r" title={r.error ?? ""}>
                  <div>
                    <div className="n">{r.name}</div>
                    <div className={`bar ${r.status === "done" ? "done" : r.status === "failed" ? "failed" : ""}`}><i style={{ width: `${(r.status === "done" ? 1 : r.progress) * 100}%` }} /></div>
                  </div>
                  <span className="num dim" style={{ textAlign: "right" }}>{r.status === "done" ? "✓" : r.status === "failed" ? "✕" : r.status === "running" ? `${Math.round(r.progress * 100)}%` : "·"}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
        {running ? <button className="btn danger" onClick={onCancel}>Cancel</button> : (
          <>
            <button className="btn primary" disabled={!clipCount} onClick={onRender}>Render all {clipCount ? `(${clipCount})` : ""}</button>
            <button className="btn" disabled={!selectedId} onClick={onRenderSelected}>Selected</button>
          </>
        )}
        <span className="dim" style={{ flex: 1, minWidth: 0, fontSize: 10, textAlign: "right", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", direction: "rtl" }} title={outDir}>{outDir}</span>
        <button className="btn small" onClick={onPickOut} title="Choose the output folder">…</button>
      </div>
    </div>
  );
}
