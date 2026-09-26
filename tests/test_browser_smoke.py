"""End-to-end smoke tests: drive the real app in headless Chrome.

This is the TDD hook for the frontend. Each test boots the actual FastAPI app
on a free port, opens it in headless Chrome via the Chrome DevTools Protocol,
deep-links the bundled Joe Walsh GP7 sample, and asserts real behavior — no
mocked DOM, no stubbed player.

Skipped automatically when Chrome or the vendored alphaTab assets are absent,
so the fast unit loop (``pytest -m "not browser"``) never depends on them.
"""

import asyncio
import contextlib
import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from guitarz.server import create_app

# Defined here rather than imported from conftest: conftest is pytest-loaded,
# and importing it as a module can resolve to an unrelated `tests` package
# elsewhere on sys.path (e.g. via PYTHONPATH leaking from another project).
REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_sample() -> Path | None:
    """Locate the well-known GP7 sample song anywhere under the repo.

    The music collection is user data and gets reorganized freely (it moved
    from the repo root into key-based subfolders like ``gps/Standard/``), so
    hardcoding a location would silently skip every browser test after each
    reshuffle. Searching by name keeps the suite running wherever the file
    lives; the tests still skip cleanly if it is gone entirely.

    Returns:
        Path to the sample ``.gp`` file, or None when it cannot be found.
    """
    for p in REPO_ROOT.glob("**/Joe Walsh-Funk*.gp"):
        if ".venv" not in p.parts and "static" not in p.parts:
            return p
    return None


SAMPLE_GP = _find_sample()


def _find_lyric_sample() -> Path | None:
    """Locate a song whose vocal track carries embedded lyrics.

    The lyrics-preservation feature can only be exercised against a file that
    actually has per-track lyrics; the Funk #49 sample has none. Black
    Sabbath's "Children Of The Sea" is used (342 lyric beats on the Dio
    vocal track) rather than "Man In The Box", whose guitar track carries the
    beat text "Talk box is used…" — that stray "box" once satisfied a lyric
    assertion and masked a real regression. Children Of The Sea's only beat
    text is "pick scrape", so the needle word ``edge`` (from "on the edge of
    time") can only come from actual lyrics. The dependent test skips when
    the file is absent.

    Returns:
        Path to the lyric-bearing ``.gp`` file, or None when not found.
    """
    for p in REPO_ROOT.glob("**/*Children Of The Sea*.gp"):
        if ".venv" not in p.parts and "static" not in p.parts:
            return p
    return None


LYRIC_GP = _find_lyric_sample()

CHROME = (
    shutil.which("google-chrome")
    or shutil.which("chromium")
    or shutil.which("chromium-browser")
)
VENDOR_JS = REPO_ROOT / "static" / "vendor" / "alphatab" / "alphaTab.min.js"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(CHROME is None, reason="no Chrome/Chromium on PATH"),
    pytest.mark.skipif(
        not VENDOR_JS.exists(),
        reason="alphaTab not vendored (run scripts/fetch_vendor.sh)",
    ),
    pytest.mark.skipif(SAMPLE_GP is None, reason="sample .gp song not found in repo"),
]


def _free_port() -> int:
    """Ask the OS for an unused TCP port.

    Binding to port 0 lets the kernel pick, avoiding collisions with a dev
    server that may already be running on the default port 8008.

    Returns:
        An available port number on 127.0.0.1.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Serve the real app (rooted at a copy of the sample song) in a thread.

    Runs uvicorn's event loop in a daemon thread rather than a subprocess so
    the fixture can signal a clean shutdown via ``server.should_exit`` and the
    test process owns the port for its whole lifetime.

    Yields:
        The server's base URL, e.g. ``http://127.0.0.1:54321``.
    """
    root = tmp_path_factory.mktemp("music")
    shutil.copy(SAMPLE_GP, root / SAMPLE_GP.name)
    if LYRIC_GP is not None:
        shutil.copy(LYRIC_GP, root / LYRIC_GP.name)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(root), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        assert time.time() < deadline, "uvicorn did not start in time"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


