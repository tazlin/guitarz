"""Behavioral spec for guitarz.gpinfo — Guitar Pro file metadata extraction.

Pins down the contract of ``read_gp_info`` across the three container
generations of the format family:

* GP3/GP4/GP5 — binary files opening with the length-prefixed marker
  ``FICHIER GUITAR PRO vX.YZ`` (fixtures generated with PyGuitarPro, so the
  suite is self-sufficient),
* GPX (GP6) — a ``BCFZ``/``BCFS`` compressed container, recognized and
  labeled but not deep-parsed,
* ``.gp`` (GP7/8) — a zip holding ``Content/score.gpif`` XML plus a
  ``VERSION`` file (fixture built as a minimal synthetic zip).

Detection must key off file *content* (magic bytes), never the extension —
renamed files are common in shared tab collections.
"""

import json
import zipfile
from pathlib import Path

import guitarpro
import pytest

from guitarz import gpinfo


def make_legacy(tmp_path: Path, version: tuple, suffix: str) -> Path:
    """Write a minimal legacy-format (gp3/gp4/gp5) fixture file.

    PyGuitarPro fills in a default single track ("Track 1"), which the
    assertions rely on. Metadata is fixed so tests can compare exactly.

    Args:
        tmp_path: directory to write into.
        version: PyGuitarPro version tuple, e.g. ``(4, 0, 6)``.
        suffix: file extension including dot, e.g. ``".gp4"``.

    Returns:
        Path of the written fixture.
    """
    song = guitarpro.models.Song()
    song.title = "Fixture Song"
    song.artist = "Fixture Artist"
    song.album = "Fixture Album"
    path = tmp_path / f"fixture{suffix}"
    guitarpro.write(song, str(path), version=version)
    return path


def make_gp7(tmp_path: Path) -> Path:
    """Write a minimal synthetic GP7-style ``.gp`` zip fixture.

    Contains exactly the two members ``read_gp_info`` consumes: the
    ``VERSION`` stamp and a bare-bones ``Content/score.gpif`` with score
    metadata and two named tracks.

    Args:
        tmp_path: directory to write into.

    Returns:
        Path of the written fixture.
    """
    gpif = """<?xml version="1.0" encoding="UTF-8"?>
<GPIF>
  <Score>
    <Title><![CDATA[Zip Song]]></Title>
    <Artist><![CDATA[Zip Artist]]></Artist>
    <Album><![CDATA[Zip Album]]></Album>
  </Score>
  <Tracks>
    <Track id="0"><Name><![CDATA[Lead Guitar]]></Name></Track>
    <Track id="1"><Name><![CDATA[Bass]]></Name></Track>
  </Tracks>
</GPIF>"""
    path = tmp_path / "fixture.gp"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("VERSION", "7.0")
        z.writestr("Content/score.gpif", gpif)
    return path


class TestLegacyFormats:
    """Binary GP3/GP4/GP5 header + metadata extraction."""

    @pytest.mark.parametrize(
        ("version", "suffix", "expected"),
        [
            ((3, 0, 0), ".gp3", "Guitar Pro 3.00"),
            ((4, 0, 6), ".gp4", "Guitar Pro 4.06"),
            ((5, 0, 0), ".gp5", "Guitar Pro 5.00"),
            ((5, 1, 0), ".gp5", "Guitar Pro 5.10"),
        ],
    )
    def test_version_detected_from_binary_marker(self, tmp_path, version, suffix, expected):
        """Each legacy generation reports its precise on-disk version string."""
        info = gpinfo.read_gp_info(make_legacy(tmp_path, version, suffix))
        assert info.version == expected

    def test_metadata_and_tracks(self, tmp_path):
        """Title, artist, album, and track names come from the file content."""
        info = gpinfo.read_gp_info(make_legacy(tmp_path, (5, 1, 0), ".gp5"))
        assert info.title == "Fixture Song"
        assert info.artist == "Fixture Artist"
        assert info.album == "Fixture Album"
        assert info.tracks == ["Track 1"]

    def test_extension_is_ignored(self, tmp_path):
        """A gp5 file renamed to .gp3 is still identified as 5.10 by content."""
        path = make_legacy(tmp_path, (5, 1, 0), ".gp5")
        renamed = path.with_suffix(".gp3")
        path.rename(renamed)
        assert gpinfo.read_gp_info(renamed).version == "Guitar Pro 5.10"


class TestGp7Format:
    """Zip-container ``.gp`` (GP7/8) extraction."""

    def test_metadata_from_gpif_and_version_file(self, tmp_path):
        """Score metadata comes from score.gpif; version from the VERSION stamp."""
        info = gpinfo.read_gp_info(make_gp7(tmp_path))
        assert info.version == "Guitar Pro 7.0"
        assert info.title == "Zip Song"
        assert info.artist == "Zip Artist"
        assert info.album == "Zip Album"
        assert info.tracks == ["Lead Guitar", "Bass"]


