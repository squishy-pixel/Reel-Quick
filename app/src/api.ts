export interface AudioTrack { stream_index: number; channels: number; sample_rate: number; codec: string }
export interface MediaInfo {
  path: string; size_bytes: number; duration: number; codec: string; profile: string; width: number; height: number;
  rate_num: number; rate_den: number; fps: number; nb_frames: number; is_intra: boolean; is_hlg: boolean; is_hdr: boolean;
  start_timecode: string | null; drop_frame: boolean; audio: AudioTrack[]; color_transfer: string | null;
}
export interface Marker { frame: number; tc: string; kind: string; shell: string; description: string; source: string; confidence: number; end_frame: number | null }
export interface Clip {
  id: string; in_frame: number; out_frame: number; name: string; shell: string; desc: string; formats: string[];
  head_seconds: number; tail_seconds: number; still_frame: number | null; rendered: Record<string, string>; notes: string;
}
export interface Still { id: string; frame: number; name: string; rendered: string | null }
export interface Game { date: string; week: string; away: string; home: string }
export interface Settings {
  audio_tracks: number[]; prores_profile: string; mp4_sdr: boolean; mp4_height: number | null; copy_container: string;
  still_format: string; still_hdr: boolean; default_formats: string[]; name_pattern: string;
  default_head_seconds: number; default_tail_seconds: number; log_offset_seconds: number; output_dir: string;
}
export interface Project { version: number; source: string; info: MediaInfo; game: Game; settings: Settings; markers: Marker[]; clips: Clip[]; stills: Still[] }
export interface Job {
  id: string; kind: string; label: string; status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number; detail: any; error: string | null; started: number | null; finished: number | null; elapsed: number; eta: number | null; result: any;
}
export interface Workspace { source: string; root: string; project_path: string; has_filmstrip: boolean; has_proxy: boolean; proxy_url: string | null; clips_dir: string }
export interface AppState {
  version: string; workspace: Workspace | null; project: Project | null; jobs: Job[]; work_root: string;
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
  addClip: (b: { in_frame: number; out_frame: number; shell?: string; desc?: string; formats?: string[] }) => call<{ clip: Clip; project: Project }>("POST", "/api/clips", b),
  patchClip: (id: string, b: Partial<Clip>) => call<{ clip: Clip; project: Project }>("PATCH", `/api/clips/${id}`, b),
  deleteClip: (id: string) => call<{ project: Project }>("DELETE", `/api/clips/${id}`),
  addStill: (frame: number, name: string) => call<{ still: Still; path: string; project: Project }>("POST", "/api/stills", { frame, name }),
  deleteStill: (id: string) => call<{ project: Project }>("DELETE", `/api/stills/${id}`),
  importMarkers: (csv: string, offset_seconds?: number) => call<{ added: number; skipped: string[]; project: Project }>("POST", "/api/markers/import", { csv, offset_seconds }),
  addMarker: (frame: number, description: string) => call<{ project: Project }>("POST", "/api/markers", { frame, description }),
  deleteMarker: (frame: number) => call<{ project: Project }>("DELETE", `/api/markers/${frame}`),
  filmstrip: () => call<FilmstripIndex>("GET", "/api/filmstrip"),
  jobs: () => call<Job[]>("GET", "/api/jobs"),
  startFilmstrip: (b: { interval?: number; width?: number; force?: boolean }) => call<Job>("POST", "/api/jobs/filmstrip", b),
  startProxy: (b: { height?: number; bitrate?: string }) => call<Job>("POST", "/api/jobs/proxy", b),
  startRender: (b: { clip_ids?: string[]; overwrite?: boolean }) => call<Job>("POST", "/api/jobs/render", b),
  cancelJob: (id: string) => call<{ ok: boolean }>("POST", `/api/jobs/${id}/cancel`),
};

// Native dialogs when running inside the pywebview window.
declare global { interface Window { pywebview?: { api: { pick_file(): Promise<string | null>; pick_folder(): Promise<string | null>; pick_csv(): Promise<string | null> } } } }
export const native = () => window.pywebview?.api ?? null;
