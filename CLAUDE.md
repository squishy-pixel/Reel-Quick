# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Reel Quick cuts broadcast clips out of six-hour MXF game recordings (H.264 4:2:2 Intra, 1080p59.94, HLG, drop-frame time-of-day timecode, 16 mono PCM tracks). A Python engine (`engine/`) does all media work by shelling out to ffmpeg/ffprobe. A React UI (`app/`) talks to it over a local FastAPI server inside a pywebview window. `README.md` has the design rationale and the CLI usage.

Detection (phase 2: finding shells, booth, scenics and fireworks in a game, and telling ours from NBC's and other contributors') is planned in `docs/DETECTION.md`. Read it before working on detection or the clip library.

## Commands

The engine is managed with uv. Run everything from `engine/`:

```bash
uv sync --extra dev --extra app             # dev deps + pywebview
uv run --with pytest-timeout pytest -q --timeout 300   # full suite (the timeout catches ffprobe/ffmpeg hangs)
uv run pytest -q tests/test_proxy.py::test_single_and_segmented_proxy_agree   # one test
uv run reelquick caps                       # which encoders/filters this ffmpeg has (NVENC, zscale, tonemap...)
uv run reelquick app --browser --port 8765  # API + UI in the default browser instead of a window
```

The tests need no media: `tests/conftest.py` builds a 6 s synthetic MXF in the real feed's format (session fixture `synthetic_mxf`, probed as `info`). `decoded_frame_hashes` is the helper for frame-accuracy checks. Most tests run real ffmpeg encodes.

The UI is built from `app/`:

```bash
npm install
npm run dev     # Vite on :5173, proxies /api /media /strip /clips to a running `reelquick app --browser --port 8765`
npm run build   # tsc --noEmit + vite build into engine/reelquick/ui
```

The built UI in `engine/reelquick/ui/` is committed so end users need no Node. After changing anything in `app/src`, run `npm run build` and commit the regenerated `ui/` files with the change.

ffmpeg lookup order is in `ffmpeg.py:_candidate_dirs`: `REELQUICK_FFMPEG_DIR`, then `bin/` next to the package or executable, then (macOS) the keg-only Homebrew `ffmpeg-full`, then PATH. The engine needs `zscale`, so plain Homebrew `ffmpeg` won't work.

## Architecture

**Integer frames are the source of truth.** Every position is a frame number from the start of the file. `timecode.py` converts to and from drop-frame timecode and time of day only at the edges: CLI arguments, log CSV import, display. A log time maps straight to a frame through the file's start timecode plus an offset setting (default −3 h, Eastern log vs Pacific recorder). `fmt_seconds` floors seek times to the microsecond so `-ss` never skips the wanted frame, and `-frames:v` pins the count.

**Layers:**
- **ffmpeg wrapper and capabilities:** `ffmpeg.py` (`run_ffmpeg` with progress/cancel/cwd, `run_ffprobe`, `capabilities()`, `encoder_works()`, which actually encodes 2 frames because NVENC is always listed).
- **Probing:** `probe.py` produces `MediaInfo`.
- **Building blocks:** `color.py` + `cubelut.py` build the HLG→SDR filter chain, `audio.py` builds track selection/joining.
- **Operations:** `filmstrip.py`, `proxy.py`, `render.py` (copy / ProRes HLG / share MP4), `stills.py`.
- **Project state:** `project.py` (JSON project, clips, markers) and `workspace.py`.
- **Front ends:** `cli.py` (click) and `server.py` (FastAPI, localhost only), which `app.py` wraps in a window.

**Workspace and Session (`workspace.py`).** One working folder per game under the work root holds `project.reelquick.json`, `strip/`, `proxy/index.m3u8` + `proxy.mp4`, `clips/` and `.cache/`. `Session` is the single open workspace the API edits, guarded by an RLock. Undo/redo keeps whole-project snapshots: every mutating endpoint in `server.py` calls `session.remember("<label>")` *before* changing the project. Undo never takes away a clip's `rendered` record.

**Undo in the UI** is one order across two places. In/out marks live only in the window. `App.tsx` keeps its own stack: a `marks` entry for each mark change, and a `server` entry each time the server's `history.seq` (a counter that only `remember()` moves) goes up. Undo pops that stack, restoring marks locally or calling `/api/undo`. Every mark change must go through `setMarks`, or it falls out of the undo order.

**Broadcast phases and filing.** `project.phases` holds the boundaries (kickoff, half_start, second_half_kickoff, game_end, plus the anthem window), each with its source. `phase_of()` turns them into pre / game / half / post, mirrored in `app/src/phases.ts`. A detector's `<game>.phases.json` (format in `docs/DETECTION.md`, Interfaces) imports through `/api/phases/import` and never replaces a boundary set by hand. Outputs go to `project.clip_folder()`: a folder per content type, or `Other Content` for clips whose `whose` isn't `ours`. `/api/render/estimate` and the render job compare `estimate_bytes()` with the destination's free space.

