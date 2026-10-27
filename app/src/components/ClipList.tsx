import type { Clip, MediaInfo, Vocab } from "../api";
import { durationText, framesToTc } from "../timecode";
import { startFrames } from "./Player";

const FORMATS: { key: string; label: string; title: string }[] = [
  { key: "prores", label: "ProRes", title: "ProRes 422, 10-bit, HDR tags kept. The broadcast-quality file." },
  { key: "mp4", label: "MP4", title: "H.264 share file, converted to SDR through the LUT." },
  { key: "copy", label: "Original", title: "The source frames untouched, no re-encode, in a .mov. Frame accurate on this intra codec." },
];

export function ClipList({ clips, info, selected, onSelect, onPatch, onDelete, onSeek, renderRows }: {
  clips: Clip[]; info: MediaInfo; selected: string | null; onSelect: (id: string) => void;
  onPatch: (id: string, patch: Partial<Clip>) => void; onDelete: (id: string) => void; onSeek: (frame: number) => void;
  renderRows: Record<string, { status: string; progress: number }>;
}) {
  const rate = { num: info.rate_num, den: info.rate_den };
  const base = startFrames(info);
  const tc = (f: number) => framesToTc(base + f, rate, info.drop_frame);
  return (
    <div className="clips">
      {clips.length === 0 && (
        <div className="hatch dim" style={{ padding: "26px 16px", textAlign: "center", lineHeight: 1.8 }}>
          No clips yet.<br />Mark <span className="kbd">I</span> and <span className="kbd">O</span>, then <span className="kbd">Enter</span> adds the range.
        </div>
      )}
      {clips.map((c, i) => {
        const still = c.still_frame ?? Math.floor((c.in_frame + c.out_frame) / 2);
        const r = renderRows[c.id];
        const rendered = Object.keys(c.rendered || {}).filter((k) => ["prores", "mp4", "copy"].includes(k));
        return (
          <div key={c.id} className={`cliprow ${selected === c.id ? "selected" : ""}`} onClick={() => { onSelect(c.id); }} onDoubleClick={() => onSeek(c.in_frame)}>
            <img className="still" src={`/api/frame/${still}?width=320`} alt="" loading="lazy" />
            <div style={{ minWidth: 0 }}>
              <div className="line1">
                <span className="seq">{String(i + 1).padStart(2, "0")}</span>
                <span className="name" title={c.name}>{c.name}</span>
              </div>
              <div className="line2">
                <span className="tc">{tc(c.in_frame)}</span>
                <span className="dim">→</span>
                <span className="tc">{tc(c.out_frame)}</span>
                <span className="dim num">{durationText(c.out_frame - c.in_frame, rate)}</span>
                {c.engine && <span className="muted">{c.engine}</span>}
                {c.camera && <span className="accent">{c.camera}</span>}
                <span className={`stillbadge ${c.still_frame === null ? "none" : ""}`} title={c.still_frame === null ? "No still chosen; the middle frame will be used" : `Still at ${tc(c.still_frame)}${c.extra_stills?.length ? ` plus ${c.extra_stills.length} more` : ""}`}>
                  {c.still_frame === null ? "no still" : `still${c.extra_stills?.length ? ` +${c.extra_stills.length}` : ""}`}</span>
                {r?.status === "running" && <span className="accent num">{Math.round(r.progress * 100)}%</span>}
                {r?.status === "failed" && <span style={{ color: "var(--red)" }}>failed</span>}
                {rendered.length > 0 && !r && <span className="ok" title={`rendered: ${rendered.join(", ")}${c.rendered?.origin === "proxy" ? " (from proxy)" : ""}`}>✓{c.rendered?.origin === "proxy" ? " proxy" : ""}</span>}
              </div>
              <div className="chips" onClick={(e) => e.stopPropagation()}>
                {FORMATS.map((f) => {
                  const on = c.formats.includes(f.key);
                  return <span key={f.key} className={`chip ${on ? "on" : ""}`} title={f.title} onClick={() => {
                    const next = on ? c.formats.filter((x) => x !== f.key) : [...c.formats, f.key];
                    if (next.length) onPatch(c.id, { formats: next });
                  }}>{f.label}</span>;
                })}
                <span className="chip" style={{ marginLeft: "auto" }} onClick={() => onDelete(c.id)} title="Delete clip">✕</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ClipEditor({ clip, info, frame, vocab, onPatch, onSeek }: {
  clip: Clip; info: MediaInfo; frame: number; vocab: Vocab; onPatch: (patch: Partial<Clip>) => void; onSeek: (f: number) => void;
}) {
  const rate = { num: info.rate_num, den: info.rate_den };
  const base = startFrames(info);
  const tc = (f: number) => framesToTc(base + f, rate, info.drop_frame);
  return (
    <div className="clipedit">
      <div className="row3">
        <div className="field"><label>Shell</label>
          <input className="text" list="shells" defaultValue={clip.shell} key={`sh${clip.id}-${clip.shell}`} onBlur={(e) => e.target.value !== clip.shell && onPatch({ shell: e.target.value })} placeholder="Shell" />
          <datalist id="shells">{vocab.shells.map((c) => <option key={c} value={c} />)}</datalist></div>
        <div className="field"><label>Engine</label>
          <select className="text" value={clip.engine} onChange={(e) => onPatch({ engine: e.target.value })}>
            <option value="">—</option>{vocab.engines.map((c) => <option key={c} value={c}>{c}</option>)}{clip.engine && !vocab.engines.includes(clip.engine) && <option value={clip.engine}>{clip.engine}</option>}
          </select></div>
        <div className="field"><label>Camera</label>
          <input className="text" list="cameras" defaultValue={clip.camera} key={`c${clip.id}-${clip.camera}`} onBlur={(e) => e.target.value !== clip.camera && onPatch({ camera: e.target.value })} placeholder="Camera" />
          <datalist id="cameras">{vocab.cameras.map((c) => <option key={c} value={c} />)}</datalist></div>
      </div>
      <div className="field"><label>Description</label><input className="text" defaultValue={clip.desc} key={`d${clip.id}-${clip.desc}`} onBlur={(e) => e.target.value !== clip.desc && onPatch({ desc: e.target.value })} placeholder="What it is" /></div>
      <div className="field"><label>File name</label><input className="text num" defaultValue={clip.name} key={`n${clip.id}-${clip.name}`} onBlur={(e) => e.target.value !== clip.name && onPatch({ name: e.target.value })} /></div>
      <div className="row">
        <div className="field"><label>In</label>
          <div style={{ display: "flex", gap: 4 }}><button className="btn small tc" onClick={() => onSeek(clip.in_frame)}>{tc(clip.in_frame)}</button><button className="btn small" title="Set in to playhead" onClick={() => onPatch({ in_frame: frame })}>←●</button></div></div>
        <div className="field"><label>Out</label>
          <div style={{ display: "flex", gap: 4 }}><button className="btn small tc" onClick={() => onSeek(clip.out_frame)}>{tc(clip.out_frame)}</button><button className="btn small" title="Set out to playhead" onClick={() => onPatch({ out_frame: frame })}>●→</button></div></div>
      </div>
      <div className="field"><label>Stills <span className="dim" style={{ textTransform: "none", letterSpacing: 0 }}>· the first names the file, extras become _02, _03</span></label>
        <div className="stillchips">
          {clip.still_frame === null && <span className="dim" style={{ fontSize: 11 }}>none chosen · press <span className="kbd">S</span> on the frame you want</span>}
          {clip.still_frame !== null && (
            <span className="chip on" title="Main still" onClick={() => onSeek(clip.still_frame!)}>★ {tc(clip.still_frame)}
              <i onClick={(e) => { e.stopPropagation(); const [next, ...rest] = clip.extra_stills || []; onPatch({ still_frame: next ?? null, extra_stills: rest }); }} title="Remove">✕</i></span>
          )}
          {(clip.extra_stills || []).map((f) => (
            <span key={f} className="chip" onClick={() => onSeek(f)}>{tc(f)}
              <i onClick={(e) => { e.stopPropagation(); onPatch({ extra_stills: clip.extra_stills.filter((x) => x !== f) }); }} title="Remove">✕</i></span>
          ))}
        </div>
      </div>
      <div className="row">
        <div className="field"><label>Head (s)</label><input className="text num" type="number" step="0.5" min="0" defaultValue={clip.head_seconds} key={`h${clip.id}-${clip.head_seconds}`} onBlur={(e) => onPatch({ head_seconds: Number(e.target.value) })} /></div>
        <div className="field"><label>Tail (s)</label><input className="text num" type="number" step="0.5" min="0" defaultValue={clip.tail_seconds} key={`t${clip.id}-${clip.tail_seconds}`} onBlur={(e) => onPatch({ tail_seconds: Number(e.target.value) })} /></div>
      </div>
    </div>
  );
}
