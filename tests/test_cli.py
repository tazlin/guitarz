"""Behavioral spec for the ``guitarz`` command-line entry point.

The CLI is a thin argparse wrapper around ``create_app`` + ``uvicorn.run``;
these tests verify argument validation and that user-supplied options actually
reach the server, with ``uvicorn.run`` monkeypatched so no socket is opened.
"""

import pytest

from guitarz import __main__ as cli


def test_nonexistent_root_exits_with_usage_error(monkeypatch, tmp_path):
    """``--root`` pointing at a missing directory aborts before serving.

    argparse's ``parser.error`` exits with status 2; getting that instead of a
    traceback is the difference between a usable CLI message and a crash.
    """
    monkeypatch.setattr(
        "sys.argv", ["guitarz", "--root", str(tmp_path / "nope")]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_options_are_passed_through_to_uvicorn(monkeypatch, tmp_path):
    """``--host`` and ``--port`` reach ``uvicorn.run`` unchanged.

    ``uvicorn.run`` is stubbed to capture its arguments, proving the CLI wires
    user input into the server rather than serving hardcoded defaults.
    """
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["guitarz", "--root", str(tmp_path), "--host", "0.0.0.0", "--port", "9001"],
    )
    cli.main()
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9001
    assert captured["app"] is not None