**Detector suggestions.** `project.candidates` holds a `<game>.candidates.json` (format in `docs/DETECTION.md`, Interfaces), imported through `/api/candidates/import`. Each suggestion is pending, accepted or dismissed, and is identified by its span, so a re-import keeps decisions already made. Accepting one (`/api/candidates/{id}/accept`, optionally as one of its look-alikes) makes a clip. Every step goes through undo, and the app's Suggested tab steps through them with N / A / D.

**Preferences** (keyboard bindings, the output root, small UI state) live in `<work root>/prefs.json` via `prefs.py` and `PUT /api/prefs`, not in browser storage. The app window starts in private mode on a new port every launch, so `localStorage` does not survive a restart. Actions and default keys are in `app/src/shortcuts.ts`; tooltips use `tip()` so they show the live binding.

**Jobs.** Long work (filmstrip, proxy, render) runs through `jobs.JobManager` as `fn(progress_cb, cancel_event)`. `run_ffmpeg` turns ffmpeg's `out_time` into a 0–1 fraction and kills the process when the event is set.

**Color.** Outputs that keep HDR (ProRes, stream copy) keep the BT.2020/HLG tags. SDR outputs (proxy, share MP4, thumbnails, stills) go through the broadcast LUT from `engine/luts/*.cube` (git-ignored, licensed) or `REELQUICK_SDR_LUT`. The LUT works on narrow-range 10-bit code values, so `cubelut.py` bakes a full-range copy into `luts/.baked/` on first use. Without a LUT, a BT.2408-style chain is used. On ffmpeg 9, `-color_primaries`/`-color_trc` output options are ignored, so color tags are stamped on frames with a `setparams` filter (engine and test fixture alike).

**Stream copy** is only allowed on intra-only sources, where every frame is a cut point. `render.py` refuses it on long-GOP sources.

**Proxy pipeline (`proxy.py`), the most fragile area:**
- The encode writes fMP4 HLS (`proxy/index.m3u8`, `init.mp4`, `segNNNNN.m4s`, an EVENT playlist that grows while encoding), so the player can scrub while it renders. `--jobs N` encodes N frame-exact stretches at once with PCM audio and joins them, encoding AAC once at the join.
- `remux_hls_to_mp4` then remuxes the same packets into `proxy.mp4` with no second encode. It checks afterwards that the MP4 shows as many video frames as the playlist has, since an MP4 edit list can hide frames even when every packet is present.
- No B-frames anywhere. AAC priming means a container's first video frame may not sit at 0, so `video_start_offset()` measures it and the server passes it to `Player.tsx` as `proxy_start_offset`. Frame N of the proxy must equal frame N of the source in every player and container.
- `test_proxy.py::test_single_and_segmented_proxy_agree` guards all of this with frame counts, start offsets and a PSNR comparison of single-pass vs segmented output.
- Run the HLS encode with `cwd=playlist.parent` and relative names (ffmpeg 9 on Windows misplaces `init.mp4` otherwise).
- Any ffprobe on a playlist that may still be growing needs a timeout, because a playlist without `#EXT-X-ENDLIST` is live and some builds wait on it.
- `Workspace.proxy_choice` prefers a finished playlist, then a finished MP4, then the recording itself when `source_playable()` (H.264 8-bit 4:2:0 SDR in MP4/MOV, served at `/media/source`: a downloaded game needs no proxy), then a growing playlist. That way a rebuild never replaces a working proxy with a partial one. Builds write to `.partial` names and rename when done.

## Cross-platform

Development happens on Windows (gyan.dev full build, ffmpeg 9, NVENC) and macOS (Homebrew `ffmpeg-full`, VideoToolbox). Linux/Mac may be on ffmpeg 6.1. Timestamp and muxer behaviour differs between ffmpeg versions, so derive offsets from measured start times rather than hard-coding what one version produces. In tests, compare paths with forward slashes.

`docs/MAC-SETUP.md` is a step-by-step runbook for a Claude session setting the app up on the Mac (paths on the external drive, verification steps, what to report).

## Naming

Clip names come from `naming.py`. `DEFAULT_PATTERN` (`{date}_{game}_{seq:02d}_{shell}_{camera}_{desc}`) is authoritative. The pattern in the README's Naming section is the older one, kept in `OLD_PATTERNS`. Shell/camera/segment vocab lives in `vocab.py`, seeded with the TNF set and stored as `vocab.json` in the work root.