class Cdp:
    """Minimal Chrome DevTools Protocol driver for one page.

    Just enough protocol to navigate, evaluate JavaScript (optionally with a
    synthetic user gesture, which WebAudio playback requires), and let event
    noise drain — deliberately tiny instead of pulling in Playwright for a
    couple of smoke tests.
    """

    def __init__(self, ws):
        """Wrap an open websocket to a page target.

        Args:
            ws: connected ``websockets`` client for the page's debugger URL.
        """
        self.ws = ws
        self.mid = 0

    async def send(self, method: str, params: dict | None = None) -> dict:
        """Issue one CDP command and wait for its matching response.

        Interleaved event notifications are discarded; only the reply whose id
        matches the request is returned, which keeps call sites synchronous.

        Args:
            method: CDP method name, e.g. ``"Runtime.evaluate"``.
            params: method parameters, if any.

        Returns:
            The ``result`` object of the CDP response.
        """
        self.mid += 1
        await self.ws.send(
            json.dumps({"id": self.mid, "method": method, "params": params or {}})
        )
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.mid:
                return msg.get("result", msg)

    async def js(self, expression: str, gesture: bool = False):
        """Evaluate a JavaScript expression in the page and return its value.

        Args:
            expression: JS source; may return a promise (awaited).
            gesture: mark the evaluation as user-initiated so gated APIs
                (AudioContext playback) are allowed to start.

        Returns:
            The JSON-serializable result value, or None.
        """
        res = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": gesture,
            },
        )
        return res.get("result", {}).get("value")


@contextlib.asynccontextmanager
async def chrome_page(url: str):
    """Launch headless Chrome, attach CDP to its page, navigate, and clean up.

    A context manager so every test gets a fresh browser (no state bleed
    between tests) and Chrome is terminated even when assertions fail.

    Args:
        url: the address to navigate the page to after attaching.

    Yields:
        A connected :class:`Cdp` driver with the navigation already issued.
    """
    import websockets

    debug_port = _free_port()
    chrome = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            f"--remote-debugging-port={debug_port}",
            "--window-size=1400,1200",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{debug_port}/json/list"
                ) as r:
                    ws_url = next(t for t in json.load(r) if t["type"] == "page")[
                        "webSocketDebuggerUrl"
                    ]
                break
            except Exception:
                await asyncio.sleep(0.5)
        assert ws_url, "Chrome debugger never came up"
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            cdp = Cdp(ws)
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("Page.navigate", {"url": url})
            yield cdp
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)


async def wait_ready(cdp: Cdp) -> dict:
    """Poll until the score is rendered and the player is ready.

    Bounded at ~90 s to cover cold-cache SoundFont loading on slow disks;
    returns early the moment the app is usable.

    Args:
        cdp: driver attached to a page that is loading a song.

    Returns:
        The final status dict (overlayHidden/svg/rows/playEnabled) for
        follow-up assertions.
    """
    status = None
    for _ in range(30):
        await asyncio.sleep(3)
        raw = await cdp.js("""JSON.stringify({
            overlayHidden: document.getElementById('overlay').classList.contains('hidden'),
            svg: document.querySelectorAll('#alphatab svg').length,
            rows: document.querySelectorAll('.track-row').length,
            playEnabled: !document.getElementById('btn-play').disabled})""")
        status = json.loads(raw)
        if status["overlayHidden"] and status["svg"] > 0 and status["playEnabled"]:
            break
    assert status and status["overlayHidden"], (
        f"score never finished rendering: {status}"
    )
    assert status["svg"] > 0, f"no score SVG rendered: {status}"
    return status


def _song_url(live_server: str) -> str:
    """Build the deep-link URL that auto-opens the bundled sample song.

    Args:
        live_server: base URL from the ``live_server`` fixture.

    Returns:
        The app URL with the ``?open=`` query for the sample file.
    """
    return f"{live_server}/?open={urllib.parse.quote(SAMPLE_GP.name)}"


