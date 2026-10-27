# Reel Quick

Find, mark and cut the segments you made out of a six hour broadcast recording,
and render just those clips at broadcast quality.

Built for the Thursday Night Football workflow: a 600 to 700 GB MXF return feed
(H.264 4:2:2 Intra, 1920x1080, 59.94, HLG), a tech's log of what aired when, and
20 to 30 pieces of 7 to 12 seconds each that need to come out as ProRes 422
with a couple of seconds of handles, named consistently, carrying the original
time-of-day timecode.

## Status

**Phase 1 engine: done and tested.** Python package in `engine/` with a CLI.
Verified against a synthetic file with the same codec, color, timecode and
audio layout as the real feed:

- `probe` reads codec, GOP structure, HDR tags, drop-frame time-of-day timecode, audio layout
- `markers` maps the log CSV (Time, Type, Shell, Description) onto the file by time of day
- `filmstrip` extracts a thumbnail every N seconds for a visual sweep of the whole show
- `proxy` renders a browser-friendly SDR MP4 of the whole program (GPU encode when available)
- `cut` renders one clip in any mix of stream copy, ProRes 422 HLG, and share MP4, frame accurate, with handles, plus a reference still
- `still` saves any single frame as a named PNG, JPEG or TIFF, tone-mapped to SDR by default
- `init` and `render` manage a project file with the clip list and batch render it

Not yet built: the desktop UI (Electron), content detection, OCR naming, the iPad companion.

## Design decisions

**Stream copy is frame accurate on this feed.** The source is intra-only
(`High 4:2:2 Intra`, GOP N=1), so every frame is a cut point. Verified by
comparing decoded pixel hashes of the cut against the source. The engine
refuses stream copy on long-GOP sources and tells you to use ProRes.

**Frame numbers are the source of truth.** Timecode strings exist for humans
and for the log. All math happens in integer frames, with drop-frame handled
in `timecode.py`, so 59.94 never drifts.

**Time of day maps straight to the file.** The MXF carries time-of-day
drop-frame timecode (15:25:00;04 on the sample game). A log entry of 19:32:05
is a frame number, no sync point needed. One offset setting handles a log
clock in a different time zone or running a few seconds off.

**HDR stays HDR unless you ask.** ProRes and stream copy outputs carry the
source's BT.2020 / HLG tags. Share MP4s, the proxy, thumbnails and stills are
converted to BT.709 SDR so they look right on anyone's screen, with an HLG
HEVC option for the MP4.

**SDR conversion uses the broadcast's own LUT.** Drop the NBCU HLG to SDR
display-light .cube in `engine/luts/` and every SDR output gets the on-air
look. The LUT is applied on 10-bit code values (narrow range, 64 = black,
940 = white), which `reelquick lut-check` confirmed: its corners sit on SDI
codes 4 and 1007, reference white lands at 94% and highlights ride into
super-white. Without a LUT, a BT.2408 style conversion is used that matches
the LUT through the midtones and differs only in the highlight knee.

**Audio is lossless either way.** Broadcast MXFs carry 16 mono PCM tracks.
The engine joins the chosen tracks (default 1 and 2) into one stereo PCM
track for ProRes and copy outputs, AAC for MP4.

**Every clip can go out in several formats at once.** The usual pair is
ProRes 422 for the reels and a share MP4 for the designers. Each format in a
clip's list renders from the same in and out, so the two never disagree.

**Every clip has a representative still.** Default is the middle frame of the
content, or pick one. It lands in `stills/` next to the clips with the same
name as the clip. Any other frame can be saved with a hand-typed name.

**The proxy doubles as the reference MP4.** One render, two uses: scrubbing in
the app, and the plain copy of the show you keep anyway. Keyframe every second
for fast seeking.

**Seek points are floored to the microsecond.** ffmpeg keeps the first packet
at or after `-ss`, in copy and transcode alike. Flooring never loses the first
frame; `-frames:v` pins the count at the far end.

## Naming

```
{date}_{week}_{matchup}_{seq:02d}_{shell}_{desc}
261022_WK08_NEvCHI_07_LowerThird_Herbstreit-pick.mov
```

Date as YYMMDD, NFL week as WKnn, away team then home team, a sequence number
by in point, the Vizrt shell, then free text. Empty parts drop out cleanly.

## Log sheet format

