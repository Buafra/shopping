#!/usr/bin/env python3
"""Cross-platform launcher that picks a port which will actually bind.

Windows reserves large TCP ranges for Hyper-V/WSL and then refuses to bind
them with WinError 10013 even though nothing is listening there, so the
conventional default of 8000 fails on many machines for no visible reason.
This probes before binding and moves on to the next candidate.
"""

from __future__ import annotations

import argparse
import errno
import socket
import sys

DEFAULT_PORTS = [8000, 8080, 8600, 8765, 9000, 9500, 0]  # 0 = let the OS choose


def port_is_bindable(port: int, host: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as exc:
            # 10013 = Windows access denied (reserved range)
            # 10048/EADDRINUSE = already in use
            if exc.errno in (errno.EACCES, errno.EADDRINUSE, 10013, 10048):
                return False
            raise
        return True


def pick_port(host: str, preferred: int | None) -> int:
    candidates = ([preferred] if preferred else []) + [
        p for p in DEFAULT_PORTS if p != preferred
    ]
    for port in candidates:
        if port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, 0))
                return s.getsockname()[1]
        if port_is_bindable(port, host):
            return port
    raise SystemExit("could not find a bindable port")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the Shopping Scout web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None,
                        help="preferred port; another is chosen if it will not bind")
    parser.add_argument("--reload", action="store_true", help="auto-reload on edits")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed — run: python -m pip install -r requirements.txt")
        return 1

    if args.port and not port_is_bindable(args.port, args.host):
        print(f"port {args.port} will not bind (in use, or reserved by Windows) "
              f"— choosing another")

    port = pick_port(args.host, args.port)

    print(f"\n  Shopping Scout -> http://{args.host}:{port}\n")
    uvicorn.run("app.main:app", host=args.host, port=port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
