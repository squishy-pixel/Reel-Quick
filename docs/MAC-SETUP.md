# Reel Quick on the Mac: setup and first launch

Instructions for a Claude Code session running on TJ's MacBook Pro (M2 Max,
64 GB) inside this checkout at `~/Dev/Reel-Quick`. Work step by step, run
each command, and do not skip a step because it looks done. Report results
verbatim at the end.

## Context

- Reel Quick is a Python engine (`engine/`) plus a built web UI that
  `reelquick app` serves in a pywebview window. It cuts broadcast clips out
  of long MXF recordings. It was developed on a Windows laptop that is now in
  repair; that laptop's data drive is copied to an external volume.
- Data on the external volume:
  - Work root (filmstrip, proxy, project file for the TB at DAL game):
    `/Volumes/TJN_TNF26/_AI-Resources/App-Reel-Quick/Work`
    containing `NFL_261008_TB_DAL_GAME_NET_RETURN_RX105-261008152523128/`
    with `strip/`, possibly `proxy.mp4`, and `project.reelquick.json`
    (Windows paths inside; the engine remaps them when the recording is
    opened).
  - The original MXF is being copied into
    `/Volumes/TJN_TNF26/_AI-Resources/App-Reel-Quick/Work/TBvDAL/`. It is
    660 GB and may still be copying. Do not read it until its size has
    stopped changing for a minute.
  - LUT files from the old Windows clone:
    `/Volumes/TJN_TNF26/_AI-Resources/App-Reel-Quick/Reel-Quick/engine/luts/*.cube`
    (three NBCU `.cube` files). If missing, search `_AI-Resources` for
    `NBCU_HLG2SDR_DL_v1.2.cube`.
  - Last year's finished clips, for a later step, not now:
    `/Volumes/TJN_TNF26/_AI-Resources/TNF/Sample TNF Game Content/2025 TNF Girraphic On-Air Clips by Game`

## Steps

1. Tooling: `brew install ffmpeg ffmpeg-full uv` (skip what is installed).
   Homebrew's regular `ffmpeg` formula is built without zimg, so it has no
   `zscale` filter and every HLG to SDR conversion in the engine fails.
   `ffmpeg-full` is keg-only (it lives in `/opt/homebrew/opt/ffmpeg-full/bin`
   and is not linked onto PATH); the engine looks there on its own on macOS,
   and `REELQUICK_FFMPEG_DIR` overrides the choice. Confirm `uv --version`
   and `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -hide_banner -filters | grep zscale`.
2. In `engine/`: `uv sync --extra dev --extra app`. Copy the three NBCU
   `.cube` files into `engine/luts/` (git-ignored except its README).
3. `uv run reelquick caps`; record the JSON. `ffmpeg` should point into the
   `ffmpeg-full` keg and `tonemap` should be true. Also
   `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -hide_banner -encoders | grep -i videotoolbox`;
   record the list.
4. `uv run pytest -q` in `engine/`. All tests should pass on macOS. Capture
   any failure verbatim; do not change tests to make them pass. If a failure
   is clearly a macOS portability bug in the engine (a path assumption, a
   Windows-only call), fix it minimally, re-run, and note the change.
   Known ffmpeg 9 behaviour: `-color_primaries` and `-color_trc` are ignored
   as output options, so the engine and the test fixture stamp color tags
   onto the frames with a `setparams` filter instead.
5. `uv run reelquick lut-check`; record the table.
6. Once the MXF copy has finished: `uv run reelquick probe "<mxf path>"`,
   then `uv run reelquick bench "<mxf path>" --only "CPU dec"`; record both.
   If the copy is still running, skip and say so.
7. Launch the app in the background, pointed at the copied work root and
   explicitly at the `ffmpeg-full` binaries:
   `REELQUICK_FFMPEG_DIR=/opt/homebrew/opt/ffmpeg-full/bin nohup uv run reelquick app --work "/Volumes/TJN_TNF26/_AI-Resources/App-Reel-Quick/Work" --browser --port 8765 > /tmp/reelquick-app.log 2>&1 &`
   Wait ten seconds, then `cat /tmp/reelquick-app.log`. The default browser
   should open the app. Report any traceback verbatim.
8. Record what `curl -s http://127.0.0.1:8765/api/recent` returns.

## Report

Include verbatim: the caps JSON, the videotoolbox encoder list, the pytest
summary line and any failures in full, the lut-check table, the probe and
bench output if run, the app log, the `/api/recent` output, and anything
changed in the repo. Commit and push only a portability fix, with a clear
message, on the current branch. No pull requests. If a step needs a
permission you do not have, say which one rather than working around it.

If the `send_message` tool is available, also send the report to session
`session_01Hphy5Fpaj4DGPqRmvzsqBy`.
