import subprocess
import threading
import time

import pytest

from reelquick.ffmpeg import FfmpegError, run_ffmpeg


def _ffmpeg_count() -> int:
    out = subprocess.run(["pgrep", "-c", "-x", "ffmpeg"], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def test_exception_in_progress_callback_kills_ffmpeg(synthetic_mxf):
    before = _ffmpeg_count()

    def boom(frac, fields):
        raise KeyboardInterrupt            # what Ctrl+C looks like from inside the loop

    with pytest.raises(KeyboardInterrupt):
        run_ffmpeg(["-re", "-i", str(synthetic_mxf), "-f", "null", "-"], duration=6, on_progress=boom)
    time.sleep(0.3)
    assert _ffmpeg_count() == before


def test_cancel_event_stops_ffmpeg(synthetic_mxf):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    t0 = time.time()
    with pytest.raises(FfmpegError, match="cancelled"):
        run_ffmpeg(["-re", "-i", str(synthetic_mxf), "-f", "null", "-"], duration=6, on_progress=lambda f, d: None, cancel=cancel)
    assert time.time() - t0 < 3
