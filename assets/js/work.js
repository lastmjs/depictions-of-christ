/* A single work's permanent page: work.html?id=<key> */
(() => {
  "use strict";

  const { $, esc, ICON_EXT, ICON_CHECK, ICON_BOOK, ICON_SHARE, keyOf, loadInto, original, centuryOf, centuryName,
    loadDetail, shardOf, shareWork, workLinks, workCredit } = window.FOC;

  const key = new URLSearchParams(location.search).get("id") || decodeURIComponent(location.hash.slice(1));
  const main = $("#wp");

  function notFound() {
    document.title = "Work not found — The Face of Christ";
    main.innerHTML = `<div class="wp-missing"><h1>Work not found</h1>
      <p>This link does not match any work in the collection. It may have been merged with another record or removed after review.</p>
      <p><a class="btn-gold" href="collection.html">Browse the collection</a></p></div>`;
  }

  async function load() {
    if (!key) return notFound();
    let rec;
    try {
      const shard = await (await fetch(`data/collection/works/${shardOf(key)}.json`)).json();
      rec = shard[key];
    } catch (e) { /* fall through */ }
    if (!rec) return notFound();
    const det = rec.x || (rec.v ? await loadDetail(key) : null);
    render(rec, det || {});
  }

  function render(w, det) {
    const c = centuryOf(w.y);
    document.title = `${w.t} — The Face of Christ`;
    const descTag = document.querySelector('meta[name="description"]');
    if (det.summary) descTag.setAttribute("content", det.summary.slice(0, 300));
    $("#wp-crumb").textContent = w.y != null ? centuryName(c) : "Date unknown";

    const meta = [["Created", w.c], ["Now at", w.l], ["Medium", w.m], ["Style", w.s]]
      .filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
    const explore = [];
    if (w.p != null) explore.push(`<a class="btn-ghost" href="map.html#place=${w.p}${c ? `&c=${c}` : ""}"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg><span>See it on the map</span></a>`);
    if (w.fe) explore.push(`<a class="btn-ghost" href="./#${encodeURIComponent(w.fe)}"><svg viewBox="0 0 24 24"><path d="M12 2l2.6 6.6L21 9l-5 4.6L17.4 21 12 17.3 6.6 21 8 13.6 3 9l6.4-.4z"/></svg><span>Featured in the Journey</span></a>`);
    if (!w.noimg) explore.push(`<a class="btn-ghost" href="collection.html#${encodeURIComponent(key)}"><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg><span>In the collection</span></a>`);
    if (c != null && c > 0) explore.push(`<a class="btn-ghost" href="collection.html#century=${c}"><span>More from the ${esc(centuryName(c).replace(/^The /, ""))}</span></a>`);

    const links = (det.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ${ICON_EXT}</a>`)
      .concat(workLinks(w));
    const seen = new Set();
    const uniqLinks = links.filter((h) => { const u = (h.match(/href="([^"]+)"/) || [])[1]; if (seen.has(u)) return false; seen.add(u); return true; });

    main.innerHTML = `
      <article class="wp-grid">
        <figure class="wp-media">
          ${w.noimg
            ? `<div class="veil wp-veil"><svg viewBox="0 0 40 40" aria-hidden="true"><use href="#chi-rho"/></svg>
                 <div class="veil-title">${esc(w.t)}</div><p>${esc(w.noimg)}</p>
                 ${w.src ? `<a class="btn-ghost" href="${esc(w.src)}" target="_blank" rel="noopener">View the work ${ICON_EXT}</a>` : ""}</div>`
            : `<a class="wp-img-link" href="${esc(original(w))}" target="_blank" rel="noopener" title="Open the full-resolution image">
                 <img id="wp-img" alt="${esc(w.t)}"></a>
               <figcaption class="credit">${workCredit(w)}</figcaption>`}
        </figure>
        <div class="wp-info">
          <div class="info-date"><span class="yr${(w.d || "").length > 14 ? " long" : ""}">${esc(w.d || "Date unknown")}</span><span class="form">${esc(w.k || "")}</span></div>
          <h1>${esc(w.t)}</h1>
          <p class="info-artist">${esc(w.a || "Unknown artist")}</p>
          ${w.v ? `<p class="d-checked">${ICON_CHECK}Hand-checked</p>` : ""}
          ${det.original_title || (w.ot && w.ot !== w.t) ? `<p class="d-original">${esc(det.original_title || w.ot)}</p>` : ""}
          <div class="d-actions">
            <button class="btn-gold" id="wp-share">${ICON_SHARE}<span>Share</span></button>
          </div>
          <dl class="meta">${meta}</dl>
          ${det.scripture ? `<p class="scripture">${ICON_BOOK}${esc(det.scripture)}</p>` : ""}
          ${det.summary ? `<p class="summary">${esc(det.summary)}</p>` : ""}
          ${(det.facts || []).length ? `<details class="facts" open><summary>Did you know</summary><ul>${det.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></details>` : ""}
          ${det.notes ? `<p class="d-notes">${esc(det.notes)}</p>` : ""}
          ${(w.u || []).length ? `<div class="d-subjects">${w.u.map((s) => `<a href="collection.html#subject=${encodeURIComponent(s)}">${esc(s)}</a>`).join("")}</div>` : ""}
          <div class="wp-explore">${explore.join("")}</div>
          ${uniqLinks.length ? `<h2 class="wp-h">Sources &amp; further reading</h2><div class="links">${uniqLinks.join("")}</div>` : ""}
        </div>
      </article>`;
    const img = $("#wp-img");
    if (img) loadInto(img, w, [1280, 960, 500]);
    $("#wp-share").addEventListener("click", () => shareWork(key, w.t));
  }

  load();
})();
