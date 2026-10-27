import { useState } from "react";
import type { AppState, Job, Marker, MediaInfo, Vocab } from "../api";
import { clockText } from "../timecode";

// Seconds of program per second of work, learned from finished jobs; defaults from the first real game.
export function rateFor(kind: string): number {
  try { const v = localStorage.getItem(`rq.rate.${kind}`); if (v) return Number(v); } catch { /* ignore */ }
  return kind === "filmstrip" ? 68 : 8.6;
}
export function learnRate(job: Job, duration: number) {
  if (job.status !== "done" || !job.elapsed || job.elapsed < 5) return;
  try { localStorage.setItem(`rq.rate.${job.kind}`, String(duration / job.elapsed)); } catch { /* ignore */ }
}
export const estimate = (kind: string, duration: number) => clockText(duration / rateFor(kind));

export function JobsPanel({ state, onFilmstrip, onProxy, onCancel, onAudioTracks, onAudioPreview }: {
  state: AppState; onFilmstrip: (force: boolean) => void; onProxy: (height: number) => void; onCancel: (id: string) => void;
  onAudioTracks: (tracks: number[]) => void; onAudioPreview: () => void;
}) {
  const latest = (kind: string) => [...state.jobs].reverse().find((j) => j.kind === kind);
  const ws = state.workspace!;
  const duration = state.project!.info.duration;
  const [height, setHeight] = useState(1080);
  const confirmThen = (kind: string, rebuild: boolean, extra: string, go: () => void) => {
    if (!rebuild) { go(); return; }
    if (confirm(`Rebuild the ${kind}? About ${estimate(kind, duration)} for this recording.\n${extra}`)) go();
  };
  const tracks = state.project!.settings.audio_tracks.join(",");
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
      <Row name="Filmstrip" job={fs} ready={ws.has_filmstrip} readyText={`ready · rebuild ≈ ${estimate("filmstrip", duration)}`}
        actions={<button className="btn small" title={`About ${estimate("filmstrip", duration)}`} onClick={() => confirmThen("filmstrip", ws.has_filmstrip, "Thumbnails are replaced as it goes; stopping keeps what is done.", () => onFilmstrip(ws.has_filmstrip))}>{ws.has_filmstrip ? "Rebuild" : `Build ≈ ${estimate("filmstrip", duration)}`}</button>} />
      <Row name="Proxy" job={px} ready={ws.has_proxy} readyText={`ready · rebuild ≈ ${estimate("proxy", duration)}`} actions={<>
        <select className="text" style={{ padding: "2px 4px", fontSize: 10 }} value={height} onChange={(e) => setHeight(Number(e.target.value))}><option value={1080}>1080p</option><option value={720}>720p</option></select>
        <button className="btn small" title={`About ${estimate("proxy", duration)}`} onClick={() => confirmThen("proxy", ws.has_proxy, "The current proxy stays in place until the new one has finished.", () => onProxy(height))}>{ws.has_proxy ? "Rebuild" : `Build ≈ ${estimate("proxy", duration)}`}</button></>} />
      <div className="jobrow">
        <span className="name">Audio</span>
        <div className="status" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span className="dim">tracks</span>
          <input className="text num" style={{ width: 64, padding: "2px 5px", fontSize: 11 }} defaultValue={tracks} key={tracks} title="Source tracks for the proxy and every clip, 1-based, e.g. 7,8"
            onBlur={(e) => { const t = e.target.value.split(/[ ,]+/).map(Number).filter((n) => n > 0); if (t.length && t.join(",") !== tracks) onAudioTracks(t); }} />
          <span className="dim">of {state.project!.info.audio.length}</span>
        </div>
        <button className="btn small" onClick={onAudioPreview} title="Hear a few seconds of every track at the playhead">Listen</button>
      </div>
    </div>
  );
}

const ZONES: [string, number][] = [["ET", -10800], ["CT", -7200], ["MT", -3600], ["PT", 0]];

