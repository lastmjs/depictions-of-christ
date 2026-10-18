/* The Face of Christ — interactive journey */
(() => {
  "use strict";

  const DATA = window.DEPICTIONS || { eras: [] };
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const ICON = {
    zoom: '<svg viewBox="0 0 24 24"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 5v16M9 7h6"/></svg>',
    diamond: '<svg viewBox="0 0 12 12"><path d="M6 0l6 6-6 6-6-6z" fill="currentColor"/></svg>',
    chiRho: '<svg viewBox="0 0 40 40" aria-hidden="true"><use href="#chi-rho"/></svg>',
  };

  /* ---------- flatten ---------- */
  const works = [];
  DATA.eras.forEach((era, ei) => {
    era.index = ei;
    era.entries.forEach((w) => { w.eraRef = era; works.push(w); });
  });
  works.forEach((w, i) => { w.n = i; });
  const withImages = works.filter((w) => w.image);

  /* ---------- hero ---------- */
  function renderHero() {
    const years = works.map((w) => w.year);
    const span = years.length ? Math.max(new Date().getFullYear() - Math.min(...years), 0) : 0;
    const stats = [
      [works.length, "Featured works"],
      [DATA.eras.length, "Eras"],
      ["…", "In the collection", "collection-count"],
      [`${Math.round(span / 100) * 100}`.replace(/\B(?=(\d{3})+$)/, ","), "Years"],
    ];
    $("#hero-stats").innerHTML = stats.map(([n, l, id]) => `<div><strong${id ? ` id="${id}"` : ""}>${n}</strong><span>${l}</span></div>`).join("");
    fetch("data/collection/index.json").then((r) => r.json()).then((m) => {
      $("#collection-count").textContent = m.total.toLocaleString();
      $("#hero-collection").textContent = `Explore all ${m.total.toLocaleString()} works`;
      $("#index-collection-link").textContent = `Browse all ${m.total.toLocaleString()} works in the complete collection →`;
    }).catch(() => { $("#collection-count").parentElement.remove(); });

    const preferred = ["sinai-christ-pantocrator", "deesis-hagia-sophia", "rublev-zvenigorod-savior", "antonello-salvator-mundi", "rembrandt-head-of-christ-philadelphia", "cefalu-pantocrator"];
    let picks = preferred.map((id) => works.find((w) => w.id === id && w.image)).filter(Boolean);
    if (picks.length < 4) {
      DATA.eras.forEach((era) => {
        const w = era.entries.find((e) => e.image && e.image.h >= e.image.w * 0.8 && !picks.includes(e));
        if (w) picks.push(w);
      });
    }
    picks = picks.slice(0, 6);
    const bg = $(".hero-bg");
    bg.innerHTML = picks.map((w) => `<img src="${w.image.src}" alt="" decoding="async">`).join("");
    const imgs = $$("img", bg);
    if (!imgs.length) return;
    let i = 0;
    imgs[0].classList.add("on");
    if (reduceMotion || imgs.length < 2) return;
    setInterval(() => {
      imgs[i].classList.remove("on");
      i = (i + 1) % imgs.length;
      imgs[i].classList.add("on");
    }, 7000);
  }

  /* ---------- journey ---------- */
  function eraCentury(era) {
    const y = era.entries.length ? era.entries[0].year : 1;
    return Math.max(1, Math.floor((y - 1) / 100) + 1);
  }

  function eraHTML(era) {
    const tiles = era.entries.filter((e) => e.image).slice(0, 12);
    const collage = tiles.length
      ? Array.from({ length: 18 }, (_, k) => `<div style="background-image:url('${tiles[k % tiles.length].image.thumb}')"></div>`).join("")
      : "";
    return `
      <section class="era" id="era-${esc(era.key)}" data-era="${era.index}" aria-labelledby="era-title-${era.index}">
        <div class="era-collage" aria-hidden="true">${collage}</div>
        <div class="era-inner reveal">
          <div class="era-numeral" aria-hidden="true">${esc(era.numeral)}</div>
          <div class="era-sub">${esc(era.subtitle)}</div>
          <h2 id="era-title-${era.index}">${esc(era.title)}</h2>
          <div class="era-span">${esc(era.span)}</div>
          <p class="era-intro">${esc(era.intro)}</p>
          <div class="ornament" aria-hidden="true">${ICON.diamond}</div>
          <p class="era-verse">“${esc(era.verse)}”<cite>${esc(era.verseRef)}</cite></p>
          <div class="era-more"><a class="btn-ghost" href="collection.html#century=${eraCentury(era)}">Explore every work from this period →</a></div>
        </div>
      </section>`;
  }

  function plateHTML(w, flip) {
    const img = w.image;
    const alt = `${w.title}${w.artist ? ", " + w.artist : ""}, ${w.date}`;
    const art = img
      ? `<figure class="frame" style="--ar:${(img.w / img.h).toFixed(4)}" tabindex="0" role="button" aria-label="View ${esc(w.title)} in full" data-open="${w.n}">
           <img class="frame-img" src="${img.src}" srcset="${img.thumb} 560w, ${img.src} ${img.w}w"
                sizes="(max-width: 960px) 100vw, 62vw" width="${img.w}" height="${img.h}"
                alt="${esc(alt)}" loading="lazy" decoding="async">
           <span class="frame-hint">${ICON.zoom}View in full</span>
         </figure>`
      : `<div class="veil">
           ${ICON.chiRho}
           <div class="veil-title">${esc(w.title)}</div>
           <p>${esc(w.copyrightNote || "This work is still protected by copyright, so it is not reproduced here. You can view it at its official source.")}</p>
           ${w.links && w.links[0] ? `<a class="btn-ghost" href="${esc(w.links[0].url)}" target="_blank" rel="noopener">View the work ${ICON.ext}</a>` : ""}
         </div>`;

    const meta = [
      ["Created", w.created],
      ["Now at", w.location],
      ["Medium", w.medium],
      ["Style", w.style],
    ].filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");

    const facts = (w.facts || []).length
      ? `<details class="facts" open><summary>Did you know</summary><ul>${w.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul></details>`
      : "";

    const links = (w.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ${ICON.ext}</a>`);
    if (img && img.page) links.push(`<a href="${esc(img.page)}" target="_blank" rel="noopener">Image source ${ICON.ext}</a>`);

    const credit = img
      ? `<p class="credit">Image: ${esc(img.credit || "Wikimedia Commons")} · ${img.licenseUrl ? `<a href="${esc(img.licenseUrl)}" target="_blank" rel="noopener">${esc(img.license)}</a>` : esc(img.license)} · via <a href="${esc(img.page)}" target="_blank" rel="noopener">Wikimedia Commons</a></p>`
      : "";

    return `
      <article class="plate${flip ? " flip" : ""}" id="${esc(w.id)}" data-n="${w.n}" style="--accent:${img ? img.accent : "#5a4528"}" aria-labelledby="t-${esc(w.id)}">
        <div class="art">${art}</div>
        <div class="info-wrap"><div class="info">
          <div class="info-date"><span class="yr${w.date.length > 14 ? " long" : ""}">${esc(w.date)}</span><span class="form">${esc(w.form || "")}</span></div>
          <h3 id="t-${esc(w.id)}">${esc(w.title)}</h3>
          <p class="info-artist">${esc(w.artist || "Unknown artist")}</p>
          <dl class="meta">${meta}</dl>
          ${w.scripture ? `<p class="scripture">${ICON.book}${esc(w.scripture)}</p>` : ""}
          <p class="summary">${esc(w.summary)}</p>
          ${facts}
          ${links.length ? `<div class="links" aria-label="Continue exploring">${links.join("")}</div>` : ""}
          ${credit}
        </div></div>
        <div class="plate-count" aria-hidden="true">${String(w.n + 1).padStart(2, "0")} / ${String(works.length).padStart(2, "0")}</div>
      </article>`;
  }

  function renderJourney() {
    let html = "";
    DATA.eras.forEach((era) => {
      html += eraHTML(era);
      era.entries.forEach((w, i) => { html += plateHTML(w, i % 2 === 1); });
    });
    $("#journey").innerHTML = html;
    $$(".frame-img").forEach((img) => {
      if (img.complete && img.naturalWidth) img.classList.add("loaded");
      else img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
    });
  }

  /* ---------- timeline rail ---------- */
  function renderRail() {
    const track = $("#rail-track");
    if (!works.length) return;
    const n = works.length;
    const pos = (i) => (n === 1 ? 50 : 3 + (i / (n - 1)) * 94);
    // place century labels where that year falls between neighbouring works
    const yearPos = (y) => {
      if (y <= works[0].year) return pos(0);
      for (let i = 1; i < n; i++) {
        if (works[i].year >= y) {
          const a = works[i - 1], b = works[i];
          const t = b.year === a.year ? 0 : (y - a.year) / (b.year - a.year);
          return pos(i - 1) + t * (pos(i) - pos(i - 1));
        }
      }
      return pos(n - 1);
    };
    let html = '<div class="rail-fill"></div>';
    let last = -100;
    for (let c = 200; c <= 2000; c += 100) {
      if (c < works[0].year || c > works[n - 1].year) continue;
      const p = yearPos(c);
      if (p - last < 5) continue;
      last = p;
      html += `<span class="rail-century" style="top:${p}%">${c}</span>`;
    }
    works.forEach((w, i) => {
      html += `<button class="rail-dot" style="top:${pos(i)}%" data-go="${esc(w.id)}" aria-label="${esc(w.date)}: ${esc(w.title)}"><span class="tip"><b>${esc(w.date)}</b>${esc(w.title)}</span></button>`;
    });
    track.innerHTML = html;

    // On narrow screens the rail is horizontal: convert top → left.
    const mq = matchMedia("(max-width: 960px)");
    const orient = () => {
      $$(".rail-dot, .rail-century", track).forEach((el) => {
        const v = el.style.top || el.style.left;
        if (mq.matches) { el.style.left = v; el.style.top = "50%"; el.dataset.p = v; }
        else { el.style.top = el.dataset.p || v; el.style.left = ""; }
      });
      $$(".rail-dot", track).forEach((el) => { if (!el.dataset.p) el.dataset.p = el.style.top; });
    };
    $$(".rail-dot, .rail-century", track).forEach((el) => { el.dataset.p = el.style.top; });
    orient();
    mq.addEventListener("change", orient);
    railPos = pos;
  }
  let railPos = () => 0;

  /* ---------- scrolling state ---------- */
  let current = -1;
  let suppressHash = false;
  let currentEra = -1;

  function setActive(n) {
    if (n === current) return;
    current = n;
    const w = works[n];
    $$(".rail-dot").forEach((d, i) => {
      d.classList.toggle("active", i === n);
      d.classList.toggle("seen", i < n);
    });
    const fill = $(".rail-fill");
    if (fill) {
      const horizontal = matchMedia("(max-width: 960px)").matches;
      fill.style.height = horizontal ? "2px" : railPos(n) + "%";
      fill.style.width = horizontal ? railPos(n) + "%" : "2px";
    }
    $(".rail-year").textContent = w.date.length <= 12 ? w.date : `c. ${w.year}`;
    $(".rail-label").textContent = w.eraRef.subtitle;
    setEra(w.eraRef.index);
    if (!suppressHash && history.replaceState && location.hash !== "#" + w.id) history.replaceState(null, "", "#" + w.id);
  }

  function setEra(i) {
    if (i === currentEra) return;
    currentEra = i;
    const era = DATA.eras[i];
    const el = $(".topbar-era");
    if (!era) { el.classList.remove("show"); return; }
    $(".topbar-numeral").textContent = era.numeral;
    $(".topbar-title").textContent = era.title;
    el.classList.add("show");
  }

  function observe() {
    const reveal = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); reveal.unobserve(e.target); } });
    }, { threshold: 0, rootMargin: "0px 0px -5% 0px" });
    $$(".plate, .era").forEach((el) => reveal.observe(el));

    const active = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const t = e.target;
        if (t.classList.contains("plate")) setActive(+t.dataset.n);
        else if (t.classList.contains("era")) setEra(+t.dataset.era);
      });
    }, { rootMargin: "-48% 0px -48% 0px" });
    $$(".plate, .era").forEach((el) => active.observe(el));

    const hero = $(".hero");
    const chrome = new IntersectionObserver(([e]) => {
      const past = !e.isIntersecting;
      $("#topbar").classList.toggle("solid", past);
      $("#rail").classList.toggle("show", past);
      if (!past) { $(".topbar-era").classList.remove("show"); currentEra = -1; }
      if (past) showKeysHint();
    }, { threshold: 0.15 });
    chrome.observe(hero);

    const epilogue = $(".epilogue");
    new IntersectionObserver(([e]) => {
      $("#rail").classList.toggle("show", !e.isIntersecting && window.scrollY > hero.offsetHeight * .5);
    }, { threshold: 0.35 }).observe(epilogue);

    const bar = $(".progress span");
    let ticking = false;
    addEventListener("scroll", () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const max = document.documentElement.scrollHeight - innerHeight;
        bar.style.transform = `scaleX(${max > 0 ? scrollY / max : 0})`;
        ticking = false;
      });
    }, { passive: true });
  }

  let hintShown = false;
  function showKeysHint() {
    if (hintShown || matchMedia("(hover: none)").matches) return;
    hintShown = true;
    const h = $("#keys-hint");
    h.classList.add("show");
    setTimeout(() => h.classList.remove("show"), 5200);
  }

  function goTo(id) {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }

  /* ---------- index dialog ---------- */
  function renderIndex() {
    $("#index-count").textContent = `${works.length} hand-picked`;
    const forms = {};
    works.forEach((w) => { if (w.form) forms[w.form] = (forms[w.form] || 0) + 1; });
    const formList = Object.keys(forms).sort((a, b) => forms[b] - forms[a]);
    $("#index-filters").innerHTML =
      `<button class="chip" aria-pressed="true" data-form="">All<small>${works.length}</small></button>` +
      formList.map((f) => `<button class="chip" aria-pressed="false" data-form="${esc(f)}">${esc(f)}<small>${forms[f]}</small></button>`).join("");

    $("#index-body").innerHTML = DATA.eras.map((era) => `
      <section class="index-era">
        <h3><span>${esc(era.numeral)}</span>${esc(era.title)}<em>${esc(era.span)}</em></h3>
        <div class="index-grid">
          ${era.entries.map((w) => `
            <button class="index-item" data-go="${esc(w.id)}" data-form="${esc(w.form || "")}">
              <span class="index-thumb">${w.image ? `<img src="${w.image.thumb}" alt="" loading="lazy">` : ICON.chiRho}</span>
              <b>${esc(w.date)}</b>
              <span>${esc(w.title)}</span>
            </button>`).join("")}
        </div>
      </section>`).join("");

    $("#index-filters").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      $$(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      const f = chip.dataset.form;
      $$(".index-item").forEach((it) => { it.hidden = !!f && it.dataset.form !== f; });
      $$(".index-era").forEach((s) => { s.hidden = !$$(".index-item", s).some((it) => !it.hidden); });
    });
  }

  function openIndex() {
    const d = $("#index");
    d.showModal();
    const cur = works[current];
    if (cur) {
      const it = $(`.index-item[data-go="${CSS.escape(cur.id)}"]`, d);
      if (it && !it.hidden) it.scrollIntoView({ block: "center" });
    }
  }

  /* ---------- lightbox ---------- */
  const lb = { el: null, img: null, stage: null, n: 0, scale: 1, x: 0, y: 0, base: null };

  function openLightbox(n) {
    const w = works[n];
    if (!w || !w.image) return;
    lb.n = n;
    lb.img.src = w.image.src;
    lb.img.alt = w.title;
    $("#lb-title").textContent = w.title;
    $("#lb-sub").textContent = [w.artist, w.date, w.location].filter(Boolean).join(" · ");
    $("#lb-original").href = w.image.original;
    resetZoom();
    if (!lb.el.open) lb.el.showModal();
  }

  function stepLightbox(dir) {
    const idx = withImages.findIndex((w) => w.n === lb.n);
    const next = withImages[(idx + dir + withImages.length) % withImages.length];
    if (next) { openLightbox(next.n); goTo(next.id); }
  }

  function applyZoom() {
    lb.img.style.transform = `translate(${lb.x}px, ${lb.y}px) scale(${lb.scale})`;
  }
  function resetZoom() {
    lb.scale = 1; lb.x = 0; lb.y = 0;
    lb.img.style.transformOrigin = "50% 50%";
    applyZoom();
  }
  function zoomAt(factor, cx, cy) {
    const rect = lb.stage.getBoundingClientRect();
    const ox = (cx ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
    const oy = (cy ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
    const ns = Math.min(8, Math.max(1, lb.scale * factor));
    const k = ns / lb.scale;
    lb.x = ox - (ox - lb.x) * k;
    lb.y = oy - (oy - lb.y) * k;
    lb.scale = ns;
    if (ns === 1) { lb.x = 0; lb.y = 0; }
    applyZoom();
  }

  function initLightbox() {
    lb.el = $("#lightbox");
    lb.img = $("#lb-img");
    lb.stage = $("#lb-stage");

    lb.stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX, e.clientY);
    }, { passive: false });
    lb.stage.addEventListener("dblclick", (e) => {
      if (lb.scale > 1) resetZoom(); else zoomAt(2.5, e.clientX, e.clientY);
    });

    const pointers = new Map();
    let last = null, pinch = null, moved = false;
    lb.stage.addEventListener("pointerdown", (e) => {
      lb.stage.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, e);
      last = { x: e.clientX, y: e.clientY };
      moved = false;
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) };
      }
      lb.stage.classList.add("dragging");
    });
    lb.stage.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, e);
      if (pointers.size === 2 && pinch) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        zoomAt(d / pinch.d, (a.clientX + b.clientX) / 2, (a.clientY + b.clientY) / 2);
        pinch.d = d;
        moved = true;
        return;
      }
      if (last && lb.scale > 1) {
        lb.x += e.clientX - last.x;
        lb.y += e.clientY - last.y;
        applyZoom();
      }
      if (last && Math.hypot(e.clientX - last.x, e.clientY - last.y) > 2) moved = true;
      last = { x: e.clientX, y: e.clientY };
    });
    const end = (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (!pointers.size) {
        lb.stage.classList.remove("dragging");
        // tap on the empty backdrop closes when not zoomed
        if (!moved && lb.scale === 1 && e.target === lb.stage) lb.el.close();
        last = null;
      }
    };
    lb.stage.addEventListener("pointerup", end);
    lb.stage.addEventListener("pointercancel", end);

    // swipe to navigate on touch when not zoomed
    let sx = null;
    lb.stage.addEventListener("touchstart", (e) => { sx = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
    lb.stage.addEventListener("touchend", (e) => {
      if (sx == null || lb.scale > 1) return;
      const dx = e.changedTouches[0].clientX - sx;
      if (Math.abs(dx) > 70) stepLightbox(dx < 0 ? 1 : -1);
      sx = null;
    });

    $("#lb-zoom-in").addEventListener("click", () => zoomAt(1.5));
    $("#lb-zoom-out").addEventListener("click", () => zoomAt(1 / 1.5));
    $("#lb-prev").addEventListener("click", () => stepLightbox(-1));
    $("#lb-next").addEventListener("click", () => stepLightbox(1));
  }

  /* ---------- events ---------- */
  function bind() {
    document.addEventListener("click", (e) => {
      const open = e.target.closest("[data-open]");
      if (open) { openLightbox(+open.dataset.open); return; }
      const go = e.target.closest("[data-go]");
      if (go) {
        e.preventDefault();
        const d = go.closest("dialog");
        if (d) d.close();
        goTo(go.dataset.go);
        return;
      }
      const close = e.target.closest("[data-close]");
      if (close) close.closest("dialog").close();
    });
    $("#index").addEventListener("click", (e) => { if (e.target.id === "index") e.target.close(); });
    $("#open-index").addEventListener("click", openIndex);
    $("#open-index-2").addEventListener("click", openIndex);

    const sections = () => $$(".hero, .era, .plate, .epilogue");
    document.addEventListener("keydown", (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (lb.el.open) {
        if (e.key === "ArrowRight") stepLightbox(1);
        else if (e.key === "ArrowLeft") stepLightbox(-1);
        else if (e.key === "+" || e.key === "=") zoomAt(1.4);
        else if (e.key === "-") zoomAt(1 / 1.4);
        return;
      }
      if ($("#index").open) return;
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      const k = e.key;
      if (k === "ArrowDown" || k === "j" || k === "ArrowUp" || k === "k") {
        e.preventDefault();
        const list = sections();
        const mid = innerHeight * 0.35;
        let idx = list.findIndex((s) => s.getBoundingClientRect().bottom > mid);
        if (idx < 0) idx = list.length - 1;
        const dir = k === "ArrowDown" || k === "j" ? 1 : -1;
        const top = list[idx].getBoundingClientRect().top;
        let target = idx + dir;
        if (dir < 0 && top < -40) target = idx; // snap back to the start of the current section first
        target = Math.max(0, Math.min(list.length - 1, target));
        list[target].scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      } else if (k === "Enter" && !e.target.closest("a, button, summary")) {
        if (works[current]?.image) openLightbox(current);
      } else if (k === "Enter" && e.target.matches(".frame")) {
        openLightbox(+e.target.dataset.open);
      } else if (k === "i" || k === "I") {
        openIndex();
      }
    });
  }

  /* ---------- boot ---------- */
  renderHero();
  renderJourney();
  renderRail();
  renderIndex();
  initLightbox();
  bind();
  observe();

  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  if (location.hash.length > 1) {
    const id = decodeURIComponent(location.hash.slice(1));
    const el = document.getElementById(id);
    if (el) {
      suppressHash = true;
      const root = document.documentElement;
      root.style.scrollBehavior = "auto";
      el.scrollIntoView({ block: "start" });
      requestAnimationFrame(() => {
        el.scrollIntoView({ block: "start" });
        root.style.scrollBehavior = "";
        setTimeout(() => { suppressHash = false; }, 800);
      });
    }
  }
})();
