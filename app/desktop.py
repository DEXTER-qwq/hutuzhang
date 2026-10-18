"""Desktop launcher: local server + native webview window."""

from __future__ import annotations

import os
import socket
import threading
import time

import uvicorn

from .main import app


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run() -> None:
    import webview  # imported late so --help / tests don't need a display

    port = int(os.environ.get("HUTUZHANG_PORT") or os.environ.get("JINGZHI_PORT") or _free_port())
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)

    webview.create_window(
        "糊涂账",
        f"http://127.0.0.1:{port}",
        width=1280,
        height=860,
        min_size=(1024, 700),
        background_color="#F6F5F1",
        text_select=True,
    )
    try:
        webview.start()
    finally:
        server.should_exit = True


if __name__ == "__main__":
    run()
