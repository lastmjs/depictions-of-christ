/* The Map — every work in the collection, placed as close as possible to where it was made */
(() => {
  "use strict";

  const { $, $$, esc, ICON_CHECK, keyOf, loadInto, centuryName, ordinal, centuryOf, createViewer } = window.FOC;

  const GOLD = "#d6b06a";
  const GOLD_BRIGHT = "#f0d49a";
  let counts = {};           // place index -> {century or "u": number of works}
  let places = [];
  const shardCache = new Map(); // works of each place, loaded on demand
  let century = "all";       // "all" | number | "undated"
  let openPlace = null;
  let panelList = [];
  let map;

  const viewer = createViewer({
    page: "map",
    onSubject: (s) => { location.href = `collection.html#subject=${encodeURIComponent(s)}`; },
  });

  /* ---------- filtering ---------- */
  const countFor = (cn) => century === "all" ? Object.values(cn).reduce((a, b) => a + b, 0)
    : (cn[century === "undated" ? "u" : String(century)] || 0);
  const matches = (w) => century === "all" || (century === "undated" ? w.c_ == null : w.c_ === century);

  function features() {
    const out = [];
    Object.entries(counts).forEach(([ps, cn]) => {
      const p = +ps;
      if (!places[p]) return; // ignore any reference to a place that is not in places.json
      const n = countFor(cn);
      if (!n) return;
      const [name, lat, lon, prec] = places[p];
      const approx = prec === "artist" || prec === "region" ? 1 : 0;
      out.push({ type: "Feature", id: p, geometry: { type: "Point", coordinates: [lon, lat] }, properties: { p, n, name, approx } });
    });
    return { type: "FeatureCollection", features: out };
  }

  function updateStats(fc) {
    const works = fc.features.reduce((k, f) => k + f.properties.n, 0);
    const label = century === "all" ? "across all centuries" : century === "undated" ? "with no known date" : `from the ${ordinal(century)} century`;
    $("#map-stats").innerHTML = `<strong>${works.toLocaleString()}</strong> works in <strong>${fc.features.length.toLocaleString()}</strong> places, ${label}`;
  }

  function refresh() {
    const fc = features();
    map.getSource("places")?.setData(fc);
    updateStats(fc);
    $$(".cb-chip").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.c === String(century))));
    if (openPlace != null) showPlace(openPlace, false);
    writeHash();
  }

  /* ---------- century bar ---------- */
  function renderCenturyBar() {
    const byCentury = new Map();
    let undated = 0, located = 0;
    Object.values(counts).forEach((cn) => Object.entries(cn).forEach(([c, n]) => {
      located += n;
      if (c === "u") undated += n;
      else byCentury.set(+c, (byCentury.get(+c) || 0) + n);
    }));
    const cs = [...byCentury.keys()].sort((a, b) => a - b);
    const chip = (c, label, n, title) =>
      `<button class="cb-chip" data-c="${c}" aria-pressed="false" title="${esc(title)}"><b>${label}</b><small>${n.toLocaleString()}</small></button>`;
    const first = byCentury.has(1) ? "" :
      `<button class="cb-chip" data-c="1" aria-pressed="false" disabled title="No depictions of Christ survive from the 1st century; the earliest date from c. AD 200–250."><b>1st</b><small>none</small></button>`;
    $("#cb-track").innerHTML =
      chip("all", "All", located, "All centuries") + first +
      cs.map((c) => chip(c, c > 0 ? ordinal(c) : "BC", byCentury.get(c), centuryName(c))).join("") +
      (undated ? chip("undated", "Undated", undated, "Date unknown") : "");
  }

  $("#cb-track").addEventListener("click", (e) => {
    const b = e.target.closest(".cb-chip");
    if (!b) return;
    century = b.dataset.c === "all" || b.dataset.c === "undated" ? b.dataset.c : +b.dataset.c;
    b.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    refresh();
  });

  /* ---------- place panel ---------- */
  function placeWorks(p) {
    const shard = (p % 256).toString(16).padStart(2, "0");
    if (!shardCache.has(shard)) {
      shardCache.set(shard, fetch(`data/map/byplace/${shard}.json`).then((r) => r.json()).then((d) => {
        Object.values(d).forEach((list) => list.forEach((w) => { w.c_ = centuryOf(w.y); }));
        return d;
      }).catch(() => ({})));
    }
    return shardCache.get(shard).then((d) => d[p] || []);
  }

  async function showPlace(p, fly = true) {
    openPlace = p;
    const [name, lat, lon] = places[p];
    $("#pp-title").textContent = name;
    $("#pp-sub").textContent = "";
    $("#pp-body").innerHTML = `<p class="pp-empty"><span class="spinner" aria-hidden="true"></span> Loading…</p>`;
    $("#place-panel").hidden = false;
    document.body.classList.add("panel-open");
    if (fly) map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 5.5), speed: 0.9, padding: panelPadding() });
    const works = await placeWorks(p);
    if (openPlace !== p) return;
    panelList = works.filter(matches).sort((a, b) => (a.y ?? 1e9) - (b.y ?? 1e9));
    const total = works.length;
    $("#pp-eyebrow").textContent = century === "all" ? "All centuries" : century === "undated" ? "Date unknown" : centuryName(century);
    $("#pp-title").textContent = name;
    const prec = places[p][3];
    const PREC = {
      made: "Recorded as the place where these works were made.",
      "in situ": "These works are still in the place where they were made.",
      artist: "Approximate: the artists' documented place of work.",
      region: "Approximate: only the region or school of origin is known.",
    };
    $("#pp-prec").textContent = PREC[prec] || "";
    $("#pp-sub").textContent = panelList.length === total
      ? `${total.toLocaleString()} ${total === 1 ? "work" : "works"} made here`
      : `${panelList.length.toLocaleString()} of ${total.toLocaleString()} works made here`;
    const body = $("#pp-body");
    body.innerHTML = panelList.length
      ? `<div class="pp-grid">${panelList.map((w, i) => `
          <button class="pcard" data-i="${i}" aria-label="${esc(w.t)}${w.d ? ", " + esc(w.d) : ""}">
            <span class="pcard-img"><img alt="" loading="lazy" decoding="async">${w.v ? `<span class="card-check" title="Hand-checked">${ICON_CHECK}</span>` : ""}</span>
            <b>${esc(w.d || "Undated")}</b><span>${esc(w.t)}</span>
          </button>`).join("")}</div>`
      : `<p class="pp-empty">No works here from this period. Choose “All” to see everything held here.</p>`;
    $$(".pcard img", body).forEach((img, i) => loadInto(img, panelList[i], [330, 500]));
    body.scrollTop = 0;
    writeHash();
  }

  function closePlace() {
    openPlace = null;
    $("#place-panel").hidden = true;
    document.body.classList.remove("panel-open");
    writeHash();
  }

  const panelPadding = () => (innerWidth > 860 ? { right: 420, left: 0, top: 0, bottom: 0 } : { bottom: innerHeight * 0.5, top: 0, left: 0, right: 0 });

  $("#pp-close").addEventListener("click", closePlace);
  $("#pp-body").addEventListener("click", (e) => {
    const c = e.target.closest(".pcard");
    if (c) viewer.open(panelList, +c.dataset.i);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !viewer.isOpen && openPlace != null) closePlace();
  });

  /* ---------- hash ---------- */
  function writeHash() {
    const p = new URLSearchParams();
    if (century !== "all") p.set("c", century);
    if (openPlace != null) p.set("place", openPlace);
    const h = p.toString();
    history.replaceState(null, "", h ? `#${h}` : location.pathname + location.search);
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const c = p.get("c");
    if (c) century = c === "undated" ? "undated" : +c;
    const pl = p.get("place");
    return pl != null && places[+pl] ? +pl : null;
  }

  /* ---------- map ---------- */
  function warmTheStyle() {
    // Give the dark basemap a warm, candle-lit palette to match the site.
    const set = (id, prop, val) => { if (map.getLayer(id)) try { map.setPaintProperty(id, prop, val); } catch (e) { /* ignore */ } };
    set("background", "background-color", "#0e0b08");
    set("water", "fill-color", "#070605");
    map.getStyle().layers.forEach((l) => {
      if (l.type === "symbol" && l.paint && "text-color" in (l.paint || {})) set(l.id, "text-color", "#8a7e6a");
      if (l.type === "symbol") set(l.id, "text-halo-color", "#0b0907");
      if (l.id.startsWith("boundary")) set(l.id, "line-color", "#3a3024");
      if (l.type === "line" && /road|highway|street|path|rail/.test(l.id)) set(l.id, "line-color", "#211b14");
      if (l.type === "fill" && /landuse|landcover|park|wood|building/.test(l.id)) set(l.id, "fill-color", "#15110c");
    });
  }

  function addLayers() {
    map.addSource("places", { type: "geojson", data: features(), cluster: true, clusterRadius: 46, clusterMaxZoom: 9, clusterProperties: { sum: ["+", ["get", "n"]] } });

    map.addLayer({ id: "cluster-glow", type: "circle", source: "places", filter: ["has", "point_count"], paint: {
      "circle-color": GOLD, "circle-opacity": 0.18, "circle-blur": 0.9,
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "sum"]], 1, 18, 10, 30, 60, 62, 200, 96],
    } });
    map.addLayer({ id: "clusters", type: "circle", source: "places", filter: ["has", "point_count"], paint: {
      "circle-color": ["interpolate", ["linear"], ["sqrt", ["get", "sum"]], 1, "#9b7638", 20, GOLD, 80, GOLD_BRIGHT],
      "circle-opacity": 0.9, "circle-stroke-color": "#fff3d6", "circle-stroke-width": 1, "circle-stroke-opacity": 0.5,
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "sum"]], 1, 9, 10, 15, 60, 30, 200, 46],
    } });
    map.addLayer({ id: "cluster-count", type: "symbol", source: "places", filter: ["has", "point_count"], layout: {
      "text-field": ["case", [">=", ["get", "sum"], 1000],
        ["concat", ["to-string", ["/", ["round", ["/", ["get", "sum"], 100]], 10]], "k"],
        ["to-string", ["get", "sum"]]],
      "text-font": ["Noto Sans Regular"], "text-size": 12, "text-allow-overlap": true,
    }, paint: { "text-color": "#1a1309" } });

    map.addLayer({ id: "place-glow", type: "circle", source: "places", filter: ["all", ["!", ["has", "point_count"]], ["==", ["get", "approx"], 0]], paint: {
      "circle-color": GOLD, "circle-opacity": 0.22, "circle-blur": 0.8,
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 10, 10, 22, 40, 44],
    } });
    map.addLayer({ id: "place", type: "circle", source: "places", filter: ["!", ["has", "point_count"]], paint: {
      "circle-color": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, GOLD, 10, GOLD_BRIGHT],
      "circle-opacity": ["case", ["==", ["get", "approx"], 1], 0.16, 1],
      "circle-stroke-color": ["case", ["==", ["get", "approx"], 1], GOLD, "#fff3d6"],
      "circle-stroke-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.4, ["==", ["get", "approx"], 1], 1.5, 0.8],
      "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 4.5, 10, 10, 40, 22],
    } });
    map.addLayer({ id: "place-label", type: "symbol", source: "places", filter: ["!", ["has", "point_count"]], minzoom: 6, layout: {
      "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 12,
      "text-offset": [0, 1.4], "text-anchor": "top", "text-max-width": 12, "text-optional": true,
    }, paint: { "text-color": "#f1e8d7", "text-halo-color": "#0b0907", "text-halo-width": 1.4 } });

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "map-tip", offset: 12 });
    let hovered = null;
    map.on("mousemove", "place", (e) => {
      const f = e.features[0];
      map.getCanvas().style.cursor = "pointer";
      if (hovered != null) map.setFeatureState({ source: "places", id: hovered }, { hover: false });
      hovered = f.id;
      map.setFeatureState({ source: "places", id: hovered }, { hover: true });
      popup.setLngLat(f.geometry.coordinates).setHTML(`<b>${esc(f.properties.name)}</b><span>${f.properties.n.toLocaleString()} ${f.properties.n === 1 ? "work" : "works"} made here${f.properties.approx ? " (approximate)" : ""}</span>`).addTo(map);
    });
    map.on("mouseleave", "place", () => {
      map.getCanvas().style.cursor = "";
      if (hovered != null) map.setFeatureState({ source: "places", id: hovered }, { hover: false });
      hovered = null;
      popup.remove();
    });
    map.on("mouseenter", "clusters", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "clusters", () => { map.getCanvas().style.cursor = ""; });
    map.on("click", "clusters", async (e) => {
      const f = e.features[0];
      const zoom = await map.getSource("places").getClusterExpansionZoom(f.properties.cluster_id);
      map.easeTo({ center: f.geometry.coordinates, zoom: Math.min(zoom + 0.3, 14), duration: 700 });
    });
    map.on("click", "place", (e) => { popup.remove(); showPlace(e.features[0].properties.p); });
  }

  function initMap(startPlace) {
    map = new maplibregl.Map({
      container: "map",
      style: "https://tiles.openfreemap.org/styles/dark",
      center: [18, 38],
      zoom: innerWidth < 700 ? 1.3 : 2.1,
      attributionControl: { compact: true },
      maxPitch: 60,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.on("style.load", () => {
      try { map.setProjection({ type: "globe" }); } catch (e) { /* older browsers: flat map */ }
      try {
        map.setSky({ "sky-color": "#0b0907", "horizon-color": "#2a2118", "fog-color": "#0b0907", "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 0.9, 6, 0] });
      } catch (e) { /* optional */ }
      warmTheStyle();
      addLayers();
      updateStats(features());
      if (startPlace != null) showPlace(startPlace);
    });
  }

  /* ---------- boot ---------- */
  (async () => {
    try {
      const pts = await (await fetch("data/map/points.json")).json();
      places = pts.places;
      counts = pts.counts;
    } catch (e) {
      $("#map-stats").textContent = "The map data could not be loaded.";
      return;
    }
    if (places.length && places[0].length < 4) {
      // Interim data: places are where the works are held today, not yet where they were made.
      $(".map-legend .eyebrow").textContent = "Where they are held today";
      $(".map-hint").textContent = "We are moving every point to where the work was made; for now each light is the museum, church or monastery that holds it. Click one to see its works.";
      $(".map-key").hidden = true;
    }
    const start = readHash();
    renderCenturyBar();
    $$(".cb-chip").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.c === String(century))));
    updateStats(features());
    initMap(start);
  })();
})();
