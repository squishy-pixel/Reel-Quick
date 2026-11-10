export interface AudioTrack { stream_index: number; channels: number; sample_rate: number; codec: string }
export interface MediaInfo {
  path: string; size_bytes: number; duration: number; codec: string; profile: string; width: number; height: number;
  rate_num: number; rate_den: number; fps: number; nb_frames: number; is_intra: boolean; is_hlg: boolean; is_hdr: boolean;
  start_timecode: string | null; drop_frame: boolean; audio: AudioTrack[]; color_transfer: string | null; field_order: string;
}
export interface Marker { frame: number | null; tc: string; kind: string; shell: string; description: string; camera: string; engine: string; section: string; source: string; confidence: number; end_frame: number | null }
export interface Clip {
  id: string; in_frame: number; out_frame: number; name: string; shell: string; desc: string; camera: string; engine: string; formats: string[];
  head_seconds: number; tail_seconds: number; still_frame: number | null; extra_stills: number[]; rendered: Record<string, any>; notes: string;
  phase: "" | "pre" | "game" | "half" | "post"; whose: "ours" | "nbc" | "other";
}
export interface Still { id: string; frame: number; name: string; rendered: string | null }
export interface Game { date: string; week: string; away: string; home: string }
export interface Settings {
  audio_tracks: number[]; surround_tracks: number[]; surround_in_mov: boolean; surround_in_mp4: boolean; prores_profile: string; mp4_sdr: boolean; mp4_height: number | null; mp4_quality: string; copy_container: string;
  still_format: string; still_hdr: boolean; default_formats: string[]; name_pattern: string;
  default_head_seconds: number; default_tail_seconds: number; log_offset_seconds: number; output_dir: string;
  file_by_type: boolean; other_folder: string;
}
export type BoundaryKey = "anthem_in" | "anthem_out" | "kickoff" | "half_start" | "second_half_kickoff" | "game_end";
export type Phases = Partial<Record<BoundaryKey, { frame: number | null; source: string }>>;
export interface Candidate {
  id: string; in_frame: number; out_frame: number; what: "shell" | "booth" | "scenic" | "closeup" | "fireworks" | "other";
  label: string; alternatives: string[]; whose: string; strength: number; source: string;
  status: "pending" | "accepted" | "dismissed"; clip_id: string | null;
}
export interface Project { version: number; source: string; info: MediaInfo; game: Game; settings: Settings; markers: Marker[]; clips: Clip[]; stills: Still[]; phases: Phases; candidates: Candidate[] }
export interface SpaceEstimate { bytes: number; free: number | null; dest: string; out_dir: string; fits: boolean }
export interface Job {
  id: string; kind: string; label: string; status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number; detail: any; error: string | null; started: number | null; finished: number | null; elapsed: number; eta: number | null; result: any;
}
export interface Workspace { source: string; source_online: boolean; root: string; project_path: string; has_filmstrip: boolean; has_proxy: boolean; proxy_url: string | null; proxy_complete: boolean; proxy_building: boolean; proxy_available_seconds: number; proxy_start_offset: number; clips_dir: string; playing_source: boolean; stream_copy_ok: boolean }
export interface Recent { root: string; project_path: string; source: string; source_online: boolean; game: Game; clips: number; has_proxy: boolean; has_filmstrip: boolean; modified: number }
export type Vocab = { shells: string[]; cameras: string[]; engines: string[]; segments: string[] };
export type RenderRequest = { clip_ids?: string[]; overwrite?: boolean; from_proxy?: boolean; formats?: string[]; mp4_quality?: string; stills?: boolean; force?: boolean };
export type Prefs = { shortcuts: Record<string, string>; ui: Record<string, unknown>; output_root: string };
export interface AppState {
  version: string; workspace: Workspace | null; project: Project | null; jobs: Job[]; work_root: string; vocab: Vocab;
  prefs: Prefs;
  history: { undo: string | null; redo: string | null; seq: number };
  engine: { ffmpeg: string; nvenc: boolean; lut: { name: string; range: string } | null };
}
export interface FilmstripIndex { interval_frames: number; width: number; count: number; frames: { i: number; frame: number; seconds: number; tc: string; file: string }[] }

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json() as Promise<T>;
}

