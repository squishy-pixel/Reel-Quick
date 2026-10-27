import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, native, type AppState, type Clip, type FilmstripIndex } from "./api";
import { Filmstrip, THUMB_MAX, THUMB_MIN } from "./components/Filmstrip";
import { Tc } from "./components/Tc";
import { Player, type PlayerHandle } from "./components/Player";
import { ClipEditor, ClipList } from "./components/ClipList";
import { AudioDialog, JobsPanel, ListsDialog, MarkersPanel, RenderPanel, learnRate } from "./components/Panels";
import { FileDialog } from "./components/FileDialog";

type Dialog = null | { kind: "open" } | { kind: "outdir" } | { kind: "help" } | { kind: "still" } | { kind: "lists" } | { kind: "audio"; tracks: { track: number; url: string; channels: number }[] | null };

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [strip, setStrip] = useState<FilmstripIndex | null>(null);
  const [frame, setFrame] = useState(0);
  const [inPoint, setIn] = useState<number | null>(null);
  const [outPoint, setOut] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [toast, setToast] = useState<{ text: string; error?: boolean } | null>(null);
  const [exactView, setExact] = useState(false);
  const [follow, setFollow] = useState(true);
  const [pendingStill, setPendingStill] = useState<number | null>(null);
  const [thumbW, setThumbWState] = useState(() => { try { return Math.max(THUMB_MIN, Math.min(THUMB_MAX, Number(localStorage.getItem("rq.thumbW")) || 160)); } catch { return 160; } });
  const setThumbW = (w: number) => { setThumbWState(w); try { localStorage.setItem("rq.thumbW", String(w)); } catch { /* ignore */ } };
  const player = useRef<PlayerHandle>(null);
  const csvInput = useRef<HTMLInputElement>(null);
  const stillName = useRef<HTMLInputElement>(null);

  const say = useCallback((text: string, error = false) => { setToast({ text, error }); setTimeout(() => setToast(null), error ? 6000 : 2500); }, []);
  const refresh = useCallback(async () => { try { setState(await api.state()); } catch (e: any) { say(e.message, true); } }, [say]);
  const run = useCallback(async <T,>(p: Promise<T>, after?: (r: T) => void): Promise<T | undefined> => {
    try { const r = await p; after?.(r); await refresh(); return r; } catch (e: any) { say(e.message, true); return undefined; }
  }, [refresh, say]);

  // Poll: fast while anything runs, slow otherwise.
  useEffect(() => { refresh(); }, [refresh]);
  const learned = useRef(new Set<string>());
  useEffect(() => {
    if (!state?.project) return;
    for (const j of state.jobs) if (j.status === "done" && !learned.current.has(j.id) && (j.kind === "filmstrip" || j.kind === "proxy")) { learned.current.add(j.id); learnRate(j, state.project.info.duration); }
  }, [state]);
  useEffect(() => {
    const busy = state?.jobs.some((j) => j.status === "running");
    const t = setInterval(refresh, busy ? 700 : 5000);
    return () => clearInterval(t);
  }, [state?.jobs, refresh]);

  // Load the filmstrip index when it appears or is rebuilt.
  const stripKey = `${state?.workspace?.source}|${state?.workspace?.has_filmstrip}|${state?.jobs.filter((j) => j.kind === "filmstrip" && j.status === "done").length}`;
  useEffect(() => {
    if (!state?.workspace?.has_filmstrip) { setStrip(null); return; }
    api.filmstrip().then(setStrip).catch(() => setStrip(null));
  }, [stripKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const project = state?.project ?? null;
  const info = project?.info ?? null;
  const fps = info?.fps ?? 60000 / 1001;
  const seek = useCallback((f: number) => { player.current?.seek(f); }, []);
  const selectedClip = useMemo(() => project?.clips.find((c) => c.id === selected) ?? null, [project, selected]);
  const renderJob = useMemo(() => [...(state?.jobs ?? [])].reverse().find((j) => j.kind === "render"), [state?.jobs]);
  const renderRows = useMemo(() => {
    const rows: Record<string, { status: string; progress: number }> = {};
    if (renderJob?.status === "running") for (const r of renderJob.detail?.clips ?? []) rows[r.id] = r;
    return rows;
  }, [renderJob]);

  // ---- actions ---------------------------------------------------------
  const openFile = async () => {
    const n = native();
    if (n) { const p = await n.pick_file(); if (p) run(api.open(p), () => { setIn(null); setOut(null); setSelected(null); setFrame(0); }); }
    else setDialog({ kind: "open" });
  };
  const addClip = useCallback(() => {
    if (!project) return;
    const a = inPoint ?? frame, b = outPoint ?? frame;
    const lo = Math.min(a, b), hi = Math.max(a, b);
    if (hi <= lo) { say("Set an in and an out point first (I and O)", true); return; }
    const still = pendingStill !== null && pendingStill >= lo && pendingStill < hi ? pendingStill : null;
    run(api.addClip({ in_frame: lo, out_frame: hi }), async (r) => {
      setSelected(r.clip.id); setIn(null); setOut(null);
      if (still !== null) { setPendingStill(null); await api.patchClip(r.clip.id, { still_frame: still }).catch(() => {}); await refresh(); }
      say(`Added ${r.clip.name}${still !== null ? " with its still" : ""}`);
    });
  }, [project, inPoint, outPoint, frame, pendingStill, run, say, refresh]);
  const patchClip = useCallback((id: string, patch: Partial<Clip>) => run(api.patchClip(id, patch)), [run]);
  const deleteClip = useCallback((id: string) => { if (confirm("Delete this clip from the list?")) run(api.deleteClip(id), () => { if (selected === id) setSelected(null); }); }, [run, selected]);
  const setStill = useCallback(() => {
    // Inside a clip: first press sets its still, later presses add extra stills. Outside any clip: remember the
    // frame and attach it when a clip is added around it.
    // A selected clip owns S anywhere inside its handles; otherwise whichever clip contains the frame.
    const inside = (c: Clip, pad = 0) => frame >= c.in_frame - pad * fps && frame < c.out_frame + pad * fps;
    const owner = selectedClip && inside(selectedClip, Math.max(selectedClip.head_seconds, selectedClip.tail_seconds)) ? selectedClip
      : project?.clips.find((c) => inside(c)) ?? null;
    if (owner) {
      if (owner.still_frame === null) run(api.patchClip(owner.id, { still_frame: frame }), () => { setSelected(owner.id); say("Still set for this clip"); });
      else if (owner.still_frame === frame || owner.extra_stills?.includes(frame)) say("That frame is already a still for this clip");
      else run(api.patchClip(owner.id, { extra_stills: [...(owner.extra_stills || []), frame] }), () => { setSelected(owner.id); say(`Extra still added (${(owner.extra_stills?.length || 0) + 2} for this clip)`); });
      return;
    }
    setPendingStill(frame);
    say("Still marked. Add a clip around it with I, O and Enter, or press Shift+S to save it as a named still.");
  }, [selectedClip, project, frame, fps, run, say]);
  const importCsv = async () => {
    const n = native();
    if (n) { const text = await n.pick_csv(); if (text) run(api.importMarkers(text), reportImport); }
    else csvInput.current?.click();
  };
  const onCsvFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; if (!f) return;
    f.text().then((text) => run(api.importMarkers(text), reportImport));
    e.target.value = "";
  };
  const reportImport = (r: { added: number; untimed: number; skipped: string[]; all_outside: boolean }) => {
    if (r.all_outside) { say("Every row fell outside the recording. Check the log clock (ET / CT / MT / PT) and import again.", true); return; }
    say(`${r.added} markers added${r.untimed ? `, ${r.untimed} without a time` : ""}${r.skipped.length ? `, ${r.skipped.length} rows skipped` : ""}`);
  };
  const nextMarker = (dir: 1 | -1) => {
    if (!project) return;
    const ms = project.markers.filter((m) => m.frame !== null).map((m) => m.frame as number);
    const target = dir > 0 ? ms.find((f) => f > frame + 1) : [...ms].reverse().find((f) => f < frame - 1);
    if (target !== undefined) seek(target); else say(dir > 0 ? "No marker after this point" : "No marker before this point");
  };

  // ---- keyboard --------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { if (e.key === "Escape") (e.target as HTMLElement).blur(); return; }
      if (!project) return;
      const p = player.current;
      const step = (n: number) => { p?.pause(); seek(frame + n); };
      const big = e.shiftKey ? Math.round(fps) : e.ctrlKey || e.metaKey ? Math.round(fps * 10) : 1;
      switch (e.key) {
        case " ": e.preventDefault(); p?.toggle(); break;
        case "k": case "K": p?.pause(); break;
        case "l": case "L": p?.shuttle(1); break;
        case "j": case "J": p?.shuttle(-1); break;
        case "ArrowLeft": e.preventDefault(); step(-big); break;
        case "ArrowRight": e.preventDefault(); step(big); break;
        case "Home": seek(0); break;
        case "End": seek(project.info.nb_frames - 1); break;
        case "i": case "I": setIn(frame); if (outPoint !== null && outPoint <= frame) setOut(null); break;
        case "o": case "O": setOut(frame); if (inPoint !== null && inPoint >= frame) setIn(null); break;
        case "Enter": addClip(); break;
        case "Escape": setIn(null); setOut(null); setSelected(null); setPendingStill(null); break;
        case "[": nextMarker(-1); break;
        case "]": nextMarker(1); break;
        case "m": case "M": run(api.addMarker(frame, ""), () => say("Marker added")); break;
        case "s": if (e.shiftKey) setDialog({ kind: "still" }); else setStill(); break;
        case "S": setDialog({ kind: "still" }); break;
        case "f": case "F": setExact((x) => !x); break;
        case "Delete": case "Backspace": if (selectedClip) deleteClip(selectedClip.id); break;
        case "?": setDialog({ kind: "help" }); break;
        default: return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [project, frame, fps, inPoint, outPoint, addClip, setStill, selectedClip, deleteClip, seek, run, say]);

  const onFrame = useCallback((f: number) => { setFrame(f); }, []);

  // ---- render ----------------------------------------------------------
  if (!state) return <div className="welcome"><div className="dim">Starting…</div></div>;
  const ws = state.workspace;
  return (
    <div className="app">
      <div className="topbar">
        <div className="brand"><span className="chev">&gt;</span>Reel Quick<span className="tag">Girraphic</span></div>
        {project ? (
          <>
            <div className="file"><b>{ws!.source.split(/[\\/]/).pop()}</b> · {info!.width}x{info!.height} {info!.fps.toFixed(2)}p {info!.is_hlg ? "HLG" : info!.color_transfer} · {info!.start_timecode} · {(info!.duration / 3600).toFixed(2)} h</div>
            <GameFields key={ws!.source} game={project.game} onChange={(g) => run(api.game(g))} />
          </>
        ) : <div className="file dim">No file open</div>}
        <button className="btn" onClick={openFile} title="Open a different recording">Open recording…</button>
        <button className="btn small" onClick={() => setDialog({ kind: "lists" })} title="Edit the shell, camera, engine and segment lists">Lists</button>
        <button className="btn small" onClick={() => setDialog({ kind: "help" })} title="Keyboard shortcuts">?</button>
      </div>

      <div className="strip-area">
        {project && strip ? (
          <Filmstrip index={strip} nbFrames={info!.nb_frames} frame={frame} markers={project.markers} clips={project.clips}
            inPoint={inPoint} outPoint={outPoint} pendingStill={pendingStill} onSeek={seek} follow={follow} thumbW={thumbW} onThumbW={setThumbW} tcOf={tcOf} />
        ) : (
          <div className="strip-empty hatch">
            {project ? <><span>No filmstrip yet.</span><button className="btn primary" onClick={() => run(api.startFilmstrip({}))}>Build filmstrip</button><span className="dim">About five minutes for a full game.</span></> : <span className="dim">Open a recording to begin.</span>}
          </div>
        )}
      </div>

      <div className="main">
        {project ? (
          <>
            <Player ref={player} info={info!} proxyUrl={ws!.proxy_url} frame={frame} onFrame={onFrame} exactView={exactView} />
            <div className="transport">
              <button className="btn small" onClick={() => player.current?.shuttle(-1)} title="J">◀◀</button>
              <button className="btn small" onClick={() => player.current?.toggle()} title="Space">▶ / ❚❚</button>
              <button className="btn small" onClick={() => player.current?.shuttle(1)} title="L">▶▶</button>
              <span style={{ width: 10 }} />
              <button className="btn small" onClick={() => nextMarker(-1)} title="[">◀ marker</button>
              <button className="btn small" onClick={() => nextMarker(1)} title="]">marker ▶</button>
              <span style={{ width: 10 }} />
              <div className={`mark ${inPoint !== null ? "set" : ""}`} onClick={() => setIn(frame)}><span className="k">In</span>{inPoint !== null ? <Tc value={tcOf(inPoint)} /> : <span className="tc">—</span>}</div>
              <div className={`mark ${outPoint !== null ? "set" : ""}`} onClick={() => setOut(frame)}><span className="k">Out</span>{outPoint !== null ? <Tc value={tcOf(outPoint)} /> : <span className="tc">—</span>}</div>
              <button className="btn primary small" onClick={addClip} disabled={inPoint === null && outPoint === null}>Add clip ⏎</button>
              <button className="btn small" onClick={setStill} title="S">Still</button>
              <span className="spacer" />
              <label className="dim" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 11 }}><input type="checkbox" checked={exactView} onChange={(e) => setExact(e.target.checked)} /> show source frame when paused (F)</label>
              <label className="dim" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 11 }}><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
            </div>
          </>
        ) : (
          <div className="welcome hatch">
            <h1><span>&gt;</span> Reel Quick</h1>
            <div>Open a game recording to scan it, mark your segments, and render them.</div>
            <button className="btn primary" onClick={openFile}>Open recording…</button>
            <div className="dim" style={{ fontSize: 11 }}>Working files go in {state.work_root}</div>
          </div>
        )}
      </div>

      <div className="side">
        {project ? (
          <>
            <div>
              <JobsPanel state={state} onFilmstrip={(force) => run(api.startFilmstrip({ force }))} onProxy={(height) => run(api.startProxy({ height }))} onCancel={(id) => run(api.cancelJob(id))}
                onAudioTracks={(t) => run(api.settings({ audio_tracks: t }), () => say("Audio tracks saved. Rebuild the proxy to hear them there."))}
                onAudioPreview={() => { setDialog({ kind: "audio", tracks: null }); api.audioPreview(frame).then((r) => setDialog({ kind: "audio", tracks: r.tracks })).catch((e) => { setDialog(null); say(e.message, true); }); }} />
              <MarkersPanel markers={project.markers} info={info!} onSeek={seek} onImport={importCsv} onDelete={(f) => run(api.deleteMarker(f))}
                onDeleteUntimed={(i) => run(api.deleteUntimed(i))}
                offset={project.settings.log_offset_seconds} onOffset={(s) => run(api.settings({ log_offset_seconds: s }), () => say("Log clock saved. Import the log again to apply it."))} />
              <div className="panel-head" style={{ padding: "10px 12px 0" }}><h2>Clips <span className="dim" style={{ letterSpacing: 0 }}>{project.clips.length}</span></h2>
                {selectedClip && <button className="btn small" onClick={() => seek(selectedClip.in_frame)}>Go to in</button>}</div>
            </div>
            <ClipList clips={project.clips} info={info!} selected={selected} onSelect={setSelected} onPatch={patchClip} onDelete={deleteClip} onSeek={seek} renderRows={renderRows} />
            <div>
              {selectedClip && <ClipEditor clip={selectedClip} info={info!} frame={frame} vocab={state.vocab} onPatch={(p) => patchClip(selectedClip.id, p)} onSeek={seek} onSetStill={setStill} />}
              <RenderPanel job={renderJob} clipCount={project.clips.length} missingStills={project.clips.filter((c) => c.still_frame === null).length} selectedId={selected} outDir={ws!.clips_dir}
                onRender={() => { const n = project.clips.filter((c) => c.still_frame === null).length; if (n && !confirm(`${n} clip${n > 1 ? "s have" : " has"} no still chosen; the middle frame will be used. Render anyway?`)) return; run(api.startRender({})); }}
                onRenderSelected={() => selected && run(api.startRender({ clip_ids: [selected] }))}
                onCancel={() => renderJob && run(api.cancelJob(renderJob.id))}
                onReveal={() => run(api.reveal(ws!.clips_dir))}
                onPickOut={async () => { const n = native(); if (n) { const p = await n.pick_folder(ws!.clips_dir); if (p) run(api.settings({ output_dir: p })); } else setDialog({ kind: "outdir" }); }} />
            </div>
          </>
        ) : <div className="panel dim hatch" style={{ height: "100%" }} />}
      </div>

      <input ref={csvInput} type="file" accept=".csv,.tsv,.txt" style={{ display: "none" }} onChange={onCsvFile} />
      {dialog?.kind === "open" && <FileDialog title="Open a recording" onClose={() => setDialog(null)} onPick={(p) => { setDialog(null); run(api.open(p), () => { setIn(null); setOut(null); setSelected(null); setFrame(0); }); }} />}
      {dialog?.kind === "outdir" && <FileDialog title="Output folder for clips" dirsOnly start={ws?.clips_dir} onClose={() => setDialog(null)} onPick={(p) => { setDialog(null); run(api.settings({ output_dir: p })); }} />}
      {dialog?.kind === "still" && project && (
        <div className="modal-bg" onClick={() => setDialog(null)}>
          <div className="modal" style={{ width: 460, gridTemplateRows: "auto 1fr auto" }} onClick={(e) => e.stopPropagation()}>
            <header><h2>Save still at {tcOf(frame)}</h2></header>
            <div style={{ padding: 14 }}>
              <img src={`/api/frame/${frame}?width=640`} alt="" style={{ width: "100%", display: "block", marginBottom: 10 }} />
              <div className="field"><label>File name</label><input ref={stillName} className="text" autoFocus defaultValue={`${[project.game.date, [project.game.week, project.game.away && project.game.home ? `${project.game.away}v${project.game.home}` : ""].filter(Boolean).join("-")].filter(Boolean).join("_")}_`} onKeyDown={(e) => { if (e.key === "Enter") saveStill(); }} /></div>
            </div>
            <footer><button className="btn primary" onClick={saveStill}>Save PNG</button><button className="btn" onClick={() => setDialog(null)}>Cancel</button></footer>
          </div>
        </div>
      )}
      {dialog?.kind === "audio" && <AudioDialog tracks={dialog.tracks} tcLabel={tcOf(frame)} onClose={() => setDialog(null)} />}
      {dialog?.kind === "lists" && <ListsDialog vocab={state.vocab} onClose={() => setDialog(null)} onSave={(v) => { setDialog(null); run(api.vocab(v), () => say("Lists saved")); }} />}
      {dialog?.kind === "help" && (
        <div className="modal-bg" onClick={() => setDialog(null)}>
          <div className="modal" style={{ width: 520, gridTemplateRows: "auto 1fr" }} onClick={(e) => e.stopPropagation()}>
            <header><h2>Keyboard</h2></header>
            <div className="help">
              {([["Space", "play / pause"], ["J / K / L", "shuttle back / pause / shuttle forward (press again for faster)"], ["← →", "one frame; Shift = one second; Ctrl = ten seconds"], ["I / O", "set in / out at the playhead"], ["Enter", "add the in–out range as a clip"], ["Esc", "clear in / out and selection"], ["[ ]", "previous / next marker"], ["M", "drop a marker at the playhead"], ["S", "still: sets the clip's still, adds an extra one if it has one, or marks the frame for the next clip"], ["Shift+S", "save this frame as a named still"], ["F", "show the exact source frame while paused (works before the proxy exists)"], ["Ctrl+wheel", "zoom the thumbnails"], ["Delete", "remove the selected clip"]] as const).map(([k, v]) => <><span className="kbd">{k}</span><span>{v}</span></>)}
            </div>
          </div>
        </div>
      )}
      {toast && <div className={`toast ${toast.error ? "error" : ""}`}>{toast.text}</div>}
    </div>
  );

  function tcOf(f: number) {
    if (!info) return "";
    // the Player exports startFrames; keep this local to avoid a circular import in the bundle
    const n = Math.round(info.fps);
    const tc = info.start_timecode; let base = 0;
    const m = tc && /^(\d{2}):(\d{2}):(\d{2})[:;.](\d{2})$/.exec(tc);
    if (m) { const [hh, mm, ss, ff] = m.slice(1).map(Number); base = n * 3600 * hh + n * 60 * mm + n * ss + ff; if (info.drop_frame) { const d = 2 * Math.floor(n / 30); const mins = 60 * hh + mm; base -= d * (mins - Math.floor(mins / 10)); } }
    return framesToTcLocal(base + f, n, info.drop_frame);
  }
  function saveStill() {
    const name = stillName.current?.value.trim();
    if (!name) return;
    setDialog(null);
    run(api.addStill(frame, name), (r) => say(`Saved ${r.path.split(/[\\/]/).pop()}`));
  }
}

