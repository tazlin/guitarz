"""FastAPI backend for guitarz.

Serves two things: a small JSON API that lets the frontend browse a
configurable "music root" directory for Guitar Pro files, and the static web
app itself. The score never touches Python — files are shipped as raw bytes to
the browser, where alphaTab parses and renders them — so this module's whole
job is careful filesystem exposure: only allowed extensions, only inside the
root, hidden entries excluded.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

#: File extensions exposed by the API — every generation alphaTab can parse.
#: (.gpx/GP6 is deliberately absent: untested and none in the collection.)
#: See tests/test_server.py for the filtering contract.
EXTENSIONS = {".gp", ".gp3", ".gp4", ".gp5"}

#: Location of the frontend bundle. Resolved relative to this file so the app
#: works from an editable install regardless of the process's working
#: directory.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(root: Path) -> FastAPI:
    """Build the FastAPI application serving one music-root directory.

    An app-factory (rather than a module-level app) so tests can spin up
    isolated instances against throwaway directories, and so the root is fixed
    at startup instead of being ambient global state. The root is resolved
    once here; all request paths are validated against this canonical form.

    Args:
        root: directory whose Guitar Pro files the app may expose. Must exist.

    Returns:
        A configured FastAPI instance with ``/api/browse``, ``/api/file`` and
        the static frontend mounted at ``/``.
    """
    root = root.resolve()
    app = FastAPI(title="guitarz")

    def resolve_safe(rel: str) -> Path:
        """Resolve a client-supplied relative path, refusing escapes.

        This is the security chokepoint for both endpoints. ``resolve()``
        canonicalizes ``..`` segments *and* symlinks, so containment is checked
        against the real filesystem location, not the textual path. Absolute
        inputs are inherently rejected: joining them onto ``root`` replaces the
        root, and the result then fails the containment check.

        Args:
            rel: root-relative path from the query string; attacker-controlled.

        Returns:
            The canonical absolute path, guaranteed to be ``root`` itself or
            inside it.

        Raises:
            HTTPException: 400 for paths the OS cannot even stat (embedded
                null bytes, oversized names); 403 for any path that resolves
                outside the root.
        """
        try:
            target = (root / rel).resolve()
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail="malformed path") from exc
        if target != root and not target.is_relative_to(root):
            raise HTTPException(status_code=403, detail="path outside music root")
        return target

    @app.get("/api/browse")
    def browse(path: str = Query("")) -> dict:
        """List one directory level of the music root.

        Returns a single level (not a recursive tree) so huge collections stay
        cheap to browse; the frontend descends folder by folder. Entries are
        sorted case-insensitively because the UI renders them in API order.
        Hidden entries (leading dot) and files with disallowed extensions are
        omitted entirely — as far as clients can tell, they don't exist.

        Args:
            path: root-relative directory to list; empty string means the root.

        Returns:
            ``{"path": <relative dir>, "dirs": [{name, path}], "files":
            [{name, path, size}]}`` where every ``path`` is root-relative
            POSIX, ready to feed back into the API.

        Raises:
            HTTPException: 400/403 from ``resolve_safe``; 404 if the target is
                not an existing directory.
        """
        target = resolve_safe(path)
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="not a directory")
        dirs = []
        files = []
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": rel})
            elif entry.suffix.lower() in EXTENSIONS:
                files.append({"name": entry.name, "path": rel, "size": entry.stat().st_size})
        rel_path = "" if target == root else target.relative_to(root).as_posix()
        return {"path": rel_path, "dirs": dirs, "files": files}

    @app.get("/api/file")
    def file(path: str = Query(...)) -> FileResponse:
        """Serve one Guitar Pro file as raw bytes.

        Bytes must reach the browser untransformed (alphaTab parses them
        directly), hence ``application/octet-stream`` and ``FileResponse``.
        The extension allowlist is enforced here too — not just in ``browse`` —
        so guessing the path of a non-tab file that happens to live under the
        root still yields nothing.

        Args:
            path: root-relative path of the file to download.

        Returns:
            A streaming response with the file's exact bytes.

        Raises:
            HTTPException: 400/403 from ``resolve_safe``; 404 if the path is
                not an existing file with an allowed extension.
        """
        target = resolve_safe(path)
        if not target.is_file() or target.suffix.lower() not in EXTENSIONS:
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target, media_type="application/octet-stream", filename=target.name)

    @app.middleware("http")
    async def demand_revalidation(request, call_next):
        """Stamp ``Cache-Control: no-cache`` on every response.

        Without an explicit cache policy, browsers apply heuristic caching to
        the frontend JS/CSS and can keep running stale code after the app is
        updated — observed in the field as a freshly-rendered button wired to
        nothing. ``no-cache`` does not disable caching: it forces a
        conditional request each time, and the ETag/Last-Modified that
        Starlette's StaticFiles already emits turns unchanged assets into
        cheap 304s. Net effect: a server restart (or just editing a file) is
        sufficient to deliver new frontend code to every open browser.

        Args:
            request: the incoming request (unused; middleware signature).
            call_next: continuation producing the downstream response.

        Returns:
            The downstream response with the cache directive applied.
        """
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response

    # Mounted last so /api/* routes win; html=True serves index.html at "/".
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