export const api = {
  state: () => call<AppState>("GET", "/api/state"),
  open: (path: string) => call<AppState>("POST", "/api/open", { path }),
  fs: (path?: string) => call<{ path: string; parent: string | null; entries: { name: string; path: string; dir: boolean; size?: number }[] }>("GET", `/api/fs${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  game: (g: Game) => call<AppState>("PUT", "/api/game", g),
  settings: (s: Partial<Settings>) => call<AppState>("PUT", "/api/settings", s),
  vocab: (v: Partial<Vocab>) => call<Vocab>("PUT", "/api/vocab", v),
  prefs: (p: Partial<Prefs>) => call<Prefs>("PUT", "/api/prefs", p),
  addClip: (b: { in_frame: number; out_frame: number; shell?: string; desc?: string; camera?: string; engine?: string; formats?: string[] }) => call<{ clip: Clip; project: Project }>("POST", "/api/clips", b),
  patchClip: (id: string, b: Partial<Clip>) => call<{ clip: Clip; project: Project }>("PATCH", `/api/clips/${id}`, b),
  deleteClip: (id: string) => call<{ project: Project }>("DELETE", `/api/clips/${id}`),
  addStill: (frame: number, name: string) => call<{ still: Still; path: string; project: Project }>("POST", "/api/stills", { frame, name }),
  deleteStill: (id: string) => call<{ project: Project }>("DELETE", `/api/stills/${id}`),
  importMarkers: (csv: string, offset_seconds?: number) => call<{ added: number; untimed: number; skipped: string[]; all_outside: boolean; project: Project }>("POST", "/api/markers/import", { csv, offset_seconds }),
  addMarker: (frame: number, description: string) => call<{ project: Project }>("POST", "/api/markers", { frame, description }),
  deleteMarker: (frame: number) => call<{ project: Project }>("DELETE", `/api/markers/${frame}`),
  undo: () => call<AppState & { label: string }>("POST", "/api/undo"),
  redo: () => call<AppState & { label: string }>("POST", "/api/redo"),
  deleteUntimed: (index: number) => call<{ project: Project }>("DELETE", `/api/markers/untimed/${index}`),
  filmstrip: () => call<FilmstripIndex>("GET", "/api/filmstrip"),
  jobs: () => call<Job[]>("GET", "/api/jobs"),
  startFilmstrip: (b: { interval?: number; width?: number; force?: boolean }) => call<Job>("POST", "/api/jobs/filmstrip", b),
  startProxy: (b: { height?: number; bitrate?: string }) => call<Job>("POST", "/api/jobs/proxy", b),
  startRender: (b: RenderRequest) => call<Job>("POST", "/api/jobs/render", b),
  estimate: (b: RenderRequest) => call<SpaceEstimate>("POST", "/api/render/estimate", b),
  setPhases: (values: Partial<Record<BoundaryKey, number | null>>) => call<AppState>("PUT", "/api/phases", { values }),
  importCandidates: (data: unknown) => call<AppState & { added: number; already_decided: number }>("POST", "/api/candidates/import", { data }),
  patchCandidate: (id: string, b: { status?: "pending" | "dismissed"; label?: string }) => call<AppState>("PATCH", `/api/candidates/${id}`, b),
  acceptCandidate: (id: string, label?: string) => call<AppState & { clip: Clip }>("POST", `/api/candidates/${id}/accept`, { label }),
  importPhases: (data: unknown) => call<AppState & { changed: string[]; kept: string[] }>("POST", "/api/phases/import", { data }),
  recent: () => call<Recent[]>("GET", "/api/recent"),
  fonts: () => call<{ file: string; url: string; family: string; weight: number; italic: boolean; playoff: boolean }[]>("GET", "/api/fonts"),
  cancelJob: (id: string) => call<{ ok: boolean }>("POST", `/api/jobs/${id}/cancel`),
  reveal: (path: string) => call<{ ok: boolean }>("POST", "/api/reveal", { path }),
  audioPreview: (at_frame: number, seconds = 15) => call<{ tracks: { track: number; url: string; wave: string; channels: number }[] }>("POST", "/api/audio-preview", { at_frame, seconds }),
};

// Native dialogs when running inside the pywebview window.
declare global { interface Window { pywebview?: { api: { pick_file(): Promise<string | null>; pick_folder(start?: string): Promise<string | null>; pick_csv(): Promise<string | null>; pick_json?(): Promise<string | null> } } } }
export const native = () => window.pywebview?.api ?? null;