def test_load_render_and_play(live_server):
    """The whole app works end-to-end: render all tracks, then really play.

    Asserts the three pillars the UI promises: (1) the conductor view renders
    every track of the sample (7 track rows, SVG score present, overlay gone),
    (2) the SoundFont player becomes ready (play button enabled), and (3) a
    user-gesture click on play starts the synth — verified by the player state
    flag and the position readout advancing past the count-in bar.
    """

    async def scenario():
        async with chrome_page(_song_url(live_server)) as cdp:
            status = await wait_ready(cdp)
            assert status["rows"] == 7, f"expected 7 track rows: {status}"
            assert status["playEnabled"], f"player never became ready: {status}"

            await cdp.js("document.getElementById('btn-play').click()", gesture=True)
            await asyncio.sleep(8)  # count-in bar + a couple seconds of song
            state = await cdp.js("Guitarz.conductor.state.api.playerState")
            pos = await cdp.js("document.getElementById('pos-time').textContent")
            assert state == 1, f"player not in Playing state: {state}"
            assert not pos.startswith("0:00 /"), f"position never advanced: {pos}"

    asyncio.run(scenario())


def test_string_tracks_are_color_coded(live_server):
    """Every string-instrument track gets one distinct color in rail AND score.

    The point of the feature is visual correspondence: the chip next to the
    track name in the rail must let the eye jump to the matching tinted staff.
    So the spec is (1) exactly the string tracks (tuned staves, not drums)
    carry a chip, (2) all chip colors are distinct, and (3) the score model
    carries a per-track style whose color equals that track's chip color —
    alphaTab renders staves from exactly these style objects.
    """

    async def scenario():
        async with chrome_page(_song_url(live_server)) as cdp:
            await wait_ready(cdp)
            raw = await cdp.js("""(() => {
                const tracks = Guitarz.conductor.state.score.tracks;
                const stringTracks = tracks.filter(
                    t => t.staves.some(s => !s.isPercussion && s.tuning.length > 0));
                const rows = [...document.querySelectorAll('.track-row')];
                const chips = rows.map(r => {
                    const c = r.querySelector('.chip');
                    return c ? c.style.backgroundColor : null;
                });
                const styleColors = tracks.map(t => {
                    if (!t.style || t.style.colors.size === 0) return null;
                    const col = [...t.style.colors.values()].find(c => c);
                    return col ? col.rgba : null;
                });
                return JSON.stringify({
                    total: tracks.length,
                    stringCount: stringTracks.length,
                    chips, styleColors});
            })()""")
            data = json.loads(raw)

            chips = [c for c in data["chips"] if c]
            assert len(chips) == data["stringCount"], (
                f"every string track (and only those) needs a chip: {data}"
            )
            assert len(set(chips)) == len(chips), (
                f"chip colors must be distinct: {data}"
            )

            styled = [c for c in data["styleColors"] if c]
            assert len(styled) == data["stringCount"], (
                f"every string track needs a score-side style color: {data}"
            )

    asyncio.run(scenario())


def test_tab_tails_hidden_by_default_with_toggle(live_server):
    """Rhythm tails under tab staves default OFF and toggle back on.

    The user tracks position by the cursor and fret numbers; stems/beams under
    the tab add clutter, so ``notation.rhythmMode`` must start at ``Hidden``
    (0). Clicking the transport's tails button must flip it to
    ``ShowWithBeams`` (1) and trigger a re-render that completes (overlay
    hidden again, SVG still present); a second click restores Hidden.
    """

    async def scenario():
        async with chrome_page(_song_url(live_server)) as cdp:
            await wait_ready(cdp)

            mode = await cdp.js(
                "Guitarz.conductor.state.api.settings.notation.rhythmMode"
            )
            assert mode == 0, f"tails must default to TabRhythmMode.Hidden: {mode}"

            await cdp.js("document.getElementById('btn-tails').click()")
            await asyncio.sleep(4)  # re-render
            mode = await cdp.js(
                "Guitarz.conductor.state.api.settings.notation.rhythmMode"
            )
            assert mode == 1, f"toggle on must set ShowWithBeams: {mode}"
            status = await wait_ready(cdp)
            assert status["svg"] > 0, "score must re-render with tails shown"

            await cdp.js("document.getElementById('btn-tails').click()")
            await asyncio.sleep(4)
            mode = await cdp.js(
                "Guitarz.conductor.state.api.settings.notation.rhythmMode"
            )
            assert mode == 0, f"toggle off must restore Hidden: {mode}"

    asyncio.run(scenario())


