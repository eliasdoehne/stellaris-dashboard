/* Galaxy map renderer (deck.gl, OrthographicView).
 *
 * Replaces the old Plotly/Dash galaxy tab. Static geometry (system positions,
 * names, territory polygons, hyperlane segments) is fetched once; per-date
 * ownership colors + border ridges are fetched on date-slider changes and
 * recolor the existing geometry client-side. See galaxy_map.py for the endpoints.
 */
(function () {
  "use strict";

  const root = document.querySelector(".galaxy-page");
  if (!root || !window.deck) return;

  const cfg = JSON.parse(root.dataset.galaxyConfig);
  const {
    Deck,
    OrthographicView,
    SolidPolygonLayer,
    PathLayer,
    ScatterplotLayer,
    TextLayer,
  } = window.deck;

  // --- theme colors (kept in sync with assets/theme.css) ---
  const UNOWNED_DOT = [150, 160, 156];
  const HYPERLANE_OWNED_ALPHA = 150;
  const HYPERLANE_FAINT = [90, 110, 104, 70];
  const BORDER_COLOR = [235, 238, 236, 230];
  const FILL_ALPHA = 48;
  const SYS_LABEL_COLOR = [223, 230, 227, 255];
  const SYS_LABEL_BG = [20, 25, 25, 170];

  // Label visibility is tiered by how far you've zoomed past the initial "fit"
  // zoom (dz), so it behaves the same regardless of galaxy size.
  //   country labels: shown from COUNTRY_DZOOM_MIN up (hidden when zoomed out
  //                   very far, where they'd pile into an unreadable heap).
  //   system labels:  owned-only from +OWNED, then every system from +ALL.
  const COUNTRY_DZOOM_MIN = -0.8; // hide empire names when zoomed out past this
  const LABEL_DZOOM_OWNED = 1.5; // ~2.8x zoomed in past the overview
  const LABEL_DZOOM_ALL = 3.0; // ~8x

  // Labels are sized in world units (sizeUnits "common") so they grow/shrink
  // with zoom, then clamped to a readable pixel range. getSize is derived so a
  // label is ~<px-at-fit> at the fit zoom and scales from there:
  //   on-screen px = pxAtFit * 2^dz, clamped to [min, max].
  const SYS_LABEL_PX_AT_FIT = 4.5; // small at fit; only shown once zoomed in
  const SYS_LABEL_PX = [9, 26]; // [min, max] clamp
  const COUNTRY_LABEL_PX_AT_FIT = 15;
  const COUNTRY_LABEL_PX = [12, 23];

  const container = document.getElementById("galaxy-deck");
  const loadingEl = document.getElementById("galaxy-loading");
  const dateLabel = document.getElementById("galaxy-date-label");
  const slider = document.getElementById("galaxy-date-slider");
  const selectionEl = document.getElementById("galaxy-selection");

  let geometry = null; // {systems, territory, hyperlanes}
  let owners = {}; // in-game system id -> {country_id, country_name, color}
  let countryLabels = []; // [{name, x, y, color}]
  let borders = []; // [[x0,y0],[x1,y1]], ...
  let dataVersion = 0;
  let baseZoom = 0; // initial fit zoom
  let currentZoom = 0;
  let deckgl = null;

  function ownerColor(id) {
    const o = owners[id];
    return o ? o.color : null;
  }

  // Mix a color toward white so empire names stay legible on the dark map.
  function brighten(c, t = 0.5) {
    return [
      Math.round(c[0] + (255 - c[0]) * t),
      Math.round(c[1] + (255 - c[1]) * t),
      Math.round(c[2] + (255 - c[2]) * t),
      255,
    ];
  }

  function fitViewState() {
    // Center on the systems and zoom so the whole galaxy fits the viewport.
    const xs = geometry.systems.map((s) => s.x);
    const ys = geometry.systems.map((s) => s.y);
    const minX = Math.min(...xs),
      maxX = Math.max(...xs);
    const minY = Math.min(...ys),
      maxY = Math.max(...ys);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const w = Math.max(maxX - minX, 1);
    const h = Math.max(maxY - minY, 1);
    const vw = container.clientWidth || 800;
    const vh = container.clientHeight || 800;
    // OrthographicView: pixels-per-world-unit = 2^zoom. Fit with a small margin.
    const zoom = Math.log2(Math.min(vw / w, vh / h)) - 0.15;
    return { target: [cx, cy, 0], zoom };
  }

  // Recompute empire-name labels (one per owner, at the mean of its systems).
  function computeCountryLabels() {
    const acc = {};
    for (const s of geometry.systems) {
      const o = owners[s.id];
      if (!o) continue;
      let a = acc[o.country_name];
      if (!a) a = acc[o.country_name] = { sx: 0, sy: 0, n: 0, color: o.color };
      a.sx += s.x;
      a.sy += s.y;
      a.n += 1;
    }
    return Object.entries(acc).map(([name, a]) => ({
      name,
      x: a.sx / a.n,
      y: a.sy / a.n,
      color: brighten(a.color, 0.55),
    }));
  }

  // World-unit size that renders as `pxAtFit` pixels at the fit zoom (so labels
  // then scale with zoom; px = pxAtFit * 2^dz, clamped per-layer).
  function worldSize(pxAtFit) {
    return pxAtFit / Math.pow(2, baseZoom);
  }

  function buildLayers() {
    const dz = currentZoom - baseZoom;
    const ownedLabels = dz >= LABEL_DZOOM_OWNED;
    const allLabels = dz >= LABEL_DZOOM_ALL;
    const countryLabelsVisible = dz >= COUNTRY_DZOOM_MIN;

    const territoryLayer = new SolidPolygonLayer({
      id: "territory",
      data: geometry.territory,
      getPolygon: (d) => d.polygon,
      getFillColor: (d) => {
        const c = ownerColor(d.id);
        return c ? [c[0], c[1], c[2], FILL_ALPHA] : [0, 0, 0, 0];
      },
      pickable: false,
      updateTriggers: { getFillColor: dataVersion },
    });

    const hyperlaneLayer = new PathLayer({
      id: "hyperlanes",
      data: geometry.hyperlanes,
      getPath: (d) => [d.source, d.target],
      getColor: (d) => {
        const oa = owners[d.a];
        const ob = owners[d.b];
        if (oa && ob && oa.country_name === ob.country_name) {
          const c = oa.color;
          return [c[0], c[1], c[2], HYPERLANE_OWNED_ALPHA];
        }
        return HYPERLANE_FAINT;
      },
      widthUnits: "pixels",
      getWidth: 1,
      capRounded: true,
      pickable: false,
      updateTriggers: { getColor: dataVersion },
    });

    const borderLayer = new PathLayer({
      id: "borders",
      data: borders,
      getPath: (d) => d,
      getColor: BORDER_COLOR,
      widthUnits: "pixels",
      getWidth: 1.2,
      pickable: false,
      updateTriggers: { getPath: dataVersion },
    });

    const systemLayer = new ScatterplotLayer({
      id: "systems",
      data: geometry.systems,
      getPosition: (d) => [d.x, d.y],
      getFillColor: (d) => ownerColor(d.id) || UNOWNED_DOT,
      getRadius: 2.5,
      radiusUnits: "pixels",
      radiusMinPixels: 2,
      radiusMaxPixels: 7,
      pickable: true,
      updateTriggers: { getFillColor: dataVersion },
    });

    // System name labels — tiered by zoom (owned first, then everything).
    const sysLabelData = !ownedLabels
      ? []
      : allLabels
      ? geometry.systems
      : geometry.systems.filter((d) => owners[d.id]);

    const systemLabelLayer = new TextLayer({
      id: "system-labels",
      data: sysLabelData,
      getPosition: (d) => [d.x, d.y],
      getText: (d) => d.name,
      getColor: SYS_LABEL_COLOR,
      getSize: worldSize(SYS_LABEL_PX_AT_FIT),
      sizeUnits: "common",
      sizeMinPixels: SYS_LABEL_PX[0],
      sizeMaxPixels: SYS_LABEL_PX[1],
      getTextAnchor: "start",
      getAlignmentBaseline: "center",
      getPixelOffset: [7, 0],
      background: true,
      getBackgroundColor: SYS_LABEL_BG,
      backgroundPadding: [3, 1],
      updateTriggers: { getSize: baseZoom },
    });

    // Empire name labels — shown except when zoomed out very far, bold, on top.
    const countryLabelLayer = new TextLayer({
      id: "country-labels",
      data: countryLabelsVisible ? countryLabels : [],
      getPosition: (d) => [d.x, d.y],
      getText: (d) => d.name,
      getColor: (d) => d.color,
      getSize: worldSize(COUNTRY_LABEL_PX_AT_FIT),
      sizeUnits: "common",
      sizeMinPixels: COUNTRY_LABEL_PX[0],
      sizeMaxPixels: COUNTRY_LABEL_PX[1],
      fontWeight: "bold",
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      background: true,
      getBackgroundColor: [10, 14, 14, 150],
      backgroundPadding: [5, 2],
      characterSet: "auto",
      updateTriggers: { getText: dataVersion, getColor: dataVersion, getSize: baseZoom },
    });

    return [
      territoryLayer,
      hyperlaneLayer,
      borderLayer,
      systemLayer,
      systemLabelLayer,
      countryLabelLayer,
    ];
  }

  function render() {
    if (!deckgl) return;
    deckgl.setProps({ layers: buildLayers() });
  }

  function showSelection(system) {
    if (!system) return;
    const owner = owners[system.id];
    const sysHref = `${cfg.historyUrl}?system=${system.system_id}`;
    let html = `Selected system: <a class="textlink" href="${sysHref}">${system.name}</a>`;
    if (owner) {
      const ownerHref = `${cfg.historyUrl}?country=${owner.country_id}`;
      html += ` (<a class="textlink" href="${ownerHref}">${owner.country_name}</a>)`;
    } else {
      html += " (Unclaimed)";
    }
    selectionEl.innerHTML = html;
  }

  async function fetchData(days) {
    const res = await fetch(`${cfg.dataUrl}?days=${Math.round(days)}`);
    const payload = await res.json();
    owners = payload.owners || {};
    borders = payload.borders || [];
    countryLabels = computeCountryLabels();
    dataVersion += 1;
    if (dateLabel) dateLabel.textContent = payload.date;
    render();
  }

  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  // A signature of which labels are visible at a given zoom. We only rebuild
  // layers when this changes (label size scales smoothly via "common" units, so
  // it needs no rebuild — only visibility changes do).
  function labelVisKey(dz) {
    const sysTier = dz >= LABEL_DZOOM_ALL ? 2 : dz >= LABEL_DZOOM_OWNED ? 1 : 0;
    const countryVisible = dz >= COUNTRY_DZOOM_MIN ? 1 : 0;
    return sysTier * 2 + countryVisible;
  }

  async function init() {
    const geomRes = await fetch(cfg.geometryUrl);
    geometry = await geomRes.json();

    const initialViewState = fitViewState();
    baseZoom = initialViewState.zoom;
    currentZoom = baseZoom;

    deckgl = new Deck({
      parent: container,
      views: new OrthographicView({ flipY: false }),
      initialViewState,
      controller: { inertia: true },
      pickingRadius: 8, // forgiving hover/click target around the small dots
      layers: [],
      getTooltip: ({ object, layer }) => {
        if (!layer || layer.id !== "systems" || !object) return null;
        const owner = owners[object.id];
        return {
          text: `${object.name}\n${owner ? owner.country_name : "Unclaimed"}`,
        };
      },
      onClick: (info) => {
        if (info.layer && info.layer.id === "systems" && info.object) {
          showSelection(info.object);
        }
      },
      onViewStateChange: ({ viewState }) => {
        const z = viewState.zoom;
        const changed =
          labelVisKey(z - baseZoom) !== labelVisKey(currentZoom - baseZoom);
        currentZoom = z;
        if (changed) render();
      },
    });

    // Load the most recent date first, then reveal the map.
    await fetchData(cfg.maxDate);
    if (loadingEl) loadingEl.style.display = "none";
  }

  if (slider) {
    slider.addEventListener(
      "input",
      debounce((e) => fetchData(Number(e.target.value)), 120)
    );
  }

  init().catch((err) => {
    console.error("Galaxy map failed to load", err);
    if (loadingEl) loadingEl.textContent = "Failed to load galaxy map.";
  });
})();
