import type { Candidate, MediaInfo } from "../api";
import { durationText, framesToTc } from "../timecode";
import { startFrames } from "./Player";

const WHAT_LABEL: Record<Candidate["what"], string> = { shell: "graphic", booth: "booth", scenic: "scenic", closeup: "close-up", fireworks: "fireworks", other: "other" };

/** The detector's suggestions, to accept as clips or dismiss. */
export function CandidateList({ candidates, info, selected, showAll, onSelect, onAccept, onDismiss, onRestore, keys }: {
  candidates: Candidate[]; info: MediaInfo; selected: string | null; showAll: boolean;
  onSelect: (c: Candidate) => void; onAccept: (c: Candidate, label?: string) => void; onDismiss: (c: Candidate) => void; onRestore: (c: Candidate) => void;
  keys: { next: string; accept: string; dismiss: string };
}) {
  const rate = { num: info.rate_num, den: info.rate_den };
  const base = startFrames(info);
  const tc = (f: number) => framesToTc(base + f, rate, info.drop_frame);
  const shown = candidates.filter((c) => showAll || c.status === "pending");
  const top = Math.max(1, ...candidates.map((c) => c.strength));
  if (!candidates.length) return (
    <div className="clips"><div className="hatch dim" style={{ padding: "26px 16px", textAlign: "center", lineHeight: 1.8 }}>
      No suggestions yet.<br />Import the detector's <span className="num">&lt;game&gt;.candidates.json</span> with Import… above.</div></div>
  );
  return (
    <div className="clips">
      {!shown.length && <div className="dim" style={{ padding: 16, textAlign: "center" }}>Every suggestion has been decided.</div>}
      {shown.map((c) => (
        <div key={c.id} className={`candrow ${c.status} ${selected === c.id ? "selected" : ""}`} onClick={() => onSelect(c)}>
          <div className="line1">
            <span className={`what ${c.what}`}>{WHAT_LABEL[c.what]}</span>
            <span className="name">{c.label || "unlabelled"}</span>
            <span className="strength" title={`Detector strength ${c.strength.toFixed(1)}: higher is surer`}><i style={{ width: `${Math.min(100, (c.strength / top) * 100)}%` }} /></span>
          </div>
          <div className="line2">
            <span className="tc">{tc(c.in_frame)}</span><span className="dim">→</span><span className="tc">{tc(c.out_frame)}</span>
            <span className="dim num">{durationText(c.out_frame - c.in_frame, rate)}</span>
            {c.status !== "pending" && <span className="muted">{c.status}</span>}
          </div>
          {c.status === "pending" && (
            <div className="line3" onClick={(e) => e.stopPropagation()}>
              {c.alternatives.length > 0 && <span className="dim">or</span>}
              {c.alternatives.map((a) => <span key={a} className="chip" title={`Accept as ${a} instead`} onClick={() => onAccept(c, a)}>{a}</span>)}
              <span style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                <button className="btn small primary" onClick={() => onAccept(c)} title={`Accept as a clip (${keys.accept})`}>Accept</button>
                <button className="btn small" onClick={() => onDismiss(c)} title={`Dismiss (${keys.dismiss})`}>Dismiss</button>
              </span>
            </div>
          )}
          {c.status === "dismissed" && (
            <div className="line3" onClick={(e) => e.stopPropagation()}>
              <span style={{ marginLeft: "auto" }}><button className="btn small" onClick={() => onRestore(c)}>Restore</button></span>
            </div>
          )}
        </div>
      ))}
      <div className="dim" style={{ fontSize: 10, padding: "8px 12px" }}>{keys.next} next · {keys.accept} accept · {keys.dismiss} dismiss</div>
    </div>
  );
}
