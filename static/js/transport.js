/**
 * Transport bar: play/pause/stop, count-in, loop, speed, and the position
 * readout (bar number, elapsed/total time, progress bar).
 *
 * Everything here is a thin mapping between DOM controls and alphaTab player
 * properties; no playback state is duplicated locally except the cached song
 * end time needed to convert progress-bar clicks into seek positions.
 */
(function () {
  const Guitarz = (window.Guitarz = window.Guitarz || {});

  const btnPlay = document.getElementById("btn-play");
  const btnStop = document.getElementById("btn-stop");
  const btnCountIn = document.getElementById("btn-countin");
  const btnTails = document.getElementById("btn-tails");
  const btnLoop = document.getElementById("btn-loop");
  const btnClearLoop = document.getElementById("btn-clearloop");
  const speed = document.getElementById("speed");
  const speedVal = document.getElementById("speed-val");
  const btnTransposeDown = document.getElementById("btn-transpose-down");
  const btnTransposeUp = document.getElementById("btn-transpose-up");
  const transposeVal = document.getElementById("transpose-val");
  const posBar = document.getElementById("pos-bar");
  const posTime = document.getElementById("pos-time");
  const progress = document.getElementById("progress");
  const progressFill = document.getElementById("progress-fill");

  let api = null;
  let endTime = 0;

  /**
   * Format milliseconds as m:ss for the time readout.
   *
   * @param {number} ms - a time position from the player, in milliseconds.
   * @returns {string} e.g. "3:46".
   */
  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  /**
   * Wire the transport controls to a live alphaTab api instance.
   *
   * Buttons start disabled in the HTML and are enabled only on playerReady
   * (SoundFont downloaded and synth initialized) so the user cannot press
   * play into silence. Count-in defaults to ON — the button is pre-lit in the
   * markup and the volume is set here to match. Click-to-seek on the score
   * and drag-to-select a loop range are alphaTab built-ins; the loop button
   * only has to flip api.isLooping.
   *
   * @param {object|null} alphaTabApi - instance from Guitarz.conductor.init();
   *        null (vendor assets missing) leaves the transport inert.
   */
  function init(alphaTabApi) {
    api = alphaTabApi;
    if (!api) return;

    api.countInVolume = 1; // count-in on by default (button starts lit)

    api.playerReady.on(() => {
      btnPlay.disabled = false;
      btnStop.disabled = false;
    });

    api.playerStateChanged.on((e) => {
      const playing = e.state === alphaTab.synth.PlayerState.Playing;
      btnPlay.innerHTML = playing ? "&#10074;&#10074;" : "&#9654;";
    });

    api.playerPositionChanged.on((e) => {
      endTime = e.endTime;
      posTime.textContent = fmt(e.currentTime) + " / " + fmt(e.endTime);
      progressFill.style.width = (e.endTime ? (100 * e.currentTime) / e.endTime : 0) + "%";
    });

    api.playedBeatChanged.on((beat) => {
      if (beat) posBar.textContent = "bar " + (beat.voice.bar.index + 1);
    });

    btnPlay.onclick = () => api.playPause();
    btnStop.onclick = () => {
      api.stop();
      posBar.textContent = "bar –";
    };

    btnCountIn.onclick = () => {
      api.countInVolume = btnCountIn.classList.toggle("on") ? 1 : 0;
    };

    // Tails = rhythm stems/beams under tab staves. Hidden by default (the
    // cursor + fret numbers are the position reference); toggling flips
    // notation.rhythmMode and needs updateSettings + a re-render because it
    // changes layout, unlike the purely-audio toggles around it.
    btnTails.onclick = () => {
      const show = btnTails.classList.toggle("on");
      api.settings.notation.rhythmMode = show
        ? alphaTab.TabRhythmMode.ShowWithBeams
        : alphaTab.TabRhythmMode.Hidden;
      api.updateSettings();
      api.render();
    };

    btnLoop.onclick = () => {
      api.isLooping = btnLoop.classList.toggle("on");
    };

    btnClearLoop.onclick = () => {
      api.playbackRange = null;
    };

    speed.oninput = () => {
      speedVal.textContent = speed.value + "%";
      api.playbackSpeed = speed.value / 100;
    };

    /**
     * Push a transpose request to the conductor and echo the clamped result.
     *
     * The conductor owns the value (it re-applies it on song changes and
     * clamps to its ±12 range); the transport only renders what actually took
     * effect — a true minus sign for flats-direction, accent color when the
     * band is off its original key.
     *
     * @param {number} semitones - requested global offset.
     */
    function setTranspose(semitones) {
      const n = Guitarz.conductor.applyTranspose(semitones);
      transposeVal.textContent = n < 0 ? "−" + -n : "+" + n;
      transposeVal.classList.toggle("active", n !== 0);
    }

    btnTransposeDown.onclick = () => setTranspose(Guitarz.conductor.state.transpose - 1);
    btnTransposeUp.onclick = () => setTranspose(Guitarz.conductor.state.transpose + 1);
    transposeVal.onclick = () => setTranspose(0);

    progress.onclick = (ev) => {
      if (!endTime) return;
      const rect = progress.getBoundingClientRect();
      api.timePosition = ((ev.clientX - rect.left) / rect.width) * endTime;
    };

    document.addEventListener("keydown", (ev) => {
      if (ev.code === "Space" && !/INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) {
        ev.preventDefault();
        if (!btnPlay.disabled) api.playPause();
      }
    });
  }

  Guitarz.transport = { init };
})();
