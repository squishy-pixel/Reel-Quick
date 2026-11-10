import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, native, type AppState, type Clip, type FilmstripIndex, type Recent } from "./api";
import { Filmstrip, THUMB_MAX, THUMB_MIN } from "./components/Filmstrip";
import { Tc } from "./components/Tc";
import { Player, type PlayerHandle } from "./components/Player";
import { Shuttle } from "./components/Shuttle";
import { ClipEditor, ClipList, type QuickExport } from "./components/ClipList";
import { AudioDialog, BroadcastPanel, JobsPanel, MarkersPanel, RenderPanel, learnRate } from "./components/Panels";
import { PHASE_LABEL, boundary, clipPhase, phaseOf, phaseSpans } from "./phases";
import type { BoundaryKey, SpaceEstimate } from "./api";
import { FileDialog } from "./components/FileDialog";
import { SettingsDialog } from "./components/Settings";
import { ACTIONS, type ActionId, actionFor, bindingsFrom, comboOf, overridesOf, showKey, tip } from "./shortcuts";
import { getUi, loadUiPrefs, setUi } from "./uiPrefs";

type Dialog = null | { kind: "open" } | { kind: "outdir" } | { kind: "outroot" } | { kind: "help" } | { kind: "still" } | { kind: "settings"; tab?: "general" | "shortcuts" | "lists" } | { kind: "audio"; tracks: { track: number; url: string; channels: number }[] | null };

