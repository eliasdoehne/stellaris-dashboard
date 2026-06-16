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
    CollisionFilterExtension,
  } = window.deck;

  // --- theme colors (kept in sync with assets/theme.css) ---
  const UNOWNED_DOT = [150, 160, 156];
  const HYPERLANE_OWNED_ALPHA = 150;
  const HYPERLANE_FAINT = [90, 110, 104, 70];
  const BORDER_COLOR = [235, 238, 236, 230];
  const FILL_ALPHA = 48;
  const LABEL_COLOR = [217, 217, 217, 255];

  // Labels are hidden when zoomed out (avoids clutter), revealed past these zoom
  // thresholds. Collision filtering thins them out further at any zoom.
  const LABEL_ZOOM_OWNED = 0.8; // owned systems first
  const LABEL_ZOOM_ALL = 2.2; // then everything

  const container = document.getElementById("galaxy-deck");
  const loadingEl = document.getElementById("galaxy-loading");
  const dateLabel = document.getElementById("galaxy-date-label");
  const slider = document.getElementById("galaxy-date-slider");
  const selectionEl = document.getElementById("galaxy-selection");

  let geometry = null; // {systems, territory, hyperlanes}
  let systemById = new Map();
  let owners = {}; // in-game system id -> {country_id, country_name, color}
  let borders = []; // [[x0,y0],[x1,y1]], ...
  let dataVersion = 0;
  let currentZoom = 0;
  let deckgl = null;

  function ownerColor(id) {
    const o = owners[id];
    return o ? o.color : null;
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
    // OrthographicView: pixels-per-world-unit = 2^zoom. Fit with a margin.
    const zoom = Math.log2(Math.min(vw / w, vh / h)) - 0.15;
    return { target: [cx, cy, 0], zoom };
  }

  function buildLayers() {
    const labelsOwnedVisible = currentZoom >= LABEL_ZOOM_OWNED;
    const labelsAllVisible = currentZoom >= LABEL_ZOOM_ALL;

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
      getRadius: 2,
      radiusUnits: "pixels",
      radiusMinPixels: 1.5,
      radiusMaxPixels: 6,
      pickable: true,
      updateTriggers: { getFillColor: dataVersion },
    });

    // Tier the labels: nothing far out, owned systems at mid zoom, everything
    // once zoomed in. Collision filtering thins whatever is shown.
    const labelData = !labelsOwnedVisible
      ? []
      : labelsAllVisible
      ? geometry.systems
      : geometry.systems.filter((d) => owners[d.id]);

    const labelLayer = new TextLayer({
      id: "labels",
      data: labelData,
      getPosition: (d) => [d.x, d.y],
      getText: (d) => d.name,
      getColor: LABEL_COLOR,
      getSize: 11,
      sizeUnits: "pixels",
      getTextAnchor: "start",
      getAlignmentBaseline: "center",
      getPixelOffset: [6, 0],
      background: true,
      getBackgroundColor: [20, 25, 25, 140],
      backgroundPadding: [3, 1],
      // Collision filtering thins overlapping labels. Guarded so a missing
      // extension can't blank the whole layer.
      ...(CollisionFilterExtension
        ? {
            extensions: [new CollisionFilterExtension()],
            collisionEnabled: true,
            getCollisionPriority: (d) => (owners[d.id] ? 1 : 0),
            collisionTestProps: { sizeScale: 2 },
            updateTriggers: { getCollisionPriority: dataVersion },
          }
        : {}),
    });

    return [territoryLayer, hyperlaneLayer, borderLayer, systemLayer, labelLayer];
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

  async function init() {
    const geomRes = await fetch(cfg.geometryUrl);
    geometry = await geomRes.json();
    systemById = new Map(geometry.systems.map((s) => [s.id, s]));

    const initialViewState = fitViewState();
    currentZoom = initialViewState.zoom;

    deckgl = new Deck({
      parent: container,
      views: new OrthographicView({ flipY: false }),
      initialViewState,
      controller: { inertia: true },
      layers: [],
      getTooltip: ({ object }) => {
        if (!object || object.name === undefined) return null;
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
        // Only re-tier labels when crossing a threshold, to avoid churn.
        const before = currentZoom;
        const crossed =
          (before < LABEL_ZOOM_OWNED) !== (z < LABEL_ZOOM_OWNED) ||
          (before < LABEL_ZOOM_ALL) !== (z < LABEL_ZOOM_ALL);
        currentZoom = z;
        if (crossed) render();
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