class TestGpxAndErrors:
    """GP6 recognition and rejection of non-GP files."""

    def test_gpx_recognized_but_not_parsed(self, tmp_path):
        """A BCFZ container is labeled Guitar Pro 6 with empty metadata.

        Deep-parsing GP6's compressed filesystem is out of scope; the tool
        must still identify the generation rather than erroring out.
        """
        path = tmp_path / "song.gpx"
        path.write_bytes(b"BCFZ" + b"\x00" * 64)
        info = gpinfo.read_gp_info(path)
        assert info.version == "Guitar Pro 6"
        assert info.title is None
        assert info.tracks == []

    def test_non_gp_file_raises(self, tmp_path):
        """Arbitrary bytes are rejected with ValueError, not misreported."""
        path = tmp_path / "notes.gp5"
        path.write_bytes(b"just some text, not a tab")
        with pytest.raises(ValueError):
            gpinfo.read_gp_info(path)


class TestCli:
    """The ``gpinfo`` command-line front end."""

    def test_json_output_for_directory(self, tmp_path, capsys, monkeypatch):
        """--json emits one record per GP file found under a directory."""
        make_legacy(tmp_path, (5, 1, 0), ".gp5")
        make_gp7(tmp_path)
        monkeypatch.setattr("sys.argv", ["gpinfo", "--json", str(tmp_path)])
        gpinfo.main()
        records = json.loads(capsys.readouterr().out)
        assert len(records) == 2
        versions = {r["version"] for r in records}
        assert versions == {"Guitar Pro 5.10", "Guitar Pro 7.0"}

    def test_table_output_lists_each_file(self, tmp_path, capsys, monkeypatch):
        """Default output is a human-readable line per file with key fields."""
        make_gp7(tmp_path)
        monkeypatch.setattr("sys.argv", ["gpinfo", str(tmp_path)])
        gpinfo.main()
        out = capsys.readouterr().out
        assert "Zip Artist" in out
        assert "Guitar Pro 7.0" in out

    def test_unreadable_file_reported_not_fatal(self, tmp_path, capsys, monkeypatch):
        """A corrupt file yields an error line; remaining files still print."""
        (tmp_path / "bad.gp5").write_bytes(b"garbage")
        make_gp7(tmp_path)
        monkeypatch.setattr("sys.argv", ["gpinfo", str(tmp_path)])
        gpinfo.main()
        captured = capsys.readouterr()  # one read: a second call would see empty streams
        combined = captured.out + captured.err
        assert "Zip Song" in combined or "Guitar Pro 7.0" in combined
        assert "bad.gp5" in combined


class TestFixExt:
    """The --fix-ext mode: reconcile extensions with actual content version.

    The rule is "fix mismatches only": a file whose extension already tells
    the truth (including GP7/8 zips keeping the standard ``.gp``) is left
    alone; a file whose extension lies about its content (a GP5 binary named
    ``.gp3``) is renamed to its true extension. Dry-run is the default so a
    collection is never modified sight-unseen; ``--apply`` executes.
    """

    def test_dry_run_plans_but_does_not_touch_disk(self, tmp_path, capsys, monkeypatch):
        """Without --apply, mismatches are reported and files stay put."""
        path = make_legacy(tmp_path, (5, 1, 0), ".gp5")
        lying = path.with_name("lying.gp3")
        path.rename(lying)
        monkeypatch.setattr("sys.argv", ["gpinfo", "--fix-ext", str(tmp_path)])
        gpinfo.main()
        out = capsys.readouterr().out
        assert "lying.gp3" in out and "lying.gp5" in out
        assert lying.exists(), "dry-run must not rename anything"

    def test_apply_renames_mismatched_file(self, tmp_path, capsys, monkeypatch):
        """--apply renames the file to its content-true extension."""
        path = make_legacy(tmp_path, (5, 1, 0), ".gp5")
        lying = path.with_name("lying.gp3")
        path.rename(lying)
        monkeypatch.setattr("sys.argv", ["gpinfo", "--fix-ext", "--apply", str(tmp_path)])
        gpinfo.main()
        assert not lying.exists()
        assert (tmp_path / "lying.gp5").exists()

    def test_correct_extensions_are_left_alone(self, tmp_path, capsys, monkeypatch):
        """Truthful files — including GP7 zips as .gp — are never renamed."""
        keep_gp5 = make_legacy(tmp_path, (5, 1, 0), ".gp5")
        keep_gp7 = make_gp7(tmp_path)
        monkeypatch.setattr("sys.argv", ["gpinfo", "--fix-ext", "--apply", str(tmp_path)])
        gpinfo.main()
        assert keep_gp5.exists() and keep_gp7.exists()
        out = capsys.readouterr().out
        assert "0 to rename" in out or "nothing to rename" in out

    def test_collision_is_skipped_with_warning(self, tmp_path, capsys, monkeypatch):
        """If the true-name target already exists, the file is left untouched."""
        path = make_legacy(tmp_path, (5, 1, 0), ".gp5")
        lying = path.with_name("song.gp3")
        path.rename(lying)
        make_legacy(tmp_path, (5, 0, 0), ".gp5").rename(tmp_path / "song.gp5")
        monkeypatch.setattr("sys.argv", ["gpinfo", "--fix-ext", "--apply", str(tmp_path)])
        gpinfo.main()
        captured = capsys.readouterr()
        assert lying.exists(), "collision must leave the source untouched"
        assert "song.gp5" in captured.out + captured.err  # skip is reported
