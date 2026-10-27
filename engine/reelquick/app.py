"""Start the local API and open the app window."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .server import create_app
from .workspace import Session


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int, session: Session) -> threading.Thread:
    app = create_app(session)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    return t


def run_app(port: int | None = None, browser: bool = False, work_root: Path | None = None,
            open_path: str | None = None) -> None:
    port = port or _free_port()
    session = Session(work_root)
    if open_path:
        session.open(open_path)
    serve(port, session)
    url = f"http://127.0.0.1:{port}/"
    if not browser:
        try:
            import webview  # pywebview: WebView2 on Windows, WKWebView on macOS
        except ImportError:
            browser = True
    if browser:
        print(f"Reel Quick is running at {url}  (Ctrl+C to quit)")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return
    window = webview.create_window("Reel Quick", url, width=1600, height=1000, background_color="#1E1E1E")

    class Api:
        def pick_file(self):
            r = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                          file_types=("Video (*.mxf;*.mov;*.mp4;*.mkv)", "All files (*.*)"))
            return r[0] if r else None

        def pick_folder(self):
            r = window.create_file_dialog(webview.FOLDER_DIALOG)
            return r[0] if r else None

        def pick_csv(self):
            r = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                          file_types=("CSV (*.csv;*.txt)", "All files (*.*)"))
            if not r:
                return None
            return Path(r[0]).read_text(encoding="utf-8-sig", errors="replace")

    window.expose(Api().pick_file, Api().pick_folder, Api().pick_csv)
    webview.start()
