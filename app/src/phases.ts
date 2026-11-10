// Broadcast phases, worked out the same way as project.phase_of() in the engine.
import type { BoundaryKey, Clip, Project } from "./api";

export type Phase = "" | "pre" | "game" | "half" | "post";
export const PHASE_LABEL: Record<Exclude<Phase, "">, string> = { pre: "Pre", game: "Game", half: "Half", post: "Post" };

export const BOUNDARY_ROWS: { key: BoundaryKey; label: string; title: string }[] = [
  { key: "anthem_in", label: "Anthem starts", title: "Start of the national anthem: fireworks in the anthem window are not ours" },
  { key: "anthem_out", label: "Anthem ends", title: "End of the national anthem" },
  { key: "kickoff", label: "Kickoff", title: "Pregame ends, the game starts" },
  { key: "half_start", label: "Halftime", title: "The first half has ended; the halftime show starts" },
  { key: "second_half_kickoff", label: "2nd-half kickoff", title: "Halftime ends, the second half starts" },
  { key: "game_end", label: "End of game", title: "The 4th quarter (or overtime) has ended; postgame starts" },
];

export const boundary = (p: Project, k: BoundaryKey): number | null => {
  const f = p.phases?.[k]?.frame;
  return typeof f === "number" ? f : null;
};

export function phaseOf(p: Project, frame: number): Phase {
  const k = boundary(p, "kickoff"), h = boundary(p, "half_start"), s = boundary(p, "second_half_kickoff"), e = boundary(p, "game_end");
  if (e !== null && frame >= e) return "post";
  if (s !== null && frame >= s) return "game";
  if (h !== null && frame >= h) return "half";
  if (k !== null) return frame >= k ? "game" : "pre";
  return "";
}

export const clipPhase = (p: Project, c: Clip): Phase => (c.phase ? c.phase : phaseOf(p, c.in_frame));

/** Stretches of the show for the timeline: studio (pre, half, post) and game, in order. */
export function phaseSpans(p: Project): { from: number; to: number; phase: Exclude<Phase, ""> }[] {
  const n = p.info.nb_frames;
  const cuts = (["kickoff", "half_start", "second_half_kickoff", "game_end"] as BoundaryKey[])
    .map((k) => boundary(p, k)).filter((f): f is number => f !== null).sort((a, b) => a - b);
  if (!cuts.length || boundary(p, "kickoff") === null) return [];
  const edges = [0, ...cuts, n];
  const out: { from: number; to: number; phase: Exclude<Phase, ""> }[] = [];
  for (let i = 0; i < edges.length - 1; i++) {
    if (edges[i + 1] <= edges[i]) continue;
    const ph = phaseOf(p, edges[i]);
    if (ph) out.push({ from: edges[i], to: edges[i + 1], phase: ph });
  }
  return out;
}

export const gb = (n: number | null | undefined) => (n === null || n === undefined ? "?" : n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);
