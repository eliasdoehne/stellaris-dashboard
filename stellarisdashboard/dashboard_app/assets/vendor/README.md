# Vendored front-end libraries

These are pinned, pre-built bundles served directly by Flask (`/static/vendor/...`)
so the dashboard works offline. There is no build step — to upgrade, drop in a new
pinned file and update the version note below.

## deck.gl.min.js

- **Library:** [deck.gl](https://deck.gl/) (vis.gl / OpenJS Foundation) — WebGL2 visualization framework.
- **Version:** 9.1.12
- **Source:** https://unpkg.com/deck.gl@9.1.12/dist.min.js
- **Global:** exposes `window.deck` (UMD bundle: core + layers + extensions).
- **Used by:** the Galaxy Map page (`templates/galaxy_map.html` + `assets/galaxy_map.js`).

The standalone `dist.min.js` bundles `@deck.gl/core`, `@deck.gl/layers`,
`@deck.gl/aggregation-layers` and `@deck.gl/extensions` — enough for the
`OrthographicView`, `SolidPolygonLayer`, `PathLayer`, `ScatterplotLayer`,
`TextLayer`, and `CollisionFilterExtension` used by the galaxy map. It has no
dependency on React, Mapbox, or MapLibre.

- **License:** MIT (the minified bundle has no banner, so the license text is
  kept alongside it in `deck.gl.LICENSE`). Bundled transitive deps are MIT /
  Apache-2.0 / BSD-style; their notices remain inside the minified source.
