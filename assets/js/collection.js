/* The Complete Collection — chronological archive of every catalogued depiction */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const ICON_EXT = '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/></svg>';
  const BATCH = 120;

  /* ---------- Commons image URLs ---------- */
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
  const SOURCE_CREDIT = {
    met: "Image: The Metropolitan Museum of Art, Open Access (CC0).",
    aic: "Image: Art Institute of Chicago, public domain (CC0).",
    cma: "Image: Cleveland Museum of Art, Open Access (CC0).",
    nga: "Image: National Gallery of Art, Washington, Open Access (CC0).",
    rijks: "Image: Rijksmuseum, Amsterdam (public domain).",
    wga: "Image: Web Gallery of Art (reproduction of a public-domain work).",
  };
  const KEY_RX = /^(Q\d+|[a-z]+:[\w.-]+)$/;

  // Hide cards whose image cannot be loaded from any source.
  function fail(img) {
    img.classList.add("broken");
    const card = img.closest(".card");
    if (card) card.hidden = true;
  }

  // Try progressively smaller standard thumbnail widths, then the original.
  function loadInto(img, w, widths) {
    if (!w.i) {
      // museum open-access record with direct image URLs
      const queue = (widths[0] >= 900 ? [w.big, w.img] : [w.img, w.big]).filter(Boolean);
      img.onerror = () => { const src = queue.shift(); if (src) img.src = src; else fail(img); };
      img.onload = () => img.classList.add("ok");
      img.src = queue.shift();
      return;
    }
    const queue = widths.map((px) => thumb(w, px));
    const lower = w.i.toLowerCase();
    if (!/\.(tiff?|svg|pdf|djvu)$/.test(lower)) queue.push(original(w));
    const next = () => {
      const src = queue.shift();
      if (!src) { fail(img); return; }
      img.src = src;
    };
    img.onerror = next;
    img.onload = () => img.classList.add("ok");
    next();
  }

  /* ---------- state ---------- */
  let all = [];
  let filtered = [];
  let rendered = 0;
  let index = null;
  const state = { q: "", subject: "", form: "", order: "asc" };

  const centuryOf = (y) => (y == null ? null : y > 0 ? Math.floor((y - 1) / 100) + 1 : 0);
  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  function centuryName(c) {
    if (c == null) return "Date unknown";
    if (c <= 0) return "Before the 1st century";
    return `The ${ordinal(c)} Century`;
  }
  function centurySpan(c) {
    if (c == null || c <= 0) return "";
    return `AD ${(c - 1) * 100 + 1} – ${c * 100}`;
  }

  /* ---------- load ---------- */
  async function load() {
    try {
      index = await (await fetch("data/collection/index.json")).json();
    } catch (e) {
      $("#col-total").textContent = "The collection could not be loaded.";
      return;
    }
    // Load every century in parallel, but start showing the earliest ones as soon as they arrive.
    const parts = new Array(index.centuries.length);
    let shown = 0, previewed = false;
    const prep = (list) => list.forEach((w) => {
      w.c_ = centuryOf(w.y);
      w.hay = [w.t, w.a, w.c, w.l, w.m, w.s, w.k, w.d, (w.u || []).join(" ")].join(" ").toLowerCase();
    });
    await Promise.all(index.centuries.map((c, k) =>
      fetch(`data/collection/c-${c.century}.json`).then((r) => r.json()).catch(() => []).then((list) => {
        prep(list);
        parts[k] = list;
        while (shown < parts.length && parts[shown]) shown++;
        if (!previewed && shown && !location.hash) {
          const ready = parts.slice(0, shown).flat();
          if (ready.length >= 150) {
            previewed = true;
            all = ready;
            all.forEach((w, n) => { w.n = n; });
            filtered = all.slice();
            $("#col-results").innerHTML = "";
            rendered = 0; lastCentury = undefined;
            countByCentury.clear();
            filtered.forEach((w) => countByCentury.set(w.c_, (countByCentury.get(w.c_) || 0) + 1));
            renderMore();
          }
        }
      })));
    all = parts.flat();
    all.forEach((w, n) => { w.n = n; });
    $("#col-total").innerHTML = `<strong>${all.length.toLocaleString()}</strong> works of art`;

    const subj = $("#subject");
    index.subjects.forEach(([s, n]) => subj.insertAdjacentHTML("beforeend", `<option value="${esc(s)}">${esc(s)} (${n.toLocaleString()})</option>`));
    const form = $("#form");
    index.types.filter(([, n]) => n >= 5).forEach(([t, n]) => form.insertAdjacentHTML("beforeend", `<option value="${esc(t)}">${esc(t[0].toUpperCase() + t.slice(1))} (${n.toLocaleString()})</option>`));

    const jump = readHash();
    if (previewed && !state.q && !state.subject && !state.form && state.order === "asc") {
      // the preview already shows the start of the full, unfiltered list: just extend it
      filtered = all;
      $("#col-count").innerHTML = `Showing all <b>${all.length.toLocaleString()}</b>`;
      renderHistogram();
    } else {
      apply();
    }
    openFromHash();
    if (jump) requestAnimationFrame(() => jumpToCentury(jump));
  }

  /* ---------- filtering + rendering ---------- */
  function apply() {
    const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    filtered = all.filter((w) =>
      (!state.subject || (w.u || []).includes(state.subject)) &&
      (!state.form || w.k === state.form) &&
      terms.every((t) => w.hay.includes(t)));
    if (state.order === "desc") {
      filtered.reverse();
      // keep undated works last either way
      filtered.sort((a, b) => (a.y == null) - (b.y == null));
    }
    $("#col-count").innerHTML = filtered.length === all.length
      ? `Showing all <b>${all.length.toLocaleString()}</b>`
      : `<b>${filtered.length.toLocaleString()}</b> of ${all.length.toLocaleString()} works`;
    $("#col-empty").hidden = filtered.length > 0;
    renderHistogram();
    $("#col-results").innerHTML = "";
    rendered = 0;
    lastCentury = undefined;
    renderMore();
    writeHash();
  }

  let lastCentury;
  let currentWall = null;
  function renderMore(until) {
    const target = Math.min(filtered.length, until != null ? Math.max(until + 1, rendered + BATCH) : rendered + BATCH);
    const results = $("#col-results");
    const frag = document.createDocumentFragment();
    for (; rendered < target; rendered++) {
      const w = filtered[rendered];
      if (w.c_ !== lastCentury) {
        lastCentury = w.c_;
        const count = countByCentury.get(w.c_) || 0;
        const sec = document.createElement("section");
        sec.className = "century";
        sec.innerHTML = `
          <header class="century-head" id="century-${w.c_ ?? "undated"}">
            <h2>${esc(centuryName(w.c_))}</h2>
            <span class="span">${esc(centurySpan(w.c_))}</span>
            <span class="n">${count.toLocaleString()} ${count === 1 ? "work" : "works"}</span>
          </header>
          <div class="wall"></div>`;
        frag.appendChild(sec);
        currentWall = $(".wall", sec);
      }
      const card = document.createElement("button");
      card.className = "card";
      card.dataset.i = rendered;
      card.innerHTML = `
        <div class="card-img"><img alt="" loading="lazy" decoding="async"></div>
        <div class="card-meta"><b>${esc(w.d || "Undated")}</b><span>${esc(w.t)}</span><small>${esc(w.a || w.c || w.l || "")}</small></div>`;
      card.setAttribute("aria-label", `${w.t}${w.a ? ", " + w.a : ""}${w.d ? ", " + w.d : ""}`);
      loadInto($("img", card), w, [330, 500]);
      currentWall.appendChild(card);
    }
    results.appendChild(frag);
    requestAnimationFrame(() => {
      const r = $("#col-sentinel").getBoundingClientRect();
      if (r.top < innerHeight + 1200 && rendered < filtered.length && until == null) renderMore();
    });
  }

  const countByCentury = new Map();
  function renderHistogram() {
    countByCentury.clear();
    filtered.forEach((w) => countByCentury.set(w.c_, (countByCentury.get(w.c_) || 0) + 1));
    const cs = [...new Set(all.map((w) => w.c_).filter((c) => c != null && c > 0))].sort((a, b) => a - b);
    if (!cs.length) return;
    const full = [];
    for (let c = cs[0]; c <= cs[cs.length - 1]; c++) full.push(c);
    const max = Math.max(1, ...full.map((c) => countByCentury.get(c) || 0));
    $("#histogram").innerHTML = full.map((c) => {
      const n = countByCentury.get(c) || 0;
      const h = n ? Math.max(3, Math.sqrt(n / max) * 100) : 2;
      return `<button class="hist-bar${n ? "" : " empty"}" data-c="${c}" title="${esc(centuryName(c))}: ${n.toLocaleString()} works" ${n ? "" : "disabled"}>
        <i style="height:${h}%"></i><span>${c > 0 ? (c - 1) * 100 : ""}</span></button>`;
    }).join("");
  }

  function jumpToCentury(c) {
    const i = filtered.findIndex((w) => w.c_ === c);
    if (i < 0) return;
    if (i >= rendered) renderMore(i + BATCH);
    requestAnimationFrame(() => {
      const el = document.getElementById(`century-${c}`);
      if (!el) return;
      const far = Math.abs(el.getBoundingClientRect().top) > innerHeight * 3;
      el.scrollIntoView({ behavior: far ? "instant" : "smooth", block: "start" });
    });
  }

  /* ---------- detail ---------- */
  let openIdx = -1;
  function openDetail(i) {
    const w = filtered[i];
    if (!w) return;
    openIdx = i;
    const d = $("#detail");
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
    $("#d-meta").innerHTML = [
      ["Created", w.c],
      ["Now at", w.l],
      ["Medium", w.m],
      ["Style", w.s],
    ].filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
    $("#d-subjects").innerHTML = (w.u || []).map((s) => `<button data-subject="${esc(s)}">${esc(s)}</button>`).join("");
    const links = [];
    if (w.w) links.push(`<a href="https://en.wikipedia.org/wiki/${encodeURIComponent(w.w)}" target="_blank" rel="noopener">Wikipedia ${ICON_EXT}</a>`);
    if (w.src) links.push(`<a href="${esc(w.src)}" target="_blank" rel="noopener">${(w.id || "").startsWith("wga:") ? "Web Gallery of Art" : (w.id || "").startsWith("commons:") ? "Wikimedia Commons" : "Museum page"} ${ICON_EXT}</a>`);
    if (w.q) links.push(`<a href="https://www.wikidata.org/wiki/${w.q}" target="_blank" rel="noopener">Wikidata ${ICON_EXT}</a>`);
    if (w.i) links.push(`<a href="${filePage(w)}" target="_blank" rel="noopener">Image &amp; license ${ICON_EXT}</a>`);
    links.push(`<a href="${esc(original(w))}" target="_blank" rel="noopener">Full resolution ${ICON_EXT}</a>`);
    $("#d-links").innerHTML = links.join("");
    $("#d-credit").innerHTML = (w.i
      ? `Image via Wikimedia Commons — public domain or freely licensed; see the <a href="${filePage(w)}" target="_blank" rel="noopener">file page</a> for author and license.`
      : SOURCE_CREDIT[(w.id || "").split(":")[0]] || `Image courtesy of ${esc(w.l || "the museum")}, released as open access.`) +
      (w.e ? " Date estimated from the artist's lifetime." : "");
    $("#d-prev").disabled = i === 0;
    $("#d-next").disabled = i === filtered.length - 1;
    if (!d.open) d.showModal();
    $(".detail-info", d).scrollTop = 0;
    history.replaceState(null, "", `#${keyOf(w)}`);
  }
  function closeDetail() {
    openIdx = -1;
    writeHash();
  }

  /* ---------- hash state ---------- */
  function writeHash() {
    if (openIdx >= 0) return;
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.subject) p.set("subject", state.subject);
    if (state.form) p.set("form", state.form);
    if (state.order !== "asc") p.set("order", state.order);
    const h = p.toString();
    history.replaceState(null, "", h ? `#${h}` : location.pathname + location.search);
  }
  function readHash() {
    const raw = location.hash.slice(1);
    if (!raw || KEY_RX.test(raw)) return null;
    const p = new URLSearchParams(raw);
    state.q = p.get("q") || "";
    state.subject = p.get("subject") || "";
    state.form = p.get("form") || "";
    state.order = p.get("order") || "asc";
    $("#q").value = state.q;
    $("#subject").value = state.subject;
    $("#form").value = state.form;
    $("#order").value = state.order;
    return p.get("century") ? +p.get("century") : null;
  }
  function openFromHash() {
    const raw = location.hash.slice(1);
    const key = decodeURIComponent(raw);
    if (!KEY_RX.test(key)) return;
    const i = filtered.findIndex((w) => keyOf(w) === key);
    if (i >= 0) { renderMore(i); openDetail(i); }
  }

  /* ---------- events ---------- */
  let qTimer;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { state.q = e.target.value.trim(); apply(); }, 220);
  });
  $("#subject").addEventListener("change", (e) => { state.subject = e.target.value; apply(); });
  $("#form").addEventListener("change", (e) => { state.form = e.target.value; apply(); });
  $("#order").addEventListener("change", (e) => { state.order = e.target.value; apply(); });

  document.addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (card) { openDetail(+card.dataset.i); return; }
    const bar = e.target.closest(".hist-bar");
    if (bar && !bar.disabled) { jumpToCentury(+bar.dataset.c); return; }
    const s = e.target.closest("[data-subject]");
    if (s) {
      $("#detail").close();
      state.subject = s.dataset.subject;
      $("#subject").value = state.subject;
      apply();
      $("#col-controls").scrollIntoView({ behavior: "smooth" });
      return;
    }
    if (e.target.closest("[data-close]")) $("#detail").close();
  });
  $("#detail").addEventListener("close", closeDetail);
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") e.target.close(); });
  $("#d-img").addEventListener("click", () => { const w = filtered[openIdx]; if (w) window.open(original(w), "_blank", "noopener"); });
  $("#d-prev").addEventListener("click", () => step(-1));
  $("#d-next").addEventListener("click", () => step(1));
  function step(dir) {
    const i = openIdx + dir;
    if (i < 0 || i >= filtered.length) return;
    if (i >= rendered) renderMore(i);
    openDetail(i);
  }
  document.addEventListener("keydown", (e) => {
    if (!$("#detail").open) {
      if (e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
      return;
    }
    if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
  });

  addEventListener("hashchange", () => {
    const raw = decodeURIComponent(location.hash.slice(1));
    if (KEY_RX.test(raw)) { openFromHash(); return; }
    if (openIdx >= 0) return;
    const jump = readHash();
    if (previewed && !state.q && !state.subject && !state.form && state.order === "asc") {
      // the preview already shows the start of the full, unfiltered list: just extend it
      filtered = all;
      $("#col-count").innerHTML = `Showing all <b>${all.length.toLocaleString()}</b>`;
      renderHistogram();
    } else {
      apply();
    }
    if (jump) requestAnimationFrame(() => jumpToCentury(jump));
  });

  new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && rendered < filtered.length) renderMore();
  }, { rootMargin: "1200px 0px" }).observe($("#col-sentinel"));

  // active century in histogram + progress bar
  const bar = $(".progress span");
  let ticking = false;
  addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      const max = document.documentElement.scrollHeight - innerHeight;
      bar.style.transform = `scaleX(${max > 0 ? scrollY / max : 0})`;
      const heads = $$(".century-head");
      let cur = null;
      for (const h of heads) { if (h.getBoundingClientRect().top < innerHeight * 0.4) cur = h.id.slice(8); else break; }
      $$(".hist-bar").forEach((b) => b.classList.toggle("active", b.dataset.c === cur));
      ticking = false;
    });
  }, { passive: true });

  load();
})();
