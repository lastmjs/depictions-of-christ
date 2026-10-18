/* Shared helpers for the collection and the map: image URLs, the work viewer, and data loading. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const ICON_EXT = '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/></svg>';
  const ICON_BOOK = '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 5v16M9 7h6"/></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></svg>';

  /* ---------- image URLs ---------- */
  const COMMONS = "https://upload.wikimedia.org/wikipedia/commons/";
  const enc = (name) => encodeURIComponent(name).replace(/%2C/g, ",").replace(/%28/g, "(").replace(/%29/g, ")");
  function thumb(w, width) {
    const name = w.i, h = w.h, e = enc(name);
    const lower = name.toLowerCase();
    let file = `${width}px-${e}`;
    if (lower.endsWith(".tif") || lower.endsWith(".tiff")) file = `lossy-page1-${width}px-${e}.jpg`;
    else if (lower.endsWith(".svg")) file = `${width}px-${e}.png`;
    return `${COMMONS}thumb/${h[0]}/${h}/${e}/${file}`;
  }
  const original = (w) => (w.i ? `${COMMONS}${w.h[0]}/${w.h}/${enc(w.i)}` : w.big || w.img);
  const filePage = (w) => (w.i ? `https://commons.wikimedia.org/wiki/File:${enc(w.i)}` : w.src);
  const keyOf = (w) => w.q || w.id;
  const KEY_RX = /^(Q\d+|[a-z]+:[\w.\-/]+)$/;
  const SOURCE_CREDIT = {
    met: "Image: The Metropolitan Museum of Art, Open Access (CC0).",
    aic: "Image: Art Institute of Chicago, public domain (CC0).",
    cma: "Image: Cleveland Museum of Art, Open Access (CC0).",
    nga: "Image: National Gallery of Art, Washington, Open Access (CC0).",
    rijks: "Image: Rijksmuseum, Amsterdam (public domain).",
    wga: "Image: Web Gallery of Art (reproduction of a public-domain work).",
    commons: "Image: Wikimedia Commons (public domain or freely licensed; see the file page).",
  };
  const SOURCE_LINK = { wga: "Web Gallery of Art", commons: "Wikimedia Commons", europeana: "Europeana record" };

  // Hide cards whose image cannot be loaded from any source.
  function fail(img) {
    img.classList.add("broken");
    const card = img.closest(".card, .pcard");
    if (card) card.hidden = true;
  }

  // Try progressively smaller standard thumbnail widths, then the original.
  function loadInto(img, w, widths) {
    let queue;
    if (!w.i) queue = (widths[0] >= 900 ? [w.big, w.img] : [w.img, w.big]).filter(Boolean);
    else {
      queue = widths.map((px) => thumb(w, px));
      if (!/\.(tiff?|svg|pdf|djvu)$/i.test(w.i)) queue.push(original(w));
    }
    const next = () => { const src = queue.shift(); if (src) img.src = src; else fail(img); };
    img.onerror = next;
    img.onload = () => img.classList.add("ok");
    next();
  }

  /* ---------- centuries ---------- */
  const centuryOf = (y) => (y == null ? null : y > 0 ? Math.floor((y - 1) / 100) + 1 : 0);
  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  const centuryName = (c) => (c == null ? "Date unknown" : c <= 0 ? "Before the 1st century" : `The ${ordinal(c)} Century`);
  const centurySpan = (c) => (c == null || c <= 0 ? "" : `AD ${(c - 1) * 100 + 1} – ${c * 100}`);

  /* ---------- collection data ---------- */
  // Loads every century file in parallel. onProgress(readyPrefix) is called as the earliest centuries arrive.
  async function loadCollection(onProgress) {
    const index = await (await fetch("data/collection/index.json")).json();
    const parts = new Array(index.centuries.length);
    let shown = 0;
    await Promise.all(index.centuries.map((c, k) =>
      fetch(`data/collection/c-${c.century}.json`).then((r) => r.json()).catch(() => []).then((list) => {
        list.forEach((w) => {
          w.c_ = centuryOf(w.y);
          w.hay = [w.t, w.a, w.c, w.l, w.m, w.s, w.k, w.d, (w.u || []).join(" ")].join(" ").toLowerCase();
        });
        parts[k] = list;
        const before = shown;
        while (shown < parts.length && parts[shown]) shown++;
        if (onProgress && shown > before) onProgress(parts.slice(0, shown).flat());
      })));
    const all = parts.flat();
    all.forEach((w, n) => { w.n = n; });
    return { index, all };
  }

  /* ---------- hand-checked text (sharded, loaded on demand) ---------- */
  const shardCache = new Map();
  function shardOf(key) {
    let h = 5381;
    for (const b of new TextEncoder().encode(key)) h = ((h * 33) ^ b) >>> 0;
    return (h & 0xff).toString(16).padStart(2, "0");
  }
  function loadDetail(key) {
    const s = shardOf(key);
    if (!shardCache.has(s)) {
      shardCache.set(s, fetch(`data/collection/detail/${s}.json`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
    }
    return shardCache.get(s).then((d) => d[key] || null);
  }

  /* ---------- the work viewer dialog ---------- */
  const DIALOG = `
  <dialog class="detail" id="detail" aria-labelledby="d-title">
    <div class="detail-media" id="d-media">
      <img id="d-img" alt="">
      <div class="detail-loading" aria-hidden="true"><span class="spinner"></span></div>
    </div>
    <div class="detail-info">
      <button class="icon-btn detail-close" data-close aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
      <div class="info-date"><span class="yr" id="d-date"></span><span class="form" id="d-form"></span></div>
      <h2 id="d-title"></h2>
      <p class="info-artist" id="d-artist"></p>
      <p class="d-checked" id="d-checked" hidden>${ICON_CHECK}Hand-checked</p>
      <p class="d-original" id="d-original" hidden></p>
      <dl class="meta" id="d-meta"></dl>
      <p class="scripture" id="d-scripture" hidden></p>
      <p class="summary" id="d-summary" hidden></p>
      <details class="facts" id="d-facts" open hidden><summary>Did you know</summary><ul></ul></details>
      <p class="d-notes" id="d-notes" hidden></p>
      <div class="d-subjects" id="d-subjects"></div>
      <div class="links" id="d-links"></div>
      <p class="credit" id="d-credit"></p>
      <div class="detail-nav">
        <button class="btn-ghost" id="d-prev"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg><span>Previous</span></button>
        <button class="btn-ghost" id="d-next"><span>Next</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button>
      </div>
    </div>
  </dialog>`;

  // createViewer({ onSubject(subject), onOpen(work), onClose() }) -> { open(list, i), close(), isOpen }
  function createViewer(opts = {}) {
    if (!$("#detail")) document.body.insertAdjacentHTML("beforeend", DIALOG);
    const d = $("#detail");
    let list = [], idx = -1;

    function showText(det) {
      const sc = $("#d-scripture"), sm = $("#d-summary"), fa = $("#d-facts"), no = $("#d-notes"), og = $("#d-original");
      sc.hidden = !det?.scripture;
      if (det?.scripture) sc.innerHTML = `${ICON_BOOK}${esc(det.scripture)}`;
      sm.hidden = !det?.summary;
      if (det?.summary) sm.textContent = det.summary;
      fa.hidden = !(det?.facts || []).length;
      if (!fa.hidden) $("ul", fa).innerHTML = det.facts.map((f) => `<li>${esc(f)}</li>`).join("");
      no.hidden = !det?.notes;
      if (det?.notes) no.textContent = det.notes;
      og.hidden = !det?.original_title;
      if (det?.original_title) og.textContent = det.original_title;
      if (det?.links?.length) {
        const have = new Set($$("#d-links a").map((a) => a.href));
        const extra = det.links.filter((l) => !have.has(l.url))
          .map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ${ICON_EXT}</a>`).join("");
        $("#d-links").insertAdjacentHTML("afterbegin", extra);
      }
    }

    function render(i) {
      const w = list[i];
      if (!w) return;
      idx = i;
      const img = $("#d-img");
      img.classList.remove("ok");
      img.removeAttribute("src");
      img.alt = w.t;
      loadInto(img, w, [1280, 960, 500]);
      $("#d-date").textContent = w.d || "Date unknown";
      $("#d-date").classList.toggle("long", (w.d || "").length > 14);
      $("#d-form").textContent = w.k || "";
      $("#d-title").textContent = w.t;
      $("#d-artist").textContent = w.a || "Unknown artist";
      $("#d-meta").innerHTML = [["Created", w.c], ["Now at", w.l], ["Medium", w.m], ["Style", w.s]]
        .filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
      $("#d-subjects").innerHTML = (w.u || []).map((s) => `<button data-subject="${esc(s)}">${esc(s)}</button>`).join("");
      const src = (w.id || "").split(":")[0];
      const links = [];
      if (w.w) links.push(`<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(w.w)}" target="_blank" rel="noopener">Wikipedia ${ICON_EXT}</a>`);
      if (w.src) links.push(`<a href="${esc(w.src)}" target="_blank" rel="noopener">${SOURCE_LINK[src] || "Museum page"} ${ICON_EXT}</a>`);
      if (w.q) links.push(`<a href="https://www.wikidata.org/wiki/${w.q}" target="_blank" rel="noopener">Wikidata ${ICON_EXT}</a>`);
      if (w.i) links.push(`<a href="${filePage(w)}" target="_blank" rel="noopener">Image &amp; license ${ICON_EXT}</a>`);
      links.push(`<a href="${esc(original(w))}" target="_blank" rel="noopener">Full resolution ${ICON_EXT}</a>`);
      $("#d-links").innerHTML = links.join("");
      $("#d-credit").innerHTML = (w.i
        ? `Image via Wikimedia Commons — public domain or freely licensed; see the <a href="${filePage(w)}" target="_blank" rel="noopener">file page</a> for author and license.`
        : src === "europeana" ? `Image: ${esc(w.l || "the providing institution")}, via Europeana${w.lic ? ` (${esc(w.lic)})` : ""}.`
        : SOURCE_CREDIT[src] || `Image courtesy of ${esc(w.l || "the museum")}${w.lic ? ` (${esc(w.lic)})` : ", released as open access"}.`) +
        (w.e ? " Date estimated from the artist's lifetime." : "");
      $("#d-checked").hidden = !w.v;
      showText(null);
      if (w.ot && w.ot !== w.t) { $("#d-original").hidden = false; $("#d-original").textContent = w.ot; }
      if (w.v) loadDetail(keyOf(w)).then((det) => { if (list[idx] === w && det) showText(det); });
      $("#d-prev").disabled = i === 0;
      $("#d-next").disabled = i === list.length - 1;
      if (!d.open) d.showModal();
      $(".detail-info", d).scrollTop = 0;
      if (opts.onOpen) opts.onOpen(w, i);
    }

    const step = (dir) => { const i = idx + dir; if (i >= 0 && i < list.length) { if (opts.onStep) opts.onStep(i); render(i); } };

    d.addEventListener("click", (e) => {
      if (e.target === d || e.target.closest("[data-close]")) { d.close(); return; }
      const s = e.target.closest("[data-subject]");
      if (s && opts.onSubject) { d.close(); opts.onSubject(s.dataset.subject); }
    });
    d.addEventListener("close", () => { idx = -1; if (opts.onClose) opts.onClose(); });
    $("#d-img").addEventListener("click", () => { const w = list[idx]; if (w) window.open(original(w), "_blank", "noopener"); });
    $("#d-prev").addEventListener("click", () => step(-1));
    $("#d-next").addEventListener("click", () => step(1));
    document.addEventListener("keydown", (e) => {
      if (!d.open) return;
      if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
    });

    return {
      open(l, i) { list = l; render(i); },
      close() { if (d.open) d.close(); },
      get index() { return idx; },
      get isOpen() { return d.open; },
    };
  }

  window.FOC = {
    $, $$, esc, ICON_EXT, ICON_CHECK, thumb, original, filePage, keyOf, KEY_RX, loadInto,
    centuryOf, ordinal, centuryName, centurySpan, loadCollection, loadDetail, createViewer,
  };
})();