export function MarkersPanel({ markers, info, onSeek, onImport, onDelete, onDeleteUntimed, offset, onOffset }: {
  markers: Marker[]; info: MediaInfo; onSeek: (f: number) => void; onImport: () => void; onDelete: (frame: number) => void;
  onDeleteUntimed: (index: number) => void; offset: number; onOffset: (s: number) => void;
}) {
  const timed = markers.filter((m) => m.frame !== null);
  const untimed = markers.filter((m) => m.frame === null);
  const zone = ZONES.find(([, o]) => o === offset)?.[0] ?? "custom";
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Markers <span className="dim" style={{ letterSpacing: 0 }}>{timed.length}</span></h2>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span className="dim" style={{ fontSize: 10 }} title="The clock the log was kept in. The recorder's timecode is Pacific.">log clock</span>
          <select className="text" style={{ padding: "2px 4px", fontSize: 11 }} value={zone} onChange={(e) => { const z = ZONES.find(([k]) => k === e.target.value); if (z) onOffset(z[1]); }}>
            {ZONES.map(([k]) => <option key={k} value={k}>{k}</option>)}
            {zone === "custom" && <option value="custom">{offset / 3600}h</option>}
          </select>
          <button className="btn small" onClick={onImport}>Import log</button>
        </div>
      </div>
      <div className="markers">
        {markers.length === 0 && <div className="dim" style={{ fontSize: 11 }}>Import the tech's log (CSV or a paste from Sheets), or press <span className="kbd">M</span> to drop a marker at the playhead.</div>}
        {timed.map((m) => (
          <div key={`${m.frame}-${m.source}`} className="markerrow" onClick={() => onSeek(m.frame!)}>
            <span className="tc">{m.tc}</span>
            <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {m.shell && <span style={{ marginRight: 6 }}>{m.shell}</span>}{m.engine && <span className="dim" style={{ marginRight: 6 }}>{m.engine}</span>}{m.camera && <span className="accent" style={{ marginRight: 6 }}>{m.camera}</span>}<span className="muted">{m.description}</span>
            </span>
            <span className="del dim" onClick={(e) => { e.stopPropagation(); onDelete(m.frame!); }}>✕</span>
          </div>
        ))}
        {untimed.length > 0 && (
          <>
            <div className="dim caps" style={{ fontSize: 9, letterSpacing: "0.16em", margin: "8px 0 2px" }}>Logged without a time, still to find</div>
            {untimed.map((m, i) => (
              <div key={`u${i}`} className="markerrow" style={{ cursor: "default" }}>
                <span className="dim">{m.section || "—"}</span>
                <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {m.shell && <span style={{ marginRight: 6 }}>{m.shell}</span>}{m.camera && <span className="accent" style={{ marginRight: 6 }}>{m.camera}</span>}<span className="muted">{m.description}</span>
                </span>
                <span className="del dim" onClick={() => onDeleteUntimed(i)} title="Found it, remove from the list">✕</span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export function RenderPanel({ job, clipCount, missingStills, selectedId, outDir, onRender, onRenderSelected, onCancel, onPickOut, onReveal }: {
  job?: Job; clipCount: number; missingStills: number; selectedId: string | null; outDir: string;
  onRender: () => void; onRenderSelected: () => void; onCancel: () => void; onPickOut: () => void; onReveal: () => void;
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
                  <div className="n"><span>{r.name}</span><span className="num">{r.status === "done" ? "✓" : r.status === "failed" ? "✕" : r.status === "running" ? `${Math.round(r.progress * 100)}%` : "·"}</span></div>
                  <div className={`bar ${r.status === "done" ? "done" : r.status === "failed" ? "failed" : ""}`}><i style={{ width: `${(r.status === "done" ? 1 : r.progress) * 100}%` }} /></div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {missingStills > 0 && !running && <div className="dim" style={{ fontSize: 11, marginTop: 8 }}><span className="accent">{missingStills}</span> clip{missingStills > 1 ? "s have" : " has"} no still chosen yet; the middle frame will be used.</div>}
      <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
        {running ? <button className="btn danger" onClick={onCancel}>Cancel</button> : (
          <>
            <button className="btn primary" disabled={!clipCount} onClick={onRender}>Render all {clipCount ? `(${clipCount})` : ""}</button>
            <button className="btn" disabled={!selectedId} onClick={onRenderSelected}>Selected</button>
          </>
        )}
        <span className="spacer" style={{ flex: 1 }} />
        <button className="btn small" onClick={onReveal} title={outDir}>Open folder</button>
        <button className="btn small" onClick={onPickOut} title="Choose a different output folder">Folder…</button>
      </div>
      <div className="dim" style={{ fontSize: 10, marginTop: 6, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", direction: "rtl", textAlign: "left" }} title={outDir}>{outDir}</div>
    </div>
  );
}


export function ListsDialog({ vocab, onSave, onClose }: { vocab: Vocab; onSave: (v: Vocab) => void; onClose: () => void }) {
  const [draft, setDraft] = useState<Record<keyof Vocab, string>>({
    shells: vocab.shells.join("\n"), cameras: vocab.cameras.join("\n"), engines: vocab.engines.join("\n"), segments: vocab.segments.join("\n"),
  });
  const parse = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ width: 820, gridTemplateRows: "auto 1fr auto" }} onClick={(e) => e.stopPropagation()}>
        <header><h2>Lists</h2><span className="dim" style={{ fontSize: 11 }}>One per line. These fill the fields here and should match the sheet's dropdowns.</span></header>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: 10, padding: 14, minHeight: 0 }}>
          {(["shells", "cameras", "engines", "segments"] as (keyof Vocab)[]).map((k) => (
            <div key={k} className="field" style={{ minHeight: 0 }}>
              <label>{k}</label>
              <textarea className="text" style={{ height: 360, resize: "none", fontFamily: "var(--font)", background: "var(--off-black)", border: "1px solid var(--line)", padding: 6 }}
                value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} spellCheck={false} />
            </div>
          ))}
        </div>
        <footer>
          <button className="btn" onClick={() => navigator.clipboard?.writeText(draft.shells).catch(() => {})} title="Copy the shell list to paste into the sheet's data validation">Copy shells</button>
          <button className="btn primary" onClick={() => onSave({ shells: parse(draft.shells), cameras: parse(draft.cameras), engines: parse(draft.engines), segments: parse(draft.segments) })}>Save</button>
          <button className="btn" onClick={onClose}>Cancel</button>
        </footer>
      </div>
    </div>
  );
}


export function AudioDialog({ tracks, tcLabel, onClose }: { tracks: { track: number; url: string; channels: number }[] | null; tcLabel: string; onClose: () => void }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ width: 520, gridTemplateRows: "auto 1fr auto" }} onClick={(e) => e.stopPropagation()}>
        <header><h2>Audio tracks at {tcLabel}</h2></header>
        {tracks ? (
          <div className="audiolist">
            {tracks.map((t) => (
              <div key={t.track} className="t"><span className="num">track {t.track} <span className="dim">{t.channels}ch</span></span><audio controls preload="none" src={t.url} /></div>
            ))}
          </div>
        ) : <div className="dim" style={{ padding: 14 }}>Extracting a few seconds of each track…</div>}
        <footer><span className="dim" style={{ flex: 1, fontSize: 11 }}>Type the dialog pair into the Audio field, e.g. 7,8. The proxy needs a rebuild to pick it up; clips use it straight away.</span><button className="btn" onClick={onClose}>Close</button></footer>
      </div>
    </div>
  );
}
