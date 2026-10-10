"""Command-line entry point for guitarz.

Kept deliberately thin: parse arguments, validate the one precondition the
server can't recover from (a missing root directory), build the app via the
factory, and hand off to uvicorn. All real behavior lives in
:mod:`guitarz.server` where it is unit-testable without opening sockets.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .server import create_app


def default_root() -> Path:
    """Return the music root used when ``--root`` is not given.

    The Guitar Pro collection is personal creative data, so it lives outside
    the source tree (mirroring xil-pipeline's ``$XIL_PROJECTROOT`` split):
    ``$GUITARZ_ROOT`` if set, else ``~/gps``. ``~`` is expanded in both cases
    so the env var can be written either way in ``~/.bashrc``. Resolved at
    call time, not import time, so tests and shells can change the env.

    Returns:
        The music root path (not checked for existence here; ``main`` does).
    """
    return Path(os.environ.get("GUITARZ_ROOT", "~/gps")).expanduser()


def main() -> None:
    """Parse CLI arguments and run the guitarz web server.

    Defaults are chosen for the local-practice use case: root is
    :func:`default_root` (``$GUITARZ_ROOT`` or ``~/gps``) and the bind
    address is loopback-only, since the app exposes part of the filesystem and
    should not be reachable from the network unless explicitly requested via
    ``--host``.

    Command-line options:
        ``--root DIR``: music directory to browse (default: ``default_root()``).
        ``--host HOST``: bind address (default: 127.0.0.1).
        ``--port N``: TCP port (default: 8008).

    Raises:
        SystemExit: exit code 2 via ``parser.error`` when ``--root`` is not an
            existing directory — a usage error, not a traceback.
    """
    parser = argparse.ArgumentParser(
        prog="guitarz",
        description="Conductor-view Guitar Pro player (serves a web UI).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="music root directory to browse (default: $GUITARZ_ROOT or ~/gps)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=8008, help="port (default: 8008)")
    args = parser.parse_args()
    if args.root is None:
        args.root = default_root()

    if not args.root.is_dir():
        parser.error(f"--root {args.root} is not a directory")

    app = create_app(args.root)
    print(f"guitarz: serving {args.root.resolve()} at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