def test_global_transpose_stepper(live_server):
    """The transpose stepper shifts playback pitch for pitched tracks only.

    Spec (audio-only, global): the transport stepper starts at +0; each click
    steps one semitone within a clamp of +/-12; clicking the readout resets to
    0; the pitch change is delivered via alphaTab's live
    ``changeTrackTranspositionPitch`` API to every pitched track and to no
    percussion track (transposing MIDI drums would remap sounds, not pitch);
    and a persisted non-zero transpose is automatically re-applied when a new
    score loads. Audio itself can't be asserted headlessly, so the contract is
    pinned at the API boundary with an in-page spy.
    """

    async def scenario():
        async with chrome_page(_song_url(live_server)) as cdp:
            await wait_ready(cdp)

            # Baseline: neutral stepper, neutral state.
            assert (
                await cdp.js("document.getElementById('transpose-val').textContent")
                == "+0"
            )
            assert await cdp.js("Guitarz.conductor.state.transpose") == 0

            # Spy on the alphaTab call so semitones + track choice are observable.
            await cdp.js("""(() => {
                const api = Guitarz.conductor.state.api;
                window.__transposeCalls = [];
                const orig = api.changeTrackTranspositionPitch.bind(api);
                api.changeTrackTranspositionPitch = (tracks, semis) => {
                    window.__transposeCalls.push({idx: tracks.map(t => t.index), semis});
                    return orig(tracks, semis);
                };
            })()""")

            # Two clicks up -> +2, applied to exactly the pitched tracks.
            await cdp.js("document.getElementById('btn-transpose-up').click()")
            await cdp.js("document.getElementById('btn-transpose-up').click()")
            raw = await cdp.js("""JSON.stringify({
                label: document.getElementById('transpose-val').textContent,
                state: Guitarz.conductor.state.transpose,
                calls: window.__transposeCalls,
                pitched: Guitarz.conductor.state.score.tracks
                    .filter(t => t.staves.some(s => !s.isPercussion && s.tuning.length > 0))
                    .map(t => t.index),
                drums: Guitarz.conductor.state.score.tracks
                    .filter(t => t.staves.every(s => s.isPercussion))
                    .map(t => t.index)})""")
            data = json.loads(raw)
            assert data["label"] == "+2", f"readout should show +2: {data}"
            assert data["state"] == 2, f"state should be 2: {data}"
            last = data["calls"][-1]
            assert last["semis"] == 2, f"last API call must carry 2 semitones: {data}"
            assert sorted(last["idx"]) == sorted(data["pitched"]), (
                f"transpose must hit exactly the pitched tracks: {data}"
            )
            assert not set(last["idx"]) & set(data["drums"]), (
                f"drums must be excluded: {data}"
            )

            # Readout click resets to 0.
            await cdp.js("document.getElementById('transpose-val').click()")
            assert (
                await cdp.js("document.getElementById('transpose-val').textContent")
                == "+0"
            )
            assert await cdp.js("window.__transposeCalls.at(-1).semis") == 0

            # Clamp: 13 clicks down stop at -12.
            await cdp.js("""(() => {
                const btn = document.getElementById('btn-transpose-down');
                for (let i = 0; i < 13; i++) btn.click();
            })()""")
            assert (
                await cdp.js("document.getElementById('transpose-val').textContent")
                == "−12"
            )
            assert await cdp.js("Guitarz.conductor.state.transpose") == -12

            # Persistence: reload the song; the -12 must be re-applied automatically.
            await cdp.js("window.__transposeCalls = []")
            await cdp.js(f"""Guitarz.conductor.loadFile({{
                path: {json.dumps(SAMPLE_GP.name)}, name: {json.dumps(SAMPLE_GP.name)}}})""")
            await wait_ready(cdp)
            reapplied = await cdp.js(
                "window.__transposeCalls.some(c => c.semis === -12)"
            )
            assert reapplied, "transpose must be re-applied after loading a score"

    asyncio.run(scenario())


