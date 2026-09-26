/**
 * Application boot: create the alphaTab engine, then wire the three panels
 * together — explorer clicks feed the conductor, the transport drives the
 * conductor's api. Load order (see index.html) guarantees the Guitarz
 * namespace modules exist before this runs.
 */
(function () {
  const Guitarz = window.Guitarz;
  const api = Guitarz.conductor.init();
  Guitarz.transport.init(api);
  Guitarz.explorer.init((file) => Guitarz.conductor.loadFile(file));

  // Deep link: /?open=<relative path> auto-loads a song. Useful for
  // bookmarking a piece and for the headless browser smoke test.
  const openPath = new URLSearchParams(location.search).get("open");
  if (openPath && api) {
    Guitarz.conductor.loadFile({ path: openPath, name: openPath.split("/").pop() });
  }
})();