Four columns, header row optional:

| Time | Type | Shell | Description |
|------|------|-------|-------------|
| 19:32:05 | AR | LowerThird | Herbstreit pick |
| 7:41 PM | booth | | Al and Kirk open |

Time is time of day on the truck clock, in Eastern. The recorder timecode is
Pacific, so three hours are subtracted by default. `19:32:05`, `7:32:05 PM`, `19:32`,
and `19:32:05;12` all parse.

## Running the engine (Windows, Mac, Linux)

Needs an ffmpeg build with `prores_ks`, `zscale` and `tonemap` (the gyan.dev
"full" build on Windows has them all plus NVENC). Python is handled by uv, so
nothing else needs installing first.

```powershell
# one time, in PowerShell
winget install Gyan.FFmpeg
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# close and reopen PowerShell so ffmpeg and uv are on PATH
git clone -b claude/tnf-clip-extraction-tool-d6mcix https://github.com/squishy-pixel/Reel-Quick
cd Reel-Quick\engine
uv sync --extra dev

# every time, from Reel-Quick\engine
uv run reelquick caps                          # what can this machine do (NVENC, tonemap...)
uv run reelquick probe D:\Records\game.mxf
uv run reelquick markers D:\Records\game.mxf log.csv
uv run reelquick filmstrip D:\Records\game.mxf D:\Work\game\strip --interval 2 --workers 8
uv run reelquick proxy D:\Records\game.mxf D:\Work\game\proxy.mp4
uv run reelquick cut D:\Records\game.mxf D:\Work\game\clips --in 19:32:05 --out 19:32:14 --format prores --format mp4 --name 261022_WK08_NEvCHI_01_LowerThird_Herbie-pick
uv run reelquick still D:\Records\game.mxf D:\Work\game\clips\stills --at 19:32:09 --name 261022_WK08_NEvCHI_booth-wide
```

`uv sync` downloads a private Python and the dependencies into `engine\.venv`
on first run. `uv run` uses that environment without activating anything.
If ffmpeg is not on PATH, put `ffmpeg.exe` and `ffprobe.exe` in `engine\bin\`
or set `REELQUICK_FFMPEG_DIR` to the folder that holds them.

Output layout for one clip rendered as ProRes plus MP4:

```
clips\261022_WK08_NEvCHI_01_LowerThird_Herbie-pick.mov
clips\261022_WK08_NEvCHI_01_LowerThird_Herbie-pick.mp4
clips\stills\261022_WK08_NEvCHI_01_LowerThird_Herbie-pick.png
```

Run the tests with `uv run pytest` inside `engine/`. They generate a short
synthetic MXF in the real feed's format, so no media is needed.

## First session on the real machine

Three numbers decide the rest of the design:

1. `reelquick probe` on the real MXF: confirms intra and time-of-day timecode on the actual file.
2. `reelquick filmstrip` timing: how long the visual sweep takes on a 622 GB file over NVMe and over the NAS.
3. `reelquick proxy` timing with NVENC against the 2.5 hour CPU benchmark.

## Roadmap

1. **Desktop app** (Electron, React). Open MXF, background jobs with progress,
   markers on a timeline, filmstrip, JKL player, in/out marking, clip list with
   per-clip format, render queue. Windows first, Mac from the same code.
2. **Detection.** Reference library from past weeks' clips grouped by type and
   shell. GPU image embeddings on sampled frames, audio cues (fireworks, music),
   candidate markers with suggested in/out snapped to shot changes. Confirmed
   clips feed back into the library. OCR name suggestions.
3. **iPad companion.** The same UI as a plain web page: load proxy and project
   from Files, mark on the plane, export the clip list, render on the PC.

## Layout

```
engine/reelquick/
  timecode.py   drop-frame math, time-of-day parsing
  probe.py      ffprobe -> MediaInfo
  ffmpeg.py     locate binaries, run with progress, capability checks
  color.py      HLG tags, SDR tone-map chain
  audio.py      track selection and joining
  filmstrip.py  thumbnail sweep
  proxy.py      whole-program MP4
  render.py     clip rendering (copy / prores / mp4)
  stills.py     single-frame grabs
  markers.py    log CSV import
  naming.py     file name pattern
  project.py    project file (JSON)
  cli.py        command line
engine/tests/   pytest suite with synthetic MXF fixture
```