function framesToTcLocal(frames: number, n: number, drop: boolean): string {
  let f = Math.max(0, Math.floor(frames));
  if (drop) { const d = 2 * Math.floor(n / 30); const per10 = n * 600 - d * 9; const perMin = n * 60 - d; const tens = Math.floor(f / per10); const rem = f % per10; f += rem > d ? d * 9 * tens + d * Math.floor((rem - d) / perMin) : d * 9 * tens; }
  const p = (x: number) => String(x).padStart(2, "0");
  return `${p(Math.floor(f / (n * 3600)))}:${p(Math.floor(f / (n * 60)) % 60)}:${p(Math.floor(f / n) % 60)}${drop ? ";" : ":"}${p(f % n)}`;
}

function GameFields({ game, onChange }: { game: { date: string; week: string; away: string; home: string }; onChange: (g: { date: string; week: string; away: string; home: string }) => void }) {
  const [g, setG] = useState(game);
  const commit = () => { if (JSON.stringify(g) !== JSON.stringify(game)) onChange(g); };
  const f = (k: keyof typeof g, w: number, ph: string) => (
    <input className="text num" style={{ width: w, padding: "3px 6px", textTransform: k === "away" || k === "home" ? "uppercase" : undefined }} value={g[k]} placeholder={ph}
      onChange={(e) => setG({ ...g, [k]: e.target.value })} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
  );
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      {f("date", 72, "YYMMDD")}{f("week", 58, "WK08")}{f("away", 46, "AWY")}<span className="dim">v</span>{f("home", 46, "HOM")}
    </div>
  );
}
