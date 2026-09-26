"""Guitar Pro file identification and metadata extraction (all generations).

The Guitar Pro family spans three unrelated container formats:

* **GP3/GP4/GP5** — a bespoke binary format opening with a length-prefixed
  marker string ``FICHIER GUITAR PRO vX.YZ`` that encodes the exact version.
  Parsed here via PyGuitarPro, which understands all three generations.
* **GPX (GP6)** — a proprietary compressed container starting with ``BCFZ``
  (or uncompressed ``BCFS``). Recognized and labeled, but its payload is not
  decompressed — deep GP6 support is deliberately out of scope.
* **``.gp`` (GP7/8)** — an ordinary zip archive holding ``Content/score.gpif``
  (XML score) and a ``VERSION`` stamp file.

Detection keys off leading magic bytes, never the file extension: tab files
circulating in collections are frequently renamed, and misreporting a file's
format is worse than refusing it.

Doubles as the ``gpinfo`` console tool: point it at files or folders to get a
table (or ``--json``) of version, artist, title, and track names.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import guitarpro

#: Extensions scanned when the CLI is handed a directory. Content magic still
#: decides how each file is read; this only limits the directory walk.
SCAN_EXTENSIONS = {".gp", ".gp3", ".gp4", ".gp5", ".gpx"}

#: Marker opening every GP3-GP5 binary file (after a 1-byte length prefix).
_LEGACY_MAGIC = b"FICHIER GUITAR PRO"


@dataclasses.dataclass
class GPInfo:
    """Extracted identity and metadata of one Guitar Pro file.

    Fields that a format cannot provide (e.g. anything beyond the version for
    GPX) are ``None``/empty rather than guessed, so callers can distinguish
    "absent from the file" from "not extracted".

    Attributes:
        path: the file the information came from.
        kind: container generation — ``"gp3/4/5"``, ``"gpx"``, or ``"gp7+"``.
        version: human-readable version, e.g. ``"Guitar Pro 5.10"``.
        title: song title, or None.
        artist: performing artist, or None.
        album: album name, or None.
        tracks: instrument track names, in file order (empty if unknown).
    """

    path: Path
    kind: str
    version: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    tracks: list[str] = dataclasses.field(default_factory=list)


def read_gp_info(path: Path | str) -> GPInfo:
    """Identify a Guitar Pro file by content and extract its metadata.

    Dispatches on the leading bytes: ``PK\\x03\\x04`` (zip → GP7/8),
    ``BCFZ``/``BCFS`` (GP6 container), or a length-prefixed
    ``FICHIER GUITAR PRO`` marker (GP3-5 binary). The extension plays no
    part, so renamed files are still classified correctly.

    Args:
        path: file to inspect.

    Returns:
        A populated :class:`GPInfo`.

    Raises:
        ValueError: if the content matches no known Guitar Pro container.
        OSError: if the file cannot be read at all.
    """
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(32)
    if head[:4] == b"PK\x03\x04":
        return _read_gp7(path)
    if head[:4] in (b"BCFZ", b"BCFS"):
        return GPInfo(path=path, kind="gpx", version="Guitar Pro 6")
    if len(head) > 1 and _LEGACY_MAGIC in head:
        return _read_legacy(path)
    raise ValueError(f"{path.name}: not a recognizable Guitar Pro file")


def _read_legacy(path: Path) -> GPInfo:
    """Extract metadata from a binary GP3/GP4/GP5 file via PyGuitarPro.

    A full parse is heavier than strictly needed for header fields, but it is
    the only reliable way to reach the track names (they live deep in the
    file, after the measure headers) and it validates the file as a side
    effect. Tab files are small, so the cost is negligible.

    Args:
        path: file whose leading marker already matched the legacy format.

    Returns:
        A populated :class:`GPInfo` with the exact ``vX.YZ`` version.

    Raises:
        ValueError: if PyGuitarPro cannot parse the file (truncated/corrupt).
    """
    try:
        song = guitarpro.parse(str(path))
    except Exception as exc:  # pyguitarpro raises assorted exception types
        raise ValueError(f"{path.name}: unparseable legacy Guitar Pro file: {exc}") from exc
    major, minor, patch = song.versionTuple
    return GPInfo(
        path=path,
        kind="gp3/4/5",
        version=f"Guitar Pro {major}.{minor}{patch}",
        title=song.title or None,
        artist=song.artist or None,
        album=song.album or None,
        tracks=[t.name for t in song.tracks],
    )


def _read_gp7(path: Path) -> GPInfo:
    """Extract metadata from a GP7/8 ``.gp`` zip container.

    Reads only the two members needed: the ``VERSION`` stamp (e.g. ``7.0``)
    and ``Content/score.gpif``, whose ``<Score>`` block carries the song
    metadata and whose ``<Tracks>`` block names the instruments. Element
    lookups are defensive — Guitar Pro versions vary in which optional
    elements they write.

    Args:
        path: file whose zip magic already matched.

    Returns:
        A populated :class:`GPInfo`.

    Raises:
        ValueError: if the zip lacks a parseable ``score.gpif``.
    """

    def text(root: ET.Element, xpath: str) -> str | None:
        el = root.find(xpath)
        value = (el.text or "").strip() if el is not None else ""
        return value or None

    try:
        with zipfile.ZipFile(path) as z:
            try:
                version = z.read("VERSION").decode("utf-8", "replace").strip()
            except KeyError:
                version = "7+"
            root = ET.fromstring(z.read("Content/score.gpif"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"{path.name}: not a valid GP7+ container: {exc}") from exc
    return GPInfo(
        path=path,
        kind="gp7+",
        version=f"Guitar Pro {version}",
        title=text(root, "./Score/Title"),
        artist=text(root, "./Score/Artist"),
        album=text(root, "./Score/Album"),
        tracks=[
            (t.findtext("Name") or "").strip()
            for t in root.findall("./Tracks/Track")
        ],
    )


def expected_extension(info: GPInfo) -> str:
    """Derive the extension a file *should* carry given its actual content.

    The rule is deliberately conservative ("fix mismatches only"): GP7/8
    files keep the standard ``.gp`` that Guitar Pro itself writes — renaming
    them to ``.gp7`` would break the desktop app's file association — and
    GPX stays ``.gpx``. Only the legacy binary generations encode a major
    version worth surfacing (``.gp3``/``.gp4``/``.gp5``).

    Args:
        info: metadata previously extracted by :func:`read_gp_info`.

    Returns:
        The content-true extension, lowercase, including the dot.
    """
    if info.kind == "gp3/4/5":
        major = info.version.split()[-1].split(".")[0]
        return f".gp{major}"
    if info.kind == "gpx":
        return ".gpx"
    return ".gp"


def _fix_extensions(files: list[Path], apply: bool) -> None:
    """Report (and optionally perform) content-true extension renames.

    Dry-run is the default posture: a collection is user data, and a bulk
    rename should never happen sight-unseen. In apply mode a rename whose
    target already exists is skipped with a diagnostic — silently
    overwriting a different song would be data loss.

    Args:
        files: candidate files (already collected/deduplicated).
        apply: False → print the plan only; True → execute renames.
    """
    planned = 0
    correct = 0
    for f in files:
        try:
            info = read_gp_info(f)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            continue
        target = f.with_suffix(expected_extension(info))
        if target.name == f.name:
            correct += 1
            continue
        planned += 1
        if not apply:
            print(f"would rename: {f}  ->  {target.name}   ({info.version})")
        elif target.exists():
            print(f"skip (target exists): {f}  ->  {target.name}", file=sys.stderr)
        else:
            f.rename(target)
            print(f"renamed: {f}  ->  {target.name}   ({info.version})")
    mode = "" if apply else " (dry run — pass --apply to execute)"
    print(f"{planned} to rename, {correct} already correct{mode}")


def _collect(paths: list[Path]) -> list[Path]:
    """Expand the CLI's path arguments into a sorted list of candidate files.

    Directories are walked recursively for known Guitar Pro extensions;
    explicit file arguments are taken as-is (content detection will reject
    non-GP files later with a per-file error rather than skipping silently).

    Args:
        paths: files and/or directories from the command line.

    Returns:
        De-duplicated candidate files, sorted for stable output.
    """
    files: set[Path] = set()
    for p in paths:
        if p.is_dir():
            files.update(
                f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SCAN_EXTENSIONS
            )
        else:
            files.add(p)
    return sorted(files)


def main() -> None:
    """CLI entry point: report version/artist/title/tracks for GP files.

    Prints one aligned line per readable file (or a JSON array with
    ``--json``). Unreadable or non-GP files produce a diagnostic line on
    stderr but never abort the run — a single corrupt download should not
    hide the rest of a collection.
    """
    parser = argparse.ArgumentParser(
        prog="gpinfo",
        description="Show Guitar Pro file version, artist, title, and tracks.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="GP files and/or folders to scan")
    parser.add_argument("--json", action="store_true", help="emit a JSON array instead of a table")
    parser.add_argument(
        "--fix-ext",
        action="store_true",
        help="rename files whose extension mismatches their content version (dry run by default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --fix-ext: actually perform the renames instead of printing the plan",
    )
    args = parser.parse_args()

    if args.fix_ext:
        _fix_extensions(_collect(args.paths), apply=args.apply)
        return

    infos: list[GPInfo] = []
    for f in _collect(args.paths):
        try:
            infos.append(read_gp_info(f))
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                [
                    {**dataclasses.asdict(i), "path": str(i.path)}
                    for i in infos
                ],
                indent=2,
            )
        )
        return

    for i in infos:
        tracks = f"{len(i.tracks)} track{'s' if len(i.tracks) != 1 else ''}"
        print(
            f"{i.version:<18} {(i.artist or '-'):<24.24} "
            f"{(i.title or '-'):<32.32} {tracks:>9}  {i.path}"
        )


if __name__ == "__main__":
    main()