@pytest.mark.skipif(LYRIC_GP is None, reason="no lyric-bearing .gp song found in repo")
def test_hiding_vocal_track_keeps_lyrics_visible(live_server):
    """Hiding a lyric-bearing track moves its lyrics onto a visible track.

    A vocal track's tab staff is noise to a guitarist, but its lyric line is
    the roadmap through the song. Collapsing the vocal staff in place was
    tried first and does NOT work — alphaTab skips a track's effect bands
    (including lyrics) when no staff type is displayed. So the contract is
    transplantation: hiding a lyric track removes its staff entirely and
    re-attaches its lyric chunks, bar-aligned, to the topmost visible pitched
    track; hiding that carrier moves them again; re-showing the vocal track
    restores the original state with no lyric residue left on other tracks.

    Verified against Children Of The Sea with needle word "edge" (from "on
    the edge of time") — chosen because syllable hyphenation breaks up most
    words, and this file's only beat text is "pick scrape", so the needle can
    only come from real lyrics. Lazy partial rendering is disabled so the
    whole score's text is present in the DOM.
    """

    async def scenario():
        async with chrome_page(
            f"{live_server}/?open={urllib.parse.quote(LYRIC_GP.name)}"
        ) as cdp:
            await wait_ready(cdp)

            await cdp.js("""(async () => {
                const api = Guitarz.conductor.state.api;
                api.settings.core.enableLazyLoading = false;
                api.updateSettings();
                const done = new Promise(res => api.renderFinished.on(res));
                api.render();
                await done;
            })()""")
            await asyncio.sleep(4)

            async def snapshot():
                """Collect the render set, lyric word presence, and per-track lyric counts."""
                raw = await cdp.js("""JSON.stringify({
                    rendered: Guitarz.conductor.state.api.tracks.map(t => t.index),
                    hasEdge: document.getElementById('alphatab').textContent
                        .toLowerCase().includes('edge'),
                    lyricCounts: Guitarz.conductor.state.score.tracks.map(t => {
                        let n = 0;
                        for (const s of t.staves) for (const b of s.bars)
                            for (const v of b.voices) for (const beat of v.beats)
                                if (beat.lyrics && beat.lyrics.length) n++;
                        return n;
                    })})""")
                return json.loads(raw)

            base = await snapshot()
            assert base["hasEdge"], f"baseline: lyrics must be visible: {base}"
            assert base["lyricCounts"][0] > 0, f"vocals must own the lyrics: {base}"

            # Hide the Dio vocals (row 0): staff gone, lyrics carried by track 1.
            await cdp.js("document.querySelectorAll('.track-row .btn-eye')[0].click()")
            await asyncio.sleep(8)
            hid = await snapshot()
            assert 0 not in hid["rendered"], f"vocal staff must be gone: {hid}"
            assert hid["hasEdge"], f"lyrics must survive hiding the vocals: {hid}"
            assert hid["lyricCounts"][1] > 0, (
                f"topmost visible track must carry them: {hid}"
            )

            # Hide the current carrier too: lyrics must move to the next track.
            await cdp.js("document.querySelectorAll('.track-row .btn-eye')[1].click()")
            await asyncio.sleep(8)
            moved = await snapshot()
            assert 1 not in moved["rendered"], f"carrier must be hidden: {moved}"
            assert moved["hasEdge"], f"lyrics must survive hiding the carrier: {moved}"
            assert moved["lyricCounts"][1] == 0, f"old carrier must be cleaned: {moved}"
            assert moved["lyricCounts"][2] > 0, (
                f"next visible track must carry them: {moved}"
            )

            # Re-show the vocals: originals back, zero residue anywhere else.
            await cdp.js("document.querySelectorAll('.track-row .btn-eye')[0].click()")
            await asyncio.sleep(8)
            restored = await snapshot()
            assert 0 in restored["rendered"], f"vocal staff must return: {restored}"
            assert restored["hasEdge"], f"lyrics must be visible again: {restored}"
            assert restored["lyricCounts"][0] > 0, (
                f"vocals must own lyrics again: {restored}"
            )
            assert all(n == 0 for n in restored["lyricCounts"][1:]), (
                f"no lyric residue may remain on other tracks: {restored}"
            )

    asyncio.run(scenario())
