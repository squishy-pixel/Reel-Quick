"""Command line front end. Useful on its own for benchmarking before the app exists."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click

from . import __version__
from .color import Lut, active_lut, set_lut
from .ffmpeg import FfmpegError, capabilities, encoder_works, fmt_seconds
from . import bench as benchmod, lutcheck
from .filmstrip import build_filmstrip
from .markers import import_log_csv
from .naming import GameInfo, build_name
from .probe import probe
from .project import Project
from .proxy import ProxySettings, build_proxy
from .render import ClipSpec, OutputFormat, render_clip
from .stills import FORMATS as STILL_FORMATS, grab_still
from .timecode import frames_to_tc, parse_time_of_day, tc_to_frames


def _tracks(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    return tuple(int(x) for x in text.replace(" ", "").split(",") if x)


def _parse_point(text: str, info) -> int:
    """Accept a frame number (f1234 or 1234), a timecode, or a time of day.

    Both timecode and time of day are on the file's own clock, so 19:32:05 and
    19:32:05;00 mean the same frame.
    """
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text.lower().startswith("f") and text[1:].isdigit():
        return int(text[1:])
    try:
        frames = tc_to_frames(text, info.rate, drop=info.drop_frame)
    except ValueError:
        frames = parse_time_of_day(text, info.rate)
    return frames - info.start_frames


class _Bar:
    """Single-line progress with elapsed time and ETA."""

    def __init__(self, label: str):
        self.label = label
        self.t0 = time.time()
        self.last = 0.0

    def __call__(self, frac: float, *_):
        now = time.time()
        if frac < 1.0 and now - self.last < 0.5:
            return
        self.last = now
        elapsed = now - self.t0
        eta = (elapsed / frac - elapsed) if frac > 0.001 else 0.0
        width = 30
        filled = int(width * frac)
        bar = "#" * filled + "-" * (width - filled)
        sys.stderr.write(f"\r{self.label} [{bar}] {frac*100:5.1f}%  {elapsed:6.0f}s  eta {eta:6.0f}s ")
        sys.stderr.flush()
        if frac >= 1.0:
            sys.stderr.write("\n")


@click.group()
@click.version_option(__version__)
@click.option("--lut", "lut_path", type=click.Path(exists=True, dir_okay=False), default=None,
              help="3D LUT for HDR to SDR conversion. Default: REELQUICK_SDR_LUT or the first file in engine/luts.")
@click.option("--lut-range", type=click.Choice(["narrow", "full"]), default=None,
              help="RGB range the LUT expects in and out. Default narrow (legal range).")
@click.option("--no-lut", is_flag=True, help="Use the built-in conversion even if a LUT is present.")
def main(lut_path, lut_range, no_lut) -> None:
    """Reel Quick engine."""
    if no_lut:
        set_lut(None)
    elif lut_path:
        set_lut(Lut(lut_path, lut_range or "narrow"))
    elif lut_range and active_lut():
        set_lut(Lut(active_lut().path, lut_range))


@main.command()
def caps() -> None:
    """Show what this ffmpeg build and machine can do."""
    c = capabilities()
    out = c.summary()
    out["nvenc_works"] = encoder_works("h264_nvenc") if c.nvenc else False
    lut = active_lut()
    out["sdr_lut"] = {"path": lut.path, "range": lut.range} if lut else None
    click.echo(json.dumps(out, indent=1))


@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.option("--seconds", default=30.0, show_default=True, help="Length of the slice to time.")
@click.option("--at", "at_", default=None, help="Where to take the slice: time of day, timecode, or frame. Default one hour in.")
@click.option("--only", default=None, help="Run only pipelines whose name contains this text.")
@click.option("--parallel", default=None, help="Instead of the stage table, run N copies at once, e.g. --parallel 1,2,4,6")
@click.option("--height", default=1080, show_default=True, help="Output height for the parallel runs.")
def bench(src: str, seconds: float, at_: str | None, only: str | None, parallel: str | None, height: int) -> None:
    """Time each stage of the proxy pipeline on a slice of the file to find the bottleneck."""
    info = probe(src)
    at = None
    if at_:
        at = float(_parse_point(at_, info)) / float(info.rate.fps)
    c = capabilities()
    lut = active_lut()
    click.echo(f"ffmpeg {c.version.split()[0]}  nvenc={encoder_works('h264_nvenc')} cuda_decode={c.cuda_decode} "
               f"libplacebo={c.has_filter('libplacebo')}  lut={Path(lut.path).name if lut else None}")
    if parallel:
        import os
        click.echo(f"cpu threads: {os.cpu_count()}; {seconds:.0f}s slices, whole-process timing (includes startup)\n")
        for n in (int(x) for x in parallel.split(",") if x.strip()):
            click.echo(benchmod.run_parallel_bench(info, n, seconds, at, height).line(info))
        return
    click.echo(f"slice: {seconds:.0f}s starting {at if at is not None else 'one hour in'}; each row reads the same frames\n")
    for r in benchmod.run_bench(info, seconds, at, only):
        click.echo(r.line(info))


@main.command("app")
@click.option("--port", default=None, type=int, help="Port for the local API; default picks a free one.")
@click.option("--browser", is_flag=True, help="Open in the default browser instead of the app window.")
@click.option("--work", default=None, type=click.Path(file_okay=False), help="Folder for working files (default ~/Videos/ReelQuick).")
@click.option("--open", "open_path", default=None, type=click.Path(exists=True, dir_okay=False), help="Open this file on launch.")
def app_cmd(port, browser, work, open_path) -> None:
    """Open the Reel Quick window."""
    from .app import run_app
    run_app(port=port, browser=browser, work_root=Path(work) if work else None, open_path=open_path)


@main.command("audio-preview")
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("out_dir", type=click.Path(file_okay=False))
@click.option("--at", "at_", default=None, help="Where to sample: time of day, timecode, or frame. Default one hour in.")
@click.option("--seconds", default=20.0, show_default=True)
def audio_preview(src, out_dir, at_, seconds) -> None:
    """Write a short MP3 of every audio track so the dialog pair can be found by ear."""
    from .ffmpeg import run_ffmpeg
    from .timecode import frames_to_seconds
    info = probe(src)
    frame = _parse_point(at_, info) if at_ else min(info.nb_frames - 1, round(3600 * float(info.rate.fps)))
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    for n, a in enumerate(info.audio, start=1):
        path = out / f"track{n:02d}.mp3"
        run_ffmpeg(["-ss", fmt_seconds(frames_to_seconds(frame, info.rate)), "-i", info.path, "-t", fmt_seconds(seconds),
                    "-map", f"0:{a.stream_index}", "-vn", "-c:a", "libmp3lame", "-b:a", "128k", str(path)])
        click.echo(f"{path}  ({a.channels}ch)")
    click.echo(f"{len(info.audio)} tracks from {frames_to_tc(info.start_frames + frame, info.rate, info.drop_frame)}")


@main.command("lut-check")
@click.argument("lut_file", type=click.Path(exists=True, dir_okay=False), required=False)
def lut_check(lut_file) -> None:
    """Measure the SDR conversion on HLG test patches; with a LUT, try both range conventions.

    Reports 8-bit BT.709 luma for 25%, 50%, 75% and 100% HLG signal. 75% is
    HLG reference white and should land near 235.
    """
    path = lut_file or (active_lut().path if active_lut() else None)
    results = lutcheck.check(path)
    for r in results:
        click.echo(r.summary)
    rec = lutcheck.recommend(results)
    if rec:
        click.echo(f"recommended --lut-range {rec}")
    elif not path:
        click.echo("no LUT given or found; only the built-in conversion was measured")


@main.command("probe")
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Machine readable output.")
def probe_cmd(src: str, as_json: bool) -> None:
    """Describe a source file."""
    info = probe(src)
    if as_json:
        click.echo(json.dumps(info.to_dict(), indent=1))
        return
    click.echo(f"{info.path}")
    click.echo(f"  {info.size_bytes/1e9:,.1f} GB  {info.duration/3600:.2f} h  {info.nb_frames:,} frames")
    click.echo(f"  {info.codec} {info.profile}  {info.width}x{info.height}  {float(info.rate.fps):.3f} fps  {info.pix_fmt} {info.bit_depth}-bit")
    click.echo(f"  intra-frame: {info.is_intra}   stream copy frame accurate: {info.is_intra}")
    click.echo(f"  color: {info.color_primaries} / {info.color_transfer} / {info.color_space}   HDR: {info.is_hdr} HLG: {info.is_hlg}")
    tc_kind = "drop-frame" if info.drop_frame else "non-drop"
    click.echo(f"  start timecode: {info.start_timecode} ({tc_kind})")
    if info.start_timecode:
        click.echo(f"  end timecode:   {frames_to_tc(info.start_frames + info.nb_frames - 1, info.rate, info.drop_frame)}")
    click.echo(f"  audio: {len(info.audio)} track(s), {info.total_audio_channels} channel(s) total")
    for n, a in enumerate(info.audio, start=1):
        click.echo(f"    {n:2d}: {a.codec} {a.channels}ch {a.sample_rate} Hz {a.bit_depth or ''}")


@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("out_dir", type=click.Path(file_okay=False))
@click.option("--interval", default=2.0, show_default=True, help="Seconds between thumbnails.")
@click.option("--width", default=320, show_default=True)
@click.option("--workers", default=8, show_default=True)
@click.option("--hw", is_flag=True, help="Decode on the GPU (CUDA).")
@click.option("--force", is_flag=True, help="Regenerate thumbnails that already exist.")
def filmstrip(src: str, out_dir: str, interval: float, width: int, workers: int, hw: bool, force: bool) -> None:
    """Extract a thumbnail every few seconds for the whole file. Existing thumbnails are kept unless --force."""
    info = probe(src)
    lut = active_lut()
    click.echo(f"SDR conversion: {Path(lut.path).name} ({lut.range} range)" if lut else "SDR conversion: built-in (no LUT found)")
    bar = _Bar("filmstrip")
    t0 = time.time()
    index = build_filmstrip(info, out_dir, interval, width, workers, on_progress=bar, use_hw=hw, force=force)
    dt = time.time() - t0
    click.echo(f"{index.count} thumbnails in {dt:.0f}s ({index.count/dt:.1f}/s) -> {out_dir}")


@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("out", type=click.Path(dir_okay=False))
@click.option("--height", default=1080, show_default=True)
@click.option("--bitrate", default="8M", show_default=True)
@click.option("--hlg", is_flag=True, help="Keep HDR instead of tone-mapping to SDR.")
@click.option("--hevc", is_flag=True, help="HEVC instead of H.264 (smaller, plays in fewer places).")
@click.option("--audio", default=None, help="Source tracks, 1-based, e.g. 1,2")
@click.option("--cpu", is_flag=True, help="Encode on the CPU instead of NVENC.")
@click.option("--hwdecode", is_flag=True, help="Decode on the GPU (CUDA). Measured slower than CPU decode on this footage.")
@click.option("--jobs", default=1, show_default=True, help="Encode this many segments at once, then join them.")
def proxy(src: str, out: str, height: int, bitrate: str, hlg: bool, hevc: bool, audio: str | None, cpu: bool,
          hwdecode: bool, jobs: int) -> None:
    """Render a browser-friendly MP4 of the whole program."""
    info = probe(src)
    settings = ProxySettings(height=height, bitrate=bitrate, sdr=not hlg, hevc=hevc,
                             audio_tracks=_tracks(audio), prefer_gpu=not cpu, hw_decode=hwdecode, jobs=max(1, jobs))
    lut = active_lut()
    click.echo(f"SDR conversion: {Path(lut.path).name}" if lut else "SDR conversion: built-in")
    if jobs > 1:
        click.echo(f"rendering {jobs} segments at once, then joining")
    bar = _Bar("proxy")
    t0 = time.time()
    path = build_proxy(info, out, settings, on_progress=bar)
    dt = time.time() - t0
    click.echo(f"{path}  {Path(path).stat().st_size/1e9:.2f} GB in {dt/60:.1f} min  ({info.duration/dt:.1f}x realtime)")


@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("out_dir", type=click.Path(file_okay=False))
@click.option("--in", "in_", required=True, help="In point: time of day (19:32:05), timecode, or frame.")
@click.option("--out", "out_", required=True, help="Out point, same forms as --in.")
@click.option("--name", required=True)
@click.option("--format", "fmts", type=click.Choice([f.value for f in OutputFormat]), multiple=True, default=["prores"],
              show_default=True, help="Repeat for several outputs, e.g. --format prores --format mp4")
@click.option("--still/--no-still", default=True, show_default=True, help="Also save a still from the middle of the clip.")
@click.option("--still-at", default=None, help="Frame for the still instead of the middle (same forms as --in).")
@click.option("--head", default=2.0, show_default=True, help="Seconds before in.")
@click.option("--tail", default=2.0, show_default=True, help="Seconds after out.")
@click.option("--audio", default=None, help="Source tracks, 1-based, e.g. 1,2")
@click.option("--prores-profile", type=click.Choice(["proxy", "lt", "standard", "hq"]), default="standard", show_default=True)
@click.option("--mp4-hdr", is_flag=True, help="Keep HLG in the MP4 instead of tone-mapping to SDR.")
@click.option("--overwrite", is_flag=True)
def cut(src, out_dir, in_, out_, name, fmts, still, still_at, head, tail, audio, prores_profile, mp4_hdr, overwrite) -> None:
    """Render one clip in one or more formats, plus a reference still."""
    info = probe(src)
    fps = float(info.rate.fps)
    in_frame, out_frame = _parse_point(in_, info), _parse_point(out_, info)
    fmts = list(dict.fromkeys(fmts))
    for fmt in fmts:
        out_name = name + ("_src" if fmt == "copy" and "prores" in fmts else "")
        spec = ClipSpec(
            in_frame=in_frame, out_frame=out_frame, name=out_name, fmt=OutputFormat(fmt),
            head_frames=round(head * fps), tail_frames=round(tail * fps),
            audio_tracks=_tracks(audio), prores_profile=prores_profile, mp4_sdr=not mp4_hdr,
        )
        bar = _Bar(f"{out_name} [{fmt}]")
        t0 = time.time()
        try:
            path = render_clip(info, spec, out_dir, on_progress=bar, overwrite=overwrite)
        except FileExistsError as e:
            raise click.ClickException(f"exists, use --overwrite: {e}")
        click.echo(f"{path}  {Path(path).stat().st_size/1e6:.1f} MB in {time.time()-t0:.1f}s")
    if still:
        frame = _parse_point(still_at, info) if still_at else (in_frame + out_frame) // 2
        try:
            path = grab_still(info, frame, Path(out_dir) / "stills", name, overwrite=overwrite)
        except FileExistsError as e:
            raise click.ClickException(f"exists, use --overwrite: {e}")
        click.echo(f"{path}  still at {frames_to_tc(info.start_frames + frame, info.rate, info.drop_frame)}")


@main.command("still")
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("out_dir", type=click.Path(file_okay=False))
@click.option("--at", required=True, help="Frame to grab: time of day, timecode, or frame number.")
@click.option("--name", required=True, help="File name without extension.")
@click.option("--format", "fmt", type=click.Choice(sorted(STILL_FORMATS)), default="png", show_default=True)
@click.option("--hdr", is_flag=True, help="16-bit PNG/TIFF with source color left alone instead of SDR tone-map.")
@click.option("--width", default=None, type=int, help="Scale down to this width; default keeps full size.")
@click.option("--overwrite", is_flag=True)
def still_cmd(src, out_dir, at, name, fmt, hdr, width, overwrite) -> None:
    """Save one frame as an image with a name of your choosing."""
    info = probe(src)
    frame = _parse_point(at, info)
    try:
        path = grab_still(info, frame, out_dir, name, fmt, hdr, width, overwrite)
    except FileExistsError as e:
        raise click.ClickException(f"exists, use --overwrite: {e}")
    click.echo(f"{path}  {frames_to_tc(info.start_frames + frame, info.rate, info.drop_frame)}")


@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("log_csv", type=click.Path(exists=True, dir_okay=False))
@click.option("--offset", default=-10800.0, show_default=True,
              help="Seconds added to log times to reach file timecode: ET -10800, CT -7200, MT -3600, PT 0.")
@click.option("--json", "as_json", is_flag=True)
def markers(src: str, log_csv: str, offset: float, as_json: bool) -> None:
    """Map a log CSV (Time, Type, Shell, Description) onto the file."""
    info = probe(src)
    res = import_log_csv(Path(log_csv), info, offset)
    if as_json:
        click.echo(json.dumps({"markers": [m.to_dict() for m in res.markers], "skipped": res.skipped}, indent=1))
        return
    for m in res.markers:
        click.echo(f"{m.tc}  frame {m.frame:>9}  {m.section:<9} {m.shell:<18} {m.camera:<8} {m.description}")
    for m in res.untimed:
        click.echo(f"untimed   {m.section:<9} {m.shell:<18} {m.camera:<8} {m.description}")
    for s in res.skipped:
        click.echo(f"skipped: {s}", err=True)
    if res.all_outside:
        click.echo("every timed row fell outside the recording: check --offset (ET -10800, CT -7200, MT -3600, PT 0)", err=True)
    click.echo(f"{len(res.markers)} markers, {len(res.untimed)} untimed, {len(res.skipped)} skipped")


@main.command()
@click.argument("src", type=click.Path(exists=True, dir_okay=False))
@click.argument("project_path", type=click.Path(dir_okay=False))
@click.option("--date", default="", help="YYMMDD")
@click.option("--week", default="", help="e.g. WK08")
@click.option("--away", default="")
@click.option("--home", default="")
def init(src: str, project_path: str, date: str, week: str, away: str, home: str) -> None:
    """Create a project file for a game."""
    info = probe(src)
    proj = Project(source=str(Path(src).resolve()), info=info, game=GameInfo(date, week, away, home))
    proj.save(project_path)
    click.echo(f"wrote {project_path}")
    click.echo(f"example name: {build_name(proj.game, 1, 'LowerThird', 'Herbstreit pick')}")


@main.command("render")
@click.argument("project_path", type=click.Path(exists=True, dir_okay=False))
@click.argument("out_dir", type=click.Path(file_okay=False))
@click.option("--overwrite", is_flag=True)
def render_project(project_path: str, out_dir: str, overwrite: bool) -> None:
    """Render every clip in a project."""
    proj = Project.load(project_path)
    failures = 0
    for clip in proj.clips:
        bar = _Bar(clip.name)
        try:
            done = proj.render_clip_outputs(clip, out_dir, on_progress=bar, overwrite=overwrite)
            click.echo(f"{clip.name}: " + ", ".join(done))
        except FileExistsError as e:
            click.echo(f"skip (exists): {e}")
        except FfmpegError as e:
            failures += 1
            click.echo(f"FAILED {clip.name}: {e}", err=True)
    for still in proj.stills:
        try:
            click.echo(str(proj.render_still(still, out_dir, overwrite=overwrite)))
        except FileExistsError as e:
            click.echo(f"skip (exists): {e}")
        except FfmpegError as e:
            failures += 1
            click.echo(f"FAILED still {still.name}: {e}", err=True)
    proj.save(project_path)
    click.echo(f"done, {failures} failure(s)")


if __name__ == "__main__":
    main()
