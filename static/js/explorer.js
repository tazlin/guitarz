/**
 * File explorer panel: browses the server's music root via /api/browse.
 *
 * The server returns one directory level at a time (never a recursive tree),
 * so this module holds the current location implicitly in what it last
 * rendered — clicking a folder or breadcrumb segment simply re-browses.
 */
(function () {
  const Guitarz = (window.Guitarz = window.Guitarz || {});

  const breadcrumbEl = document.getElementById("breadcrumb");
  const listEl = document.getElementById("filelist");
  let onOpenFile = null;
  let selectedPath = null;
  let staticCatalog = null;

  /**
   * Format a byte count for the file list's size hint.
   *
   * Coarse units are enough here — the size exists only to distinguish a
   * full transcription from a fragment at a glance.
   *
   * @param {number} bytes - file size from the API.
   * @returns {string} human-readable size, e.g. "43 KB".
   */
  function fmtSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  /**
   * Fetch one directory level from the server and render it.
   *
   * The path is encoded with encodeURIComponent because tab filenames
   * routinely contain "#" and spaces, which would otherwise truncate the
   * query string.
   *
   * @param {string} path - root-relative directory ("" for the root).
   * @returns {Promise<void>} resolves after the panel is re-rendered.
   */
  async function browse(path) {
    try {
      let data;
      if (window.GuitarzStatic) {
        if (!staticCatalog) {
          const res = await fetch("/catalog.json");
          if (!res.ok) throw new Error("catalog unavailable");
          staticCatalog = await res.json();
        }
        data = Object.hasOwn(staticCatalog, path || "") ? staticCatalog[path || ""] : null;
        if (!data) throw new Error("directory not found");
      } else {
        const res = await fetch("/api/browse?path=" + encodeURIComponent(path || ""));
        if (!res.ok) throw new Error("directory unavailable");
        data = await res.json();
      }
      renderBreadcrumb(data.path);
      renderList(data);
    } catch (e) {
      listEl.innerHTML = "<li>error loading directory</li>";
    }
  }

  /**
   * Rebuild the breadcrumb trail for the current directory.
   *
   * Every segment (including "root") is a link that re-browses that level,
   * which is the only "go up" affordance besides the ".." row.
   *
   * @param {string} path - root-relative path of the directory being shown.
   */
  function renderBreadcrumb(path) {
    breadcrumbEl.innerHTML = "";
    const mkLink = (label, target) => {
      const a = document.createElement("a");
      a.textContent = label;
      a.onclick = () => browse(target);
      return a;
    };
    breadcrumbEl.appendChild(mkLink("root", ""));
    if (path) {
      let acc = "";
      for (const part of path.split("/")) {
        acc = acc ? acc + "/" + part : part;
        breadcrumbEl.appendChild(document.createTextNode(" / "));
        breadcrumbEl.appendChild(mkLink(part, acc));
      }
    }
  }

  /**
   * Render the directory listing: ".." row, folders, then files.
   *
   * Rows are DOM-built (not innerHTML-concatenated) so arbitrary filenames
   * can never inject markup. Clicking a file marks it selected and hands it
   * to the conductor via the callback registered in init().
   *
   * @param {{path: string, dirs: Array<{name: string, path: string}>,
   *          files: Array<{name: string, path: string, size: number}>}} data
   *        one directory level as returned by /api/browse.
   */
  function renderList(data) {
    listEl.innerHTML = "";
    if (data.path) {
      const up = document.createElement("li");
      up.className = "dir";
      up.textContent = "..";
      up.onclick = () => browse(data.path.split("/").slice(0, -1).join("/"));
      listEl.appendChild(up);
    }
    for (const d of data.dirs) {
      const li = document.createElement("li");
      li.className = "dir";
      li.textContent = d.name;
      li.onclick = () => browse(d.path);
      listEl.appendChild(li);
    }
    for (const f of data.files) {
      const li = document.createElement("li");
      li.className = "file";
      if (f.path === selectedPath) li.classList.add("selected");
      li.textContent = f.name;
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = fmtSize(f.size);
      li.appendChild(size);
      li.onclick = () => {
        selectedPath = f.path;
        listEl.querySelectorAll("li.selected").forEach((el) => el.classList.remove("selected"));
        li.classList.add("selected");
        if (onOpenFile) onOpenFile(f);
      };
      listEl.appendChild(li);
    }
    if (!data.dirs.length && !data.files.length) {
      const li = document.createElement("li");
      li.textContent = "(no Guitar Pro files here)";
      listEl.appendChild(li);
    }
  }

  Guitarz.explorer = {
    /**
     * Wire the explorer and show the music root's top level.
     *
     * @param {(file: {name: string, path: string}) => void} openFileCallback
     *        invoked when the user clicks a song; receives the API file entry.
     */
    init(openFileCallback) {
      onOpenFile = openFileCallback;
      browse("");
    },
  };
})();