// One undo order for everything: the in/out marks live here in the window, project edits on the server.
// A "server" entry stands for one remembered project edit; the server's own stack holds the snapshot.
type UndoEntry = { kind: "marks"; in: number | null; out: number | null; label: string } | { kind: "server" };

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
  const [recent, setRecent] = useState<Recent[]>([]);
  const [pstatus, setPstatus] = useState({ rate: 0, mode: "no video" });
  const [reveal, setReveal] = useState<{ frame: number; n: number } | null>(null);
  // Fonts dropped in <work root>/fonts: Playoff takes over the numerals when present, as the brand guide asks.
  useEffect(() => {
    api.fonts().then((fonts) => {
      if (!fonts.length) return;
      const css = fonts.map((f) => `@font-face{font-family:"${f.playoff ? "Playoff" : f.family}";font-weight:${f.weight};font-style:${f.italic ? "italic" : "normal"};src:url("${f.url}");font-display:swap;}`).join("\n");
      const el = document.createElement("style"); el.textContent = css; document.head.appendChild(el);
      if (fonts.some((f) => f.playoff)) document.documentElement.style.setProperty("--mono", '"Playoff", "Saira Semi Condensed", "Saira", monospace');
    }).catch(() => {});
  }, []);
  useEffect(() => { if (state && !state.project) api.recent().then(setRecent).catch(() => setRecent([])); }, [state?.project === null]); // eslint-disable-line react-hooks/exhaustive-deps
  const clampThumb = (w: unknown) => Math.max(THUMB_MIN, Math.min(THUMB_MAX, Number(w) || 160));
  const [thumbW, setThumbWState] = useState(() => clampThumb(getUi("thumbW", 160)));
  const setThumbW = (w: number) => { setThumbWState(w); setUi("thumbW", w); };
  // The saved UI settings arrive with the first state; the thumbnail size follows them once.
  const uiLoaded = useRef(false);
  useEffect(() => {
    if (!state?.prefs) return;
    loadUiPrefs(state.prefs.ui);
    if (!uiLoaded.current) { uiLoaded.current = true; setThumbWState(clampThumb(getUi("thumbW", 160))); }
  }, [state?.prefs]); // eslint-disable-line react-hooks/exhaustive-deps
  const player = useRef<PlayerHandle>(null);
  const undoStack = useRef<UndoEntry[]>([]);
  const redoStack = useRef<UndoEntry[]>([]);
  const seqSeen = useRef<number | null>(null);
  const marks = useRef<{ in: number | null; out: number | null }>({ in: null, out: null });
  const [, setUndoTick] = useState(0);                 // re-render the undo buttons when the window's stack changes
  const csvInput = useRef<HTMLInputElement>(null);
  const jsonInput = useRef<HTMLInputElement>(null);
  const [space, setSpace] = useState<SpaceEstimate | null>(null);
  const stillName = useRef<HTMLInputElement>(null);

  const say = useCallback((text: string, error = false) => { setToast({ text, error }); setTimeout(() => setToast(null), error ? 6000 : 2500); }, []);
  const refresh = useCallback(async () => { try { setState(await api.state()); } catch (e: any) { say(e.message, true); } }, [say]);
  const run = useCallback(async <T,>(p: Promise<T>, after?: (r: T) => void): Promise<T | undefined> => {
    try { const r = await p; after?.(r); await refresh(); return r; } catch (e: any) { say(e.message, true); return undefined; }
  }, [refresh, say]);

  // A new project edit (the server's edit counter moved) joins the undo order; undo and redo leave it alone.
  useEffect(() => {
    const seq = state?.history?.seq;
    if (seq === undefined || seq === null) return;
    if (seqSeen.current !== null && seq > seqSeen.current) {
      for (let i = seqSeen.current; i < seq; i++) undoStack.current.push({ kind: "server" });
      redoStack.current = [];
      setUndoTick((t) => t + 1);
    }
    seqSeen.current = seq;
  }, [state?.history?.seq]);
  // Every change of the in/out marks goes through here so it can be undone like any other edit.
  const setMarks = useCallback((nextIn: number | null, nextOut: number | null, label: string) => {
    const cur = marks.current;
    if (cur.in === nextIn && cur.out === nextOut) return;
    undoStack.current.push({ kind: "marks", in: cur.in, out: cur.out, label });
    redoStack.current = [];
    marks.current = { in: nextIn, out: nextOut };
    setIn(nextIn); setOut(nextOut); setUndoTick((t) => t + 1);
  }, []);
  const resetMarks = useCallback(() => {
    marks.current = { in: null, out: null }; setIn(null); setOut(null);
    undoStack.current = []; redoStack.current = []; seqSeen.current = null; setUndoTick((t) => t + 1);
  }, []);

  // What a full render would take, against the destination's free space. Rechecked when the clip list,
  // settings or output folder change, and every so often since the drive's free space moves on its own.
  const spaceKey = JSON.stringify([state?.project?.clips.map((c) => [c.in_frame, c.out_frame, c.formats, c.head_seconds, c.tail_seconds, c.extra_stills.length]), state?.project?.settings, state?.workspace?.clips_dir]);
  useEffect(() => {
    if (!state?.project?.clips.length) { setSpace(null); return; }
    let live = true;
    const check = () => api.estimate({}).then((r) => live && setSpace(r)).catch(() => live && setSpace(null));
    const t = setTimeout(check, 400), every = setInterval(check, 60000);
    return () => { live = false; clearTimeout(t); clearInterval(every); };
  }, [spaceKey]); // eslint-disable-line react-hooks/exhaustive-deps

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
  // A jump chosen from a list (clip, marker, still) also brings the strip there, whatever "follow" says.
  const jumpTo = useCallback((f: number) => { player.current?.seek(f); setReveal((r) => ({ frame: f, n: (r?.n ?? 0) + 1 })); }, []);
  const selectedClip = useMemo(() => project?.clips.find((c) => c.id === selected) ?? null, [project, selected]);
  const renderJob = useMemo(() => [...(state?.jobs ?? [])].reverse().find((j) => j.kind === "render"), [state?.jobs]);
  const renderRows = useMemo(() => {
    const rows: Record<string, { status: string; progress: number }> = {};
    if (renderJob?.status === "running") for (const r of renderJob.detail?.clips ?? []) rows[r.id] = r;
    return rows;
  }, [renderJob]);

  // ---- actions ---------------------------------------------------------
  const openPath = (p: string) => run(api.open(p), () => { resetMarks(); setSelected(null); setFrame(0); setPendingStill(null); });
  const openFile = async () => {
    const n = native();
    if (n) { const p = await n.pick_file(); if (p) openPath(p); }
    else setDialog({ kind: "open" });
  };
  const startRender = (clipIds?: string[], once?: QuickExport) => {
    const offline = ws && !ws.source_online;
    if (offline) {
      if (!ws!.has_proxy) { say("The original recording is not available and there is no proxy to cut from.", true); return; }
      if (!confirm("The original recording is not available.\nRender from the proxy instead? Outputs will be SDR at proxy quality, named with _fromproxy, so they are easy to redo from the original later.")) return;
    }
    const pool = clipIds ? project!.clips.filter((c) => clipIds.includes(c.id)) : project!.clips;
    const n = once && !once.stills ? 0 : pool.filter((c) => c.still_frame === null).length;
    if (n && !confirm(`${n} clip${n > 1 ? "s have" : " has"} no still chosen; the middle frame will be used. Render anyway?`)) return;
    const req = { clip_ids: clipIds, from_proxy: !!offline, ...(once ?? {}) };
    api.startRender(req).then(refresh).catch((e: Error) => {
      // the destination looks too small: say so, and let the user go ahead anyway (the figure is an estimate)
      if (e.message.startsWith("not enough space") && confirm(`${e.message[0].toUpperCase()}${e.message.slice(1)}.\n\nRender anyway?`)) run(api.startRender({ ...req, force: true }));
      else if (!e.message.startsWith("not enough space")) say(e.message, true);
    });
  };
  const setBoundary = (key: BoundaryKey, f: number | null) => run(api.setPhases({ [key]: f }), () => say(f === null ? "Boundary cleared" : `Set at ${tcOf(f)}`));
  const importPhases = async () => {
    const n = native();
    if (n?.pick_json) { const text = await n.pick_json(); if (text) loadPhases(text); }
    else jsonInput.current?.click();
  };
  const loadPhases = (text: string) => {
    let data: unknown;
    try { data = JSON.parse(text); } catch { say("That file is not JSON", true); return; }
    run(api.importPhases(data), (r) => say(`${r.changed.length} boundar${r.changed.length === 1 ? "y" : "ies"} updated${r.kept.length ? `; kept your own for ${r.kept.join(", ").replace(/_/g, " ")}` : ""}`));
  };
  const addClip = useCallback(() => {
    if (!project) return;
    const a = inPoint ?? frame, b = outPoint ?? frame;
    const lo = Math.min(a, b), hi = Math.max(a, b);
    if (hi <= lo) { say("Set an in and an out point first (I and O)", true); return; }
    const still = pendingStill !== null && pendingStill >= lo && pendingStill < hi ? pendingStill : null;
    run(api.addClip({ in_frame: lo, out_frame: hi }), async (r) => {
      setSelected(r.clip.id); setMarks(null, null, "in and out of the new clip");
      if (still !== null) { setPendingStill(null); await api.patchClip(r.clip.id, { still_frame: still }).catch(() => {}); await refresh(); }
      say(`Added ${r.clip.name}${still !== null ? " with its still" : ""}`);
    });
  }, [project, inPoint, outPoint, frame, pendingStill, run, say, refresh, setMarks]);
  const patchClip = useCallback((id: string, patch: Partial<Clip>) => run(api.patchClip(id, patch)), [run]);
  const deleteClip = useCallback((id: string) => { if (confirm("Delete this clip from the list?")) run(api.deleteClip(id), () => { if (selected === id) setSelected(null); }); }, [run, selected]);
  const setStill = useCallback(() => {
    // Inside a clip: first press sets its still, later presses add extra stills. Outside any clip: remember the
    // frame and attach it when a clip is added around it.
    // A selected clip owns S anywhere inside its handles; otherwise whichever clip contains the frame,
    // content first, handles second. The server keeps stills within the same padded range.
    const inside = (c: Clip, pad = true) => frame >= c.in_frame - (pad ? c.head_seconds : 0) * fps && frame < c.out_frame + (pad ? c.tail_seconds : 0) * fps;
    const owner = selectedClip && inside(selectedClip) ? selectedClip
      : project?.clips.find((c) => inside(c, false)) ?? project?.clips.find((c) => inside(c)) ?? null;
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
    if (target !== undefined) jumpTo(target); else say(dir > 0 ? "No marker after this point" : "No marker before this point");
  };
  // Every edit can be taken back, and put back, in the order it was made: in/out marks, clips, stills,
  // markers, settings. Marks are restored here; a project edit is undone by the server.
  const undo = useCallback((redo = false) => {
    const from = redo ? redoStack.current : undoStack.current;
    const to = redo ? undoStack.current : redoStack.current;
    const serverHas = !!(redo ? state?.history?.redo : state?.history?.undo);
    const e = from.pop() ?? (serverHas ? { kind: "server" as const } : undefined);   // edits from before this window opened
    if (!e) { say(redo ? "Nothing to redo" : "Nothing to undo"); return; }
    if (e.kind === "marks") {
      const cur = marks.current;
      to.push({ kind: "marks", in: cur.in, out: cur.out, label: e.label });
      marks.current = { in: e.in, out: e.out }; setIn(e.in); setOut(e.out); setUndoTick((t) => t + 1);
      say(`${redo ? "Redid" : "Undid"}: ${e.label}`);
      return;
    }
    if (!serverHas) { setUndoTick((t) => t + 1); say(redo ? "Nothing to redo" : "Nothing to undo"); return; }
    run(redo ? api.redo() : api.undo(), (r) => { to.push({ kind: "server" }); setUndoTick((t) => t + 1); say(`${redo ? "Redid" : "Undid"}: ${r.label}`); });
  }, [state?.history, run, say]);
  const undoLabel = (redo = false): string | null => {
    const top = (redo ? redoStack.current : undoStack.current).at(-1);
    if (top?.kind === "marks") return top.label;
    return (redo ? state?.history?.redo : state?.history?.undo) ?? null;
  };

  // ---- keyboard --------------------------------------------------------
  const bindings = useMemo(() => bindingsFrom(state?.prefs?.shortcuts), [state?.prefs]);
  const markIn = useCallback(() => setMarks(frame, outPoint !== null && outPoint <= frame ? null : outPoint, "mark in"), [frame, outPoint, setMarks]);
  const markOut = useCallback(() => setMarks(inPoint !== null && inPoint >= frame ? null : inPoint, frame, "mark out"), [frame, inPoint, setMarks]);
  const clearMarks = useCallback(() => setMarks(null, null, "clear in and out"), [setMarks]);
  const doAction = useCallback((id: ActionId) => {
    if (!project) { if (id === "help") setDialog({ kind: "help" }); return; }
    const p = player.current;
    const step = (n: number) => { p?.pause(); seek(frame + n); };
    switch (id) {
      case "play": p?.toggle(); break;
      case "pause": p?.pause(); break;
      case "shuttle_fwd": p?.shuttle(1); break;
      case "shuttle_fwd_slow": p?.shuttle(1, true); break;
      case "shuttle_back": p?.shuttle(-1); break;
      case "shuttle_back_slow": p?.shuttle(-1, true); break;
      case "step_back": step(-1); break;
      case "step_fwd": step(1); break;
      case "back_1s": step(-Math.round(fps)); break;
      case "fwd_1s": step(Math.round(fps)); break;
      case "back_10s": step(-Math.round(fps * 10)); break;
      case "fwd_10s": step(Math.round(fps * 10)); break;
      case "go_start": seek(0); break;
      case "go_end": seek(project.info.nb_frames - 1); break;
      case "go_in": if (inPoint !== null) jumpTo(inPoint); else say("No in point set"); break;
      case "go_out": if (outPoint !== null) jumpTo(outPoint); else say("No out point set"); break;
      case "mark_in": markIn(); break;
      case "mark_out": markOut(); break;
      case "clear_in": setMarks(null, outPoint, "clear in"); break;
      case "clear_out": setMarks(inPoint, null, "clear out"); break;
      case "clear_marks": clearMarks(); break;
      case "add_clip": addClip(); break;
      case "escape": setMarks(null, null, "clear in and out"); setSelected(null); setPendingStill(null); break;
      case "prev_marker": nextMarker(-1); break;
      case "next_marker": nextMarker(1); break;
      case "add_marker": run(api.addMarker(frame, ""), () => say("Marker added")); break;
      case "still": setStill(); break;
      case "named_still": setDialog({ kind: "still" }); break;
      case "source_frame": setExact((x) => !x); break;
      case "delete_clip": if (selectedClip) deleteClip(selectedClip.id); break;
      case "undo": undo(); break;
      case "redo": undo(true); break;
      case "help": setDialog({ kind: "help" }); break;
    }
  }, [project, frame, fps, inPoint, outPoint, addClip, setStill, selectedClip, deleteClip, seek, jumpTo, run, say, undo, setMarks, markIn, markOut, clearMarks]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { if (e.key === "Escape") (e.target as HTMLElement).blur(); return; }
      if (dialog) { if (e.key === "Escape") setDialog(null); return; }   // keys belong to the open window
      let combo = comboOf(e);
      if (!combo) return;
      // the usual aliases: Backspace deletes like Delete, Ctrl+Y redoes like Ctrl+Shift+Z
      if (combo === "Backspace" && bindings.delete_clip === "Delete") combo = "Delete";
      const id = combo === "Mod+Y" ? "redo" : actionFor(bindings, combo);
      if (!id) return;
      e.preventDefault();
      doAction(id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [bindings, doAction, dialog]);

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
            <div className="file"><b>{ws!.source.split(/[\\/]/).pop()}</b> · {info!.width}x{info!.height} {info!.fps.toFixed(2)}p {info!.is_hlg ? "HLG" : info!.color_transfer} · {info!.start_timecode} · {(info!.duration / 3600).toFixed(2)} h
              {!ws!.source_online && <span className="offline" title="The original recording is not reachable. Marking works from the filmstrip and proxy; rendering can use the proxy.">original offline</span>}</div>
            <GameFields key={ws!.source} game={project.game} onChange={(g) => run(api.game(g))} />
          </>
        ) : <div className="file dim">No file open</div>}
        {project && (
          <>
            <button className="btn small" onClick={() => undo()} disabled={!undoLabel()} title={undoLabel() ? tip(`Undo ${undoLabel()}`, bindings, "undo") : "Nothing to undo"}>↶ Undo</button>
            <button className="btn small" onClick={() => undo(true)} disabled={!undoLabel(true)} title={undoLabel(true) ? tip(`Redo ${undoLabel(true)}`, bindings, "redo") : "Nothing to redo"}>↷</button>
          </>
        )}
        <button className="btn" onClick={openFile} title="Open a different recording">Open recording…</button>
        <button className="btn small icon" onClick={() => setDialog({ kind: "settings" })} title="Settings: keyboard shortcuts, lists" aria-label="Settings">
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.4 7.4 0 0 0-1.7-1L14.9 3h-4l-.4 2.9a7.4 7.4 0 0 0-1.7 1l-2.5-1-2 3.5L6.4 11a7.6 7.6 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a7.4 7.4 0 0 0 1.7 1l.4 2.9h4l.4-2.9a7.4 7.4 0 0 0 1.7-1l2.5 1 2-3.5zM12.9 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z" /></svg>
        </button>
        <button className="btn small" onClick={() => setDialog({ kind: "help" })} title={tip("Keyboard shortcuts", bindings, "help")}>?</button>
      </div>

      <div className="main">
        {project ? (
          <>
            <Player ref={player} info={info!} proxyUrl={ws!.proxy_url} frame={frame} onFrame={onFrame} exactView={exactView} onStatus={setPstatus} onError={(m) => say(m, true)} startOffset={ws!.proxy_start_offset} />
            <div className="transport">
              <div className="trow">
                <div className="readout">
                  <Tc value={tcOf(frame)} className="big" />
                  <div className="stack" title={pstatus.mode === "not in proxy yet" ? "The proxy has not been built this far yet: the picture is a source frame or the nearest filmstrip thumbnail, and play waits until the proxy reaches here." : "Frames from the start of the recording, and what the picture is showing"}>
                    <span className="num">{frame.toLocaleString()}<span className="unit">fr</span></span>
                    <span className={`caps mode ${pstatus.mode === "not in proxy yet" || pstatus.rate !== 0 ? "accent" : "dim"}`}>{pstatus.rate !== 0 ? `${pstatus.rate}×` : pstatus.mode}</span>
                  </div>
                  <span className="phasechip readoutphase" title="Part of the broadcast at the playhead (set the boundaries in the Broadcast panel)">{(() => { const ph = phaseOf(project, frame); return ph ? PHASE_LABEL[ph] : "—"; })()}</span>
                </div>
                <div className="tgroup">
                  <button className="btn small" onClick={() => nextMarker(-1)} title={tip("Previous marker", bindings, "prev_marker")}>◀ Prev</button>
                  <button className="btn small icon" onClick={() => doAction("go_in")} disabled={inPoint === null} title={tip("Go to in", bindings, "go_in")}>⇤</button>
                  <button className="btn small icon" onClick={() => doAction("step_back")} title={tip("Back one frame", bindings, "step_back")}>◀|</button>
                  <button className="btn small icon play" onClick={() => player.current?.toggle()} title={tip("Play / pause", bindings, "play")}>{pstatus.rate !== 0 ? "❚❚" : "▶"}</button>
                  <button className="btn small icon" onClick={() => doAction("step_fwd")} title={tip("Forward one frame", bindings, "step_fwd")}>|▶</button>
                  <button className="btn small icon" onClick={() => doAction("go_out")} disabled={outPoint === null} title={tip("Go to out", bindings, "go_out")}>⇥</button>
                  <button className="btn small" onClick={() => nextMarker(1)} title={tip("Next marker", bindings, "next_marker")}>Next ▶</button>
                </div>
                <Shuttle rate={pstatus.rate} onRate={(r) => player.current?.setRate(r)} disabled={!ws!.proxy_url} />
              </div>
              <div className="trow">
                <button className="btn small" onClick={markIn} title={tip("Mark in at the playhead", bindings, "mark_in")}>{"{"} In</button>
                <button className="btn small" onClick={markOut} title={tip("Mark out at the playhead", bindings, "mark_out")}>Out {"}"}</button>
                <div className={`mark ${inPoint !== null ? "set" : ""}`} onClick={() => inPoint !== null && jumpTo(inPoint)} title={inPoint !== null ? tip("Go to in", bindings, "go_in") : tip("No in point: mark one", bindings, "mark_in")}>
                  <span className="k">In</span><span className="tc">{inPoint !== null ? tcOf(inPoint) : "--:--:--:--"}</span></div>
                <div className={`mark ${outPoint !== null ? "set" : ""}`} onClick={() => outPoint !== null && jumpTo(outPoint)} title={outPoint !== null ? tip("Go to out", bindings, "go_out") : tip("No out point: mark one", bindings, "mark_out")}>
                  <span className="k">Out</span><span className="tc">{outPoint !== null ? tcOf(outPoint) : "--:--:--:--"}</span></div>
                <div className={`mark dur ${inPoint !== null && outPoint !== null && outPoint > inPoint ? "set" : ""}`} title="Duration of the in–out range">
                  <span className="k">Dur</span><span className="tc">{inPoint !== null && outPoint !== null && outPoint > inPoint ? durOf(outPoint - inPoint) : "--:--:--:--"}</span></div>
                <button className="btn small icon" onClick={clearMarks} disabled={inPoint === null && outPoint === null} title={tip("Clear in and out", bindings, "clear_marks")}>✕</button>
                <span className="gap" />
                <button className="btn primary small" onClick={addClip} disabled={inPoint === null && outPoint === null} title={tip("Add the in–out range as a clip", bindings, "add_clip")}>Add clip</button>
                <button className="btn small" onClick={setStill} title={tip("Still: set this clip's still, add another, or mark one for the next clip", bindings, "still")}>Still</button>
                <button className="btn small" onClick={() => doAction("add_marker")} title={tip("Add a marker at the playhead", bindings, "add_marker")}>Marker</button>
                <span className="spacer" />
                <label className="dim opt" title={tip("Show the exact source frame while paused (works before the proxy exists)", bindings, "source_frame")}><input type="checkbox" checked={exactView} onChange={(e) => setExact(e.target.checked)} /> Source frame</label>
                <label className="dim opt" title="Keep the playhead in view in the thumbnails"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow</label>
                <label className="dim zoomctl" title="Thumbnail size (Ctrl+wheel over the thumbnails)">
                  <svg viewBox="0 0 20 14" width="16" height="12" aria-hidden="true"><rect x="0.5" y="4.5" width="6" height="5" fill="none" stroke="currentColor" /><rect x="9.5" y="0.5" width="10" height="13" fill="none" stroke="currentColor" /></svg>
                  <input type="range" min={THUMB_MIN} max={THUMB_MAX} step={8} value={thumbW} onChange={(e) => setThumbW(Number(e.target.value))} aria-label="Thumbnail size" />
                </label>
              </div>
            </div>
            <div className="strip-area">
              {strip ? (
                <Filmstrip index={strip} nbFrames={info!.nb_frames} fps={fps} frame={frame} markers={project.markers} clips={project.clips} selectedId={selected}
                  inPoint={inPoint} outPoint={outPoint} pendingStill={pendingStill} onSeek={seek} follow={follow} reveal={reveal} thumbW={thumbW} onThumbW={setThumbW} tcOf={tcOf}
                  proxySeconds={ws!.proxy_url ? ws!.proxy_available_seconds : 0}
                  spans={phaseSpans(project)} anthem={(() => { const a = boundary(project, "anthem_in"), b = boundary(project, "anthem_out"); return a !== null && b !== null && b > a ? [a, b] as [number, number] : null; })()} />
              ) : (
                <div className="strip-empty hatch">
                  <span>No filmstrip yet.</span><button className="btn primary" onClick={() => run(api.startFilmstrip({}))}>Build filmstrip</button><span className="dim">About five minutes for a full game.</span>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="welcome hatch">
            <h1><span>&gt;</span> Reel Quick</h1>
            <div>Open a game recording to scan it, mark your segments, and render them.</div>
            <button className="btn primary" onClick={openFile}>Open recording…</button>
            {recent.length > 0 && (
              <div className="recent">
                <h2>Recent games</h2>
                {recent.map((r) => (
                  <div key={r.root} className="recentrow" onClick={() => openPath(r.root)}>
                    <span className="g">{[r.game?.date, [r.game?.week, r.game?.away && r.game?.home ? `${r.game.away}v${r.game.home}` : ""].filter(Boolean).join("-")].filter(Boolean).join(" · ") || r.root.split(/[\\/]/).pop()}</span>
                    <span className="dim">{r.clips} clip{r.clips === 1 ? "" : "s"}</span>
                    <span className={r.has_filmstrip ? "ok" : "dim"}>strip</span>
                    <span className={r.has_proxy ? "ok" : "dim"}>proxy</span>
                    <span className={r.source_online ? "ok" : "offline"}>{r.source_online ? "original online" : "original offline"}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="dim" style={{ fontSize: 11 }}>Working files go in {state.work_root}. A game can be opened from there even when its recording is not attached.</div>
          </div>
        )}
      </div>

      <div className="side">
        {project ? (
          <>
            <div>
              <Fold id="scan"><JobsPanel state={state} onFilmstrip={(force) => run(api.startFilmstrip({ force }))} onProxy={(height) => run(api.startProxy({ height }))} onCancel={(id) => run(api.cancelJob(id))}
                onAudioTracks={(t) => run(api.settings({ audio_tracks: t }), () => say("Stereo pair saved. Rebuild the proxy to hear it there."))}
                onSurround={(sv) => run(api.settings(sv))}
                onAudioPreview={() => { setDialog({ kind: "audio", tracks: null }); api.audioPreview(frame).then((r) => setDialog({ kind: "audio", tracks: r.tracks })).catch((e) => { setDialog(null); say(e.message, true); }); }} /></Fold>
              <Fold id="broadcast"><BroadcastPanel project={project} frame={frame} tcOf={tcOf} onSet={setBoundary} onSeek={jumpTo} onImport={importPhases} /></Fold>
              <Fold id="markers"><MarkersPanel markerKey={showKey(bindings.add_marker || undefined)} markers={project.markers} info={info!} onSeek={jumpTo} onImport={importCsv} onDelete={(f) => run(api.deleteMarker(f))}
                onDeleteUntimed={(i) => run(api.deleteUntimed(i))}
                offset={project.settings.log_offset_seconds} onOffset={(s) => run(api.settings({ log_offset_seconds: s }), () => say("Log clock saved. Import the log again to apply it."))} /></Fold>
            </div>
            {/* the clip list keeps its heading with it, so it never scrolls away with the panels above */}
            <div className="clipsarea">
            <div className="panel-head" style={{ padding: "10px 12px 0" }}><h2>Clips <span className="dim" style={{ letterSpacing: 0 }}>{project.clips.length}</span></h2>
              {selectedClip && <button className="btn small" onClick={() => jumpTo(selectedClip.in_frame)}>Go to in</button>}</div>
            <ClipList clips={project.clips} info={info!} selected={selected} onSelect={setSelected} onPatch={patchClip} onDelete={deleteClip} onSeek={jumpTo} onExport={(id, o) => startRender([id], o)} renderRows={renderRows}
              keys={{ in: showKey(bindings.mark_in || undefined), out: showKey(bindings.mark_out || undefined), add: showKey(bindings.add_clip || undefined) }}
              phaseOfClip={(c) => clipPhase(project, c)} copyOk={ws!.stream_copy_ok} />
            </div>
            <div>
              {selectedClip && <ClipEditor clip={selectedClip} info={info!} frame={frame} vocab={state.vocab} autoPhase={phaseOf(project, selectedClip.in_frame)} onPatch={(p) => patchClip(selectedClip.id, p)} onSeek={jumpTo} onSay={say} />}
              <RenderPanel space={space} job={renderJob} clipCount={project.clips.length} missingStills={project.clips.filter((c) => c.still_frame === null).length} selectedId={selected} outDir={ws!.clips_dir}
                onRender={() => startRender()}
                onRenderSelected={() => selected && startRender([selected])}
                onCancel={() => renderJob && run(api.cancelJob(renderJob.id))}
                onReveal={() => run(api.reveal(ws!.clips_dir))}
                onPickOut={async () => { const n = native(); if (n) { const p = await n.pick_folder(ws!.clips_dir); if (p) run(api.settings({ output_dir: p })); } else setDialog({ kind: "outdir" }); }} />
            </div>
          </>
        ) : <div className="panel dim hatch" style={{ height: "100%" }} />}
      </div>

      <input ref={csvInput} type="file" accept=".csv,.tsv,.txt" style={{ display: "none" }} onChange={onCsvFile} />
      <input ref={jsonInput} type="file" accept=".json" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) f.text().then(loadPhases); e.target.value = ""; }} />
      {dialog?.kind === "open" && <FileDialog title="Open a recording" onClose={() => setDialog(null)} onPick={(p) => { setDialog(null); openPath(p); }} />}
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
      {dialog?.kind === "outroot" && <FileDialog title="Output folder for every game" dirsOnly start={state.prefs.output_root || state.work_root} onClose={() => setDialog({ kind: "settings", tab: "general" })} onPick={(p) => { setDialog({ kind: "settings", tab: "general" }); run(api.prefs({ output_root: p }), () => say("Output folder saved")); }} />}
      {dialog?.kind === "settings" && <SettingsDialog initialTab={dialog.tab} bindings={bindings} vocab={state.vocab} onClose={() => setDialog(null)}
        outputRoot={state.prefs.output_root} workRoot={state.work_root} space={space} gameFolder={ws?.clips_dir ?? ""}
        onPickOutputRoot={async () => { const n = native(); if (n) { const p = await n.pick_folder(state.prefs.output_root || state.work_root); if (p) run(api.prefs({ output_root: p }), () => say("Output folder saved")); } else setDialog({ kind: "outroot" }); }}
        onClearOutputRoot={() => run(api.prefs({ output_root: "" }), () => say("Clips go in each game's work folder again"))}
        onSaveBindings={(b) => run(api.prefs({ shortcuts: overridesOf(b) }), () => say("Keyboard shortcuts saved"))}
        onSaveVocab={(v) => run(api.vocab(v), () => say("Lists saved"))} />}
      {dialog?.kind === "help" && (
        <div className="modal-bg" onClick={() => setDialog(null)}>
          <div className="modal" style={{ width: 520, gridTemplateRows: "auto 1fr" }} onClick={(e) => e.stopPropagation()}>
            <header><h2>Keyboard</h2></header>
            <div className="help">
              {ACTIONS.map((a) => <Fragment key={a.id}><span className="kbd">{showKey(bindings[a.id] || undefined)}</span><span>{a.label}</span></Fragment>)}
              {([["shuttle", "drag the knob in the transport: further is faster, let go to stop"], ["two-finger swipe", "jog over the picture: swipe right to go forward; slow for single frames, flick for seconds (Shift+wheel with a mouse)"], ["Ctrl+wheel", "zoom the thumbnails"]] as const).map(([k, v]) => <Fragment key={k}><span className="kbd">{k}</span><span>{v}</span></Fragment>)}
              <span /><span className="dim">Change any of these in Settings ⚙ → Keyboard.</span>
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
  function durOf(n: number) {
    // a duration counts frames, so it is always non-drop
    return framesToTcLocal(n, Math.round(info?.fps ?? 60), false);
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

/** A side panel that folds to its heading: click the heading to close or open it. Remembered across launches. */
function Fold({ id, children }: { id: string; children: React.ReactNode }) {
  const [closed, setClosed] = useState(() => getUi(`fold.${id}`, false));
  const toggle = (e: React.MouseEvent) => {
    if (!(e.target as HTMLElement).closest(".panel-head h2")) return;
    setClosed((c) => { setUi(`fold.${id}`, !c); return !c; });
  };
  return <div className={`fold ${closed ? "closed" : ""}`} onClick={toggle}>{children}</div>;
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
