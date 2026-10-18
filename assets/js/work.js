/* A single work's permanent page: work.html?id=<key> — the same viewer as everywhere else, as a page. */
(() => {
  "use strict";

  const { $, centuryOf, centuryName, loadRecord, createViewer } = window.FOC;
  const key = new URLSearchParams(location.search).get("id") || decodeURIComponent(location.hash.slice(1));
  const main = $("#wp");

  function notFound() {
    document.title = "Work not found — The Face of Christ";
    main.innerHTML = `<div class="wp-missing"><h1>Work not found</h1>
      <p>This link does not match any work in the collection. It may have been merged with another record or removed after review.</p>
      <p><a class="btn-gold" href="collection.html">Browse the collection</a></p></div>`;
  }

  (async () => {
    const rec = key ? await loadRecord(key) : null;
    if (!rec) return notFound();
    main.innerHTML = "";
    const viewer = createViewer({
      mode: "page",
      mount: main,
      onOpen: (w) => {
        document.title = `${w.t} — The Face of Christ`;
        $("#wp-crumb").textContent = w.y != null ? centuryName(centuryOf(w.y)) : "Date unknown";
      },
    });
    viewer.open([rec], 0);
  })();
})();
