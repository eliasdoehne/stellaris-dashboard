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

  // System-name labels reveal as you zoom in (maps-like). Thresholds are
  // relative to the initial "fit" zoom so they behave the same regardless of
  // galaxy size. Tiering (owned-only, then all) keeps overlap manageable
  // without a collision pass.
  //   +OWNED  -> system names of owned systems
  //   +ALL    -> every system name
  const LABEL_DZOOM_OWNED = 1.5; // ~2.8x zoomed in past the overview
  const LABEL_DZOOM_ALL = 3.0; // ~8x

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

  function buildLayers() {
    const dz = currentZoom - baseZoom;
    const ownedLabels = dz >= LABEL_DZOOM_OWNED;
    const allLabels = dz >= LABEL_DZOOM_ALL;

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
      getSize: 11,
      sizeUnits: "pixels",
      getTextAnchor: "start",
      getAlignmentBaseline: "center",
      getPixelOffset: [7, 0],
      background: true,
      getBackgroundColor: SYS_LABEL_BG,
      backgroundPadding: [3, 1],
    });

    // Empire name labels — always shown, bold, drawn on top.
    const countryLabelLayer = new TextLayer({
      id: "country-labels",
      data: countryLabels,
      getPosition: (d) => [d.x, d.y],
      getText: (d) => d.name,
      getColor: (d) => d.color,
      getSize: 17,
      sizeUnits: "pixels",
      fontWeight: "bold",
      getTextAnchor: "middle",
      getAlignmentBaseline: "center",
      background: true,
      getBackgroundColor: [10, 14, 14, 150],
      backgroundPadding: [5, 2],
      characterSet: "auto",
      updateTriggers: { getText: dataVersion, getColor: dataVersion },
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

  // Re-tier system labels only when crossing a threshold (avoids churn on every
  // wheel tick).
  function labelTier(dz) {
    if (dz >= LABEL_DZOOM_ALL) return 2;
    if (dz >= LABEL_DZOOM_OWNED) return 1;
    return 0;
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
        const retier = labelTier(z - baseZoom) !== labelTier(currentZoom - baseZoom);
        currentZoom = z;
        if (retier) render();
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
