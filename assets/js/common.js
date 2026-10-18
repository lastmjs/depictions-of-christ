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
    yale: "Image: Yale University Art Gallery / Yale Center for British Art (public domain).",
    princeton: "Image: Princeton University Art Museum (public domain).",
    getty: "Image: J. Paul Getty Museum, Open Content Program (CC0).",
    walters: "Image: The Walters Art Museum, Baltimore (CC0).",
    vam: "Image: © Victoria and Albert Museum, London (free for non-commercial use).",
    smk: "Image: SMK – National Gallery of Denmark (public domain).",
    nationalmuseum: "Image: Nationalmuseum, Stockholm (public domain).",
    fng: "Image: Finnish National Gallery (CC0).",
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

  /* ---------- permanent work pages + sharing ---------- */
  const ICON_SHARE = '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/></svg>';
  const workUrl = (key) => `work.html?id=${encodeURIComponent(key)}`;
  const absoluteUrl = (rel) => new URL(rel, location.href).href;

  function toast(msg) {
    let t = $("#foc-toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "foc-toast";
      t.className = "toast";
      t.setAttribute("role", "status");
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), 2200);
  }

  async function shareWork(key, title) {
    const url = absoluteUrl(workUrl(key));
    if (navigator.share && matchMedia("(hover: none)").matches) {
      try { await navigator.share({ title: `${title} — The Face of Christ`, url }); return; } catch (e) { if (e.name === "AbortError") return; }
    }
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied");
    } catch (e) {
      prompt("Copy this link:", url);
    }
  }

  function workLinks(w) {
    const src = (w.id || "").split(":")[0];
    const links = [];
    if (w.w) links.push(`<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(w.w)}" target="_blank" rel="noopener">Wikipedia ${ICON_EXT}</a>`);
    if (w.src) links.push(`<a href="${esc(w.src)}" target="_blank" rel="noopener">${SOURCE_LINK[src] || "Museum page"} ${ICON_EXT}</a>`);
    if (w.q) links.push(`<a href="https://www.wikidata.org/wiki/${w.q}" target="_blank" rel="noopener">Wikidata ${ICON_EXT}</a>`);
    if (w.i) links.push(`<a href="${filePage(w)}" target="_blank" rel="noopener">Image &amp; license ${ICON_EXT}</a>`);
    if (w.i || w.big || w.img) links.push(`<a href="${esc(original(w))}" target="_blank" rel="noopener">Full resolution ${ICON_EXT}</a>`);
    return links;
  }

  function workCredit(w) {
    if (w.noimg) return "";
    const src = (w.id || "").split(":")[0];
    return (w.i
      ? `Image via Wikimedia Commons — public domain or freely licensed; see the <a href="${filePage(w)}" target="_blank" rel="noopener">file page</a> for author and license.`
      : w.cr ? `Image: ${esc(w.cr)}${w.lic ? ` (${esc(w.lic)})` : ""}.`
      : src === "europeana" ? `Image: ${esc(w.l || "the providing institution")}, via Europeana${w.lic ? ` (${esc(w.lic)})` : ""}.`
      : src === "feat" ? `Image: ${w.lic ? esc(w.lic) : "public domain or freely licensed"}; see the <a href="${esc(w.src)}" target="_blank" rel="noopener">source</a> for author and license.`
      : SOURCE_CREDIT[src] || `Image courtesy of ${esc(w.l || "the museum")}${w.lic ? ` (${esc(w.lic)})` : ", released as open access"}.`) +
      (w.e ? " Date estimated from the artist's lifetime." : "");
  }

  /* ---------- records by key (for any work, from any page) ---------- */
  const recCache = new Map();
  function loadRecord(key) {
    const s = shardOf(key);
    if (!recCache.has(s)) {
      recCache.set(s, fetch(`data/collection/works/${s}.json`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
    }
    return recCache.get(s).then((d) => d[key] || null);
  }

  /* ---------- zoom & pan for an image inside a stage ---------- */
  function createZoom(stage, img, { onTapEmpty, onSwipe } = {}) {
    const z = { scale: 1, x: 0, y: 0 };
    const apply = () => {
      const sw = stage.clientWidth, sh = stage.clientHeight;
      const iw = img.offsetWidth * z.scale, ih = img.offsetHeight * z.scale;
      const mx = Math.max(0, (iw - sw) / 2), my = Math.max(0, (ih - sh) / 2);
      z.x = Math.min(mx, Math.max(-mx, z.x));
      z.y = Math.min(my, Math.max(-my, z.y));
      img.style.transform = `translate(${z.x}px, ${z.y}px) scale(${z.scale})`;
      stage.classList.toggle("zoomed", z.scale > 1);
    };
    const reset = () => { z.scale = 1; z.x = 0; z.y = 0; apply(); };
    const zoomAt = (factor, cx, cy) => {
      const r = stage.getBoundingClientRect();
      const ox = (cx ?? r.left + r.width / 2) - r.left - r.width / 2;
      const oy = (cy ?? r.top + r.height / 2) - r.top - r.height / 2;
      const ns = Math.min(8, Math.max(1, z.scale * factor));
      const k = ns / z.scale;
      z.x = ox - (ox - z.x) * k;
      z.y = oy - (oy - z.y) * k;
      z.scale = ns;
      if (ns === 1) { z.x = 0; z.y = 0; }
      apply();
    };
    stage.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX, e.clientY); }, { passive: false });
    stage.addEventListener("dblclick", (e) => {
      if (e.pointerType === "mouse" || matchMedia("(hover: hover)").matches) return; // single click handles zoom with a mouse
      if (z.scale > 1) reset(); else zoomAt(2.5, e.clientX, e.clientY);
    });
    const pointers = new Map();
    let pinch = null, moved = false, gestured = false, startX = null, downTarget = null;
    const pinchState = () => {
      const [a, b] = [...pointers.values()];
      return { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
    };
    stage.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button, a")) return;
      stage.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1) { moved = false; gestured = false; startX = e.clientX; downTarget = e.target; }
      if (pointers.size === 2) { pinch = pinchState(); gestured = true; }
      stage.classList.add("dragging");
    });
    stage.addEventListener("pointermove", (e) => {
      const prev = pointers.get(e.pointerId);
      if (!prev) return;
      const cur = { x: e.clientX, y: e.clientY };
      pointers.set(e.pointerId, cur);
      if (pointers.size >= 2 && pinch) {
        const now = pinchState();
        if (z.scale > 1 || now.d / pinch.d > 1) { z.x += now.mx - pinch.mx; z.y += now.my - pinch.my; }
        zoomAt(now.d / pinch.d, now.mx, now.my);
        pinch = now; moved = true;
        return;
      }
      const dx = cur.x - prev.x, dy = cur.y - prev.y;
      if (z.scale > 1) { z.x += dx; z.y += dy; apply(); }
      if (Math.hypot(dx, dy) > 2) moved = true;
    });
    const end = (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      pinch = pointers.size >= 2 ? pinchState() : null;
      if (!pointers.size) {
        stage.classList.remove("dragging");
        const dx = e.clientX - (startX ?? e.clientX);
        if (!gestured && z.scale === 1 && Math.abs(dx) > 70 && e.pointerType !== "mouse" && onSwipe) onSwipe(dx < 0 ? 1 : -1);
        else if (!moved && !gestured && downTarget === stage && z.scale === 1 && onTapEmpty) onTapEmpty();
        else if (!moved && !gestured && downTarget === img && e.pointerType === "mouse") {
          if (z.scale > 1) reset(); else zoomAt(2.5, e.clientX, e.clientY);
        }
      }
    };
    stage.addEventListener("pointerup", end);
    stage.addEventListener("pointercancel", end);
    return { reset, zoomAt, apply };
  }

  /* ---------- the one work viewer (modal on every page; standalone on work.html) ---------- */
  const ICON_MAP = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>';
  const ICON_STAR = '<svg viewBox="0 0 24 24"><path d="M12 2l2.6 6.6L21 9l-5 4.6L17.4 21 12 17.3 6.6 21 8 13.6 3 9l6.4-.4z"/></svg>';
  const ICON_GRID = '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg>';
  const ICON_PAGE = '<svg viewBox="0 0 24 24"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6"/></svg>';

  const VIEWER = (tag) => `
  <${tag} class="wv${tag === "div" ? " wv-page" : ""}" id="wv" aria-labelledby="wv-title">
    <div class="wv-shell">
      <div class="wv-stage" id="wv-stage">
        <img id="wv-img" alt="" draggable="false">
        <div class="wv-veil" id="wv-veil" hidden></div>
        <div class="wv-loading" id="wv-loading"><span class="spinner"></span></div>
        <div class="wv-tools">
          <button class="icon-btn" id="wv-zoom-out" aria-label="Zoom out"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M16 16l4 4"/></svg></button>
          <button class="icon-btn" id="wv-zoom-in" aria-label="Zoom in"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M8 11h6M11 8v6M16 16l4 4"/></svg></button>
          <a class="icon-btn" id="wv-full" target="_blank" rel="noopener" aria-label="Full-resolution image"><svg viewBox="0 0 24 24"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg></a>
        </div>
        <button class="wv-nav wv-prev" id="wv-prev" aria-label="Previous work"><svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg></button>
        <button class="wv-nav wv-next" id="wv-next" aria-label="Next work"><svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg></button>
        <span class="wv-pos" id="wv-pos"></span>
      </div>
      <div class="wv-info" id="wv-info"></div>
      ${tag === "dialog" ? `<button class="icon-btn wv-close" data-close autofocus aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>` : ""}
    </div>
  </${tag}>`;

  function infoHTML(w, det, opts) {
    const key = keyOf(w);
    const c = centuryOf(w.y);
    const meta = [["Created", w.c], ["Now at", w.l], ["Medium", w.m], ["Style", w.s]]
      .filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
    const explore = [];
    if (w.p != null && opts.page !== "map") explore.push(`<a class="btn-ghost" href="map.html#place=${w.p}${c ? `&c=${c}` : ""}">${ICON_MAP}<span>On the map</span></a>`);
    if (w.fe && opts.page !== "journey") explore.push(`<a class="btn-ghost" href="./#${encodeURIComponent(w.fe)}">${ICON_STAR}<span>In the Journey</span></a>`);
    if (!w.noimg && opts.page !== "collection") explore.push(`<a class="btn-ghost" href="collection.html#${encodeURIComponent(key)}">${ICON_GRID}<span>In the collection</span></a>`);
    if (c != null && c > 0) explore.push(`<a class="btn-ghost" href="collection.html#century=${c}"><span>More from the ${esc(centuryName(c).replace(/^The /, ""))}</span></a>`);
    const seen = new Set();
    const links = (det.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ${ICON_EXT}</a>`)
      .concat(workLinks(w))
      .filter((h) => { const u = (h.match(/href="([^"]+)"/) || [])[1]; if (seen.has(u)) return false; seen.add(u); return true; });
    const subjects = (w.u || []).map((s) => opts.page === "collection"
      ? `<button data-subject="${esc(s)}">${esc(s)}</button>`
      : `<a href="collection.html#subject=${encodeURIComponent(s)}">${esc(s)}</a>`).join("");
    return `
      <div class="info-date"><span class="yr${(w.d || "").length > 14 ? " long" : ""}">${esc(w.d || "Date unknown")}</span><span class="form">${esc(w.k || "")}</span></div>
      <${opts.mode === "page" ? "h1" : "h2"} id="wv-title">${esc(w.t)}</${opts.mode === "page" ? "h1" : "h2"}>
      <p class="info-artist">${esc(w.a || "Unknown artist")}</p>
      ${w.v ? `<p class="d-checked">${ICON_CHECK}Hand-checked</p>` : ""}
      ${det.original_title || (w.ot && w.ot !== w.t) ? `<p class="d-original">${esc(det.original_title || w.ot)}</p>` : ""}
      <div class="d-actions">
        <button class="btn-gold" data-share-key="${esc(key)}" data-share-title="${esc(w.t)}">${ICON_SHARE}<span>Share</span></button>
        ${opts.mode === "page" ? "" : `<a class="btn-ghost" href="${workUrl(key)}">${ICON_PAGE}<span>Open page</span></a>`}
      </div>
      <dl class="meta">${meta}</dl>
      ${det.scripture ? `<p class="scripture">${ICON_BOOK}${esc(det.scripture)}</p>` : ""}
      ${det.summary ? `<p class="summary">${esc(det.summary)}</p>` : ""}
      ${(det.facts || []).length ? `<details class="facts" open><summary>Did you know</summary><ul>${det.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></details>` : ""}
      ${det.notes ? `<p class="d-notes">${esc(det.notes)}</p>` : ""}
      ${subjects ? `<div class="d-subjects">${subjects}</div>` : ""}
      <div class="wv-explore">${explore.join("")}</div>
      ${links.length ? `<h3 class="wv-h">Sources &amp; further reading</h3><div class="links">${links.join("")}</div>` : ""}
      ${w.noimg ? "" : `<p class="credit">${workCredit(w)}</p>`}`;
  }

  // createViewer({ mode: "modal" | "page", page, mount, onOpen, onClose, onSubject, onStep })
  // .open(items, i): items are work records or work keys (strings, loaded on demand).
  function createViewer(opts = {}) {
    const mode = opts.mode || "modal";
    if (!$("#wv")) (opts.mount || document.body).insertAdjacentHTML("beforeend", VIEWER(mode === "page" ? "div" : "dialog"));
    const root = $("#wv"), stage = $("#wv-stage"), img = $("#wv-img"), info = $("#wv-info");
    let list = [], idx = -1, token = 0;
    const zoom = createZoom(stage, img, {
      onTapEmpty: () => { if (mode === "modal") root.close(); },
      onSwipe: (dir) => step(dir),
    });
    img.addEventListener("load", () => { img.classList.add("ok"); $("#wv-loading").hidden = true; zoom.reset(); });

    async function render(i) {
      idx = i;
      const my = ++token;
      let w = list[i];
      if (typeof w === "string") {
        $("#wv-loading").hidden = false;
        w = await loadRecord(w);
        if (my !== token) return;
        if (!w) { info.innerHTML = `<p class="d-notes">This work could not be found.</p>`; return; }
        list[i] = w;
      }
      const key = keyOf(w);
      img.classList.remove("ok");
      img.removeAttribute("src");
      zoom.reset();
      const veil = $("#wv-veil");
      if (w.noimg) {
        img.hidden = true; $("#wv-loading").hidden = true; veil.hidden = false;
        veil.innerHTML = `<svg viewBox="0 0 40 40" aria-hidden="true"><use href="#chi-rho"/></svg><div class="veil-title">${esc(w.t)}</div><p>${esc(w.noimg)}</p>${w.src ? `<a class="btn-ghost" href="${esc(w.src)}" target="_blank" rel="noopener">View the work ${ICON_EXT}</a>` : ""}`;
        $("#wv-full").hidden = true;
      } else {
        img.hidden = false; veil.hidden = true; $("#wv-loading").hidden = false;
        img.alt = w.t;
        loadInto(img, w, [1280, 960, 500]);
        $("#wv-full").hidden = false;
        $("#wv-full").href = original(w);
      }
      const det = w.x || (w.v ? (await loadDetail(key)) || {} : {});
      if (my !== token) return;
      info.innerHTML = infoHTML(w, det, { mode, page: opts.page });
      info.scrollTop = 0;
      $("#wv-prev").hidden = !(list.length > 1 && i > 0);
      $("#wv-next").hidden = !(list.length > 1 && i < list.length - 1);
      $("#wv-pos").textContent = list.length > 1 ? `${(i + 1).toLocaleString()} / ${list.length.toLocaleString()}` : "";
      if (mode === "modal" && !root.open) root.showModal();
      if (opts.onOpen) opts.onOpen(w, i);
    }

    const step = (dir) => { const i = idx + dir; if (i >= 0 && i < list.length) { if (opts.onStep) opts.onStep(i); render(i); } };
    $("#wv-prev").addEventListener("click", () => step(-1));
    $("#wv-next").addEventListener("click", () => step(1));
    $("#wv-zoom-in").addEventListener("click", () => zoom.zoomAt(1.5));
    $("#wv-zoom-out").addEventListener("click", () => zoom.zoomAt(1 / 1.5));
    root.addEventListener("click", (e) => {
      if (mode === "modal" && (e.target === root || e.target.closest("[data-close]"))) { root.close(); return; }
      const sh = e.target.closest("[data-share-key]");
      if (sh) { shareWork(sh.dataset.shareKey, sh.dataset.shareTitle); return; }
      const s = e.target.closest("[data-subject]");
      if (s && opts.onSubject) { if (mode === "modal") root.close(); opts.onSubject(s.dataset.subject); }
    });
    if (mode === "modal") root.addEventListener("close", () => { idx = -1; token++; if (opts.onClose) opts.onClose(); });
    document.addEventListener("keydown", (e) => {
      if (mode === "modal" && !root.open) return;
      if (e.target.closest && e.target.closest("input, textarea, select")) return;
      if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "+" || e.key === "=") zoom.zoomAt(1.4);
      else if (e.key === "-") zoom.zoomAt(1 / 1.4);
    });

    return {
      open(l, i) { list = l.slice(); render(i); },
      close() { if (mode === "modal" && root.open) root.close(); },
      get index() { return idx; },
      get isOpen() { return mode === "page" || root.open; },
    };
  }

  window.FOC = {
    $, $$, esc, ICON_EXT, ICON_CHECK, ICON_BOOK, ICON_SHARE, workUrl, shareWork, toast, shardOf, workLinks, workCredit, SOURCE_CREDIT, SOURCE_LINK, thumb, original, filePage, keyOf, KEY_RX, loadInto,
    centuryOf, ordinal, centuryName, centurySpan, loadCollection, loadDetail, loadRecord, createViewer, createZoom,
  };
})();
