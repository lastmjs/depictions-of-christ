/* The Complete Collection — chronological archive of every catalogued depiction */
(() => {
  "use strict";

  const { $, $$, esc, ICON_CHECK, keyOf, KEY_RX, loadInto, centuryName, centurySpan, loadCollection, createViewer } = window.FOC;
  const BATCH = 120;

  /* ---------- state ---------- */
  let all = [];
  let filtered = [];
  let rendered = 0;
  let index = null;
  let previewed = false;
  let lastCentury;
  let currentWall = null;
  const countByCentury = new Map();
  const state = { q: "", subject: "", form: "", order: "asc" };

  const viewer = createViewer({
    onOpen: (w, i) => { if (i >= rendered) renderMore(i); history.replaceState(null, "", `#${keyOf(w)}`); },
    onClose: () => writeHash(),
    onSubject: (s) => {
      state.subject = s;
      $("#subject").value = s;
      apply();
      $("#col-controls").scrollIntoView({ behavior: "smooth" });
    },
  });

  /* ---------- load ---------- */
  async function load() {
    let data;
    try {
      data = await loadCollection((ready) => {
        // show the earliest centuries as soon as they arrive (unfiltered view only)
        if (previewed || location.hash || ready.length < 150) return;
        previewed = true;
        all = ready;
        all.forEach((w, n) => { w.n = n; });
        filtered = all.slice();
        countByCentury.clear();
        filtered.forEach((w) => countByCentury.set(w.c_, (countByCentury.get(w.c_) || 0) + 1));
        resetResults();
        renderMore();
      });
    } catch (e) {
      $("#col-total").textContent = "The collection could not be loaded.";
      return;
    }
    index = data.index;
    all = data.all;
    $("#col-total").innerHTML = `<strong>${all.length.toLocaleString()}</strong> works of art` +
      (index.checked ? `<span class="col-checked">${index.checked.toLocaleString()} hand-checked so far</span>` : "");

    const subj = $("#subject");
    index.subjects.forEach(([s, n]) => subj.insertAdjacentHTML("beforeend", `<option value="${esc(s)}">${esc(s)} (${n.toLocaleString()})</option>`));
    const form = $("#form");
    index.types.filter(([, n]) => n >= 5).forEach(([t, n]) => form.insertAdjacentHTML("beforeend", `<option value="${esc(t)}">${esc(t[0].toUpperCase() + t.slice(1))} (${n.toLocaleString()})</option>`));

    restoreFromHash(true);
  }

  function isUnfiltered() {
    return !state.q && !state.subject && !state.form && state.order === "asc";
  }

  function restoreFromHash(initial) {
    const raw = decodeURIComponent(location.hash.slice(1));
    if (KEY_RX.test(raw)) {
      if (initial) { if (previewed) extendPreview(); else apply(); }
      const i = filtered.findIndex((w) => keyOf(w) === raw);
      if (i >= 0) viewer.open(filtered, i);
      return;
    }
    const jump = readHash();
    if (previewed && isUnfiltered() && initial) extendPreview();
    else apply();
    if (jump) requestAnimationFrame(() => jumpToCentury(jump));
  }

  // The preview already shows the start of the full, unfiltered list: just extend it.
  function extendPreview() {
    filtered = all;
    $("#col-count").innerHTML = `Showing all <b>${all.length.toLocaleString()}</b>`;
    renderHistogram();
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
      filtered.sort((a, b) => (a.y == null) - (b.y == null)); // undated works last either way
    }
    $("#col-count").innerHTML = filtered.length === all.length
      ? `Showing all <b>${all.length.toLocaleString()}</b>`
      : `<b>${filtered.length.toLocaleString()}</b> of ${all.length.toLocaleString()} works`;
    $("#col-empty").hidden = filtered.length > 0;
    renderHistogram();
    resetResults();
    renderMore();
    writeHash();
  }

  function resetResults() {
    $("#col-results").innerHTML = "";
    rendered = 0;
    lastCentury = undefined;
  }

  // The first century has no surviving depictions; say so where it would appear in the timeline.
  const FIRST_CENTURY_NOTE = `
    <section class="century century-empty">
      <header class="century-head" id="century-1">
        <h2>The 1st Century</h2>
        <span class="span">AD 1 – 100</span>
        <span class="n">no surviving works</span>
      </header>
      <div class="empty-note">
        <p>No image of Christ survives from the century in which he lived, died and rose again. The first believers
        worshipped in homes, and the earliest pictures we know were made around AD 200–250, in Rome's catacombs and
        in the house church at Dura-Europos on the Euphrates.</p>
        <p>Later centuries treasured images believed to come from his own time: the cloth sent to King Abgar of Edessa,
        Veronica's veil, icons said to have been painted by St Luke, the crucifixes said to have been carved by
        Nicodemus, and the Shroud of Turin. Each appears in this collection at the date to which scholars assign it,
        with its tradition told in its description.</p>
      </div>
    </section>`;

  function renderMore(until) {
    if (rendered === 0 && isUnfiltered() && filtered.length && filtered[0].c_ > 1) {
      $("#col-results").insertAdjacentHTML("beforeend", FIRST_CENTURY_NOTE);
    }
    const target = Math.min(filtered.length, until != null ? Math.max(until + 1, rendered + BATCH) : rendered + BATCH);
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
        <div class="card-img"><img alt="" loading="lazy" decoding="async">${w.v ? `<span class="card-check" title="Hand-checked">${ICON_CHECK}</span>` : ""}</div>
        <div class="card-meta"><b>${esc(w.d || "Undated")}</b><span>${esc(w.t)}</span><small>${esc(w.a || w.c || w.l || "")}</small></div>`;
      card.setAttribute("aria-label", `${w.t}${w.a ? ", " + w.a : ""}${w.d ? ", " + w.d : ""}`);
      loadInto($("img", card), w, [330, 500]);
      currentWall.appendChild(card);
    }
    $("#col-results").appendChild(frag);
    requestAnimationFrame(() => {
      const r = $("#col-sentinel").getBoundingClientRect();
      if (r.top < innerHeight + 1200 && rendered < filtered.length && until == null) renderMore();
    });
  }

  function renderHistogram() {
    countByCentury.clear();
    filtered.forEach((w) => countByCentury.set(w.c_, (countByCentury.get(w.c_) || 0) + 1));
    const cs = [...new Set(all.map((w) => w.c_).filter((c) => c != null && c > 0))].sort((a, b) => a - b);
    if (!cs.length) return;
    const full = [];
    for (let c = Math.min(1, cs[0]); c <= cs[cs.length - 1]; c++) full.push(c);
    const max = Math.max(1, ...full.map((c) => countByCentury.get(c) || 0));
    $("#histogram").innerHTML = full.map((c) => {
      const n = countByCentury.get(c) || 0;
      const h = n ? Math.max(3, Math.sqrt(n / max) * 100) : 2;
      const note = c === 1 && !n && isUnfiltered();
      return `<button class="hist-bar${n ? "" : " empty"}" data-c="${c}" title="${esc(centuryName(c))}: ${note ? "no surviving works — read why" : `${n.toLocaleString()} works`}" ${n || note ? "" : "disabled"}>
        <i style="height:${h}%"></i><span>${c > 0 ? (c - 1) * 100 : ""}</span></button>`;
    }).join("");
  }

  function jumpToCentury(c) {
    if (c === 1 && document.getElementById("century-1") && !filtered.some((w) => w.c_ === 1)) {
      document.getElementById("century-1").scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
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

  /* ---------- hash state ---------- */
  function writeHash() {
    if (viewer.isOpen) return;
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
    if (!raw) return null;
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
    if (card) { viewer.open(filtered, +card.dataset.i); return; }
    const bar = e.target.closest(".hist-bar");
    if (bar && !bar.disabled) jumpToCentury(+bar.dataset.c);
  });
  document.addEventListener("keydown", (e) => {
    if (!viewer.isOpen && e.key === "/" && document.activeElement !== $("#q")) { e.preventDefault(); $("#q").focus(); }
  });
  addEventListener("hashchange", () => { if (all.length && !viewer.isOpen) restoreFromHash(false); });

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
      let cur = null;
      for (const h of $$(".century-head")) { if (h.getBoundingClientRect().top < innerHeight * 0.4) cur = h.id.slice(8); else break; }
      $$(".hist-bar").forEach((b) => b.classList.toggle("active", b.dataset.c === cur));
      ticking = false;
    });
  }, { passive: true });

  load();
})();
