"""Запуск: python -m mediaviewer [папка] [--port 8765] [--no-browser]"""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from . import app as app_module


def main() -> None:
    ap = argparse.ArgumentParser(description="Media Viewer — отбор фото и видео")
    ap.add_argument("folder", nargs="?", help="папка, которую открыть сразу")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if args.folder:
        app_module.open_library(args.folder)
    else:
        recent = app_module._recent()
        if recent:
            try:
                app_module.open_library(recent[0])
            except Exception:  # noqa: BLE001
                pass

    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Media Viewer: {url}")
    uvicorn.run(app_module.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
