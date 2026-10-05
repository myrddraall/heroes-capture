# heroes-capture site

The maps heroes-capture rendered, published to GitHub Pages: a home page listing them, and each
map's pack ([PACK.md](../../PACK.md)) with the reference viewer as its page.

- `maps/<map id>/` holds each map's pack, as `map render` wrote it (committed: renders only
  happen on a machine with the game).
- `tools/build.mjs` (`pnpm run site.build`) builds `dist/`: the packs, the current reference
  viewer as each one's `index.html` (its entry in `pack.json` updated), and the home page.
- `tools/add-map.mjs` (`pnpm run site.add-map <pack folder>`) adds a map, or refreshes it, from
  a pack folder (`maps\<map id>\pack` where heroes-capture ran).

## Publishing

The project's release artifact is a `static-site` that owns the Pages root (`release-artifacts.yml`):
merging its release PR attaches the site to the release with a `deploy-gh-pages.zip` bundle.
`gitflow deploy` then runs `.github/workflows/deploy-production.yml`, which pushes the site to the
`gh-pages` branch. (The workflow can also be run by hand from the Actions tab with the release's tag.)

Once, in the repository's settings: Pages → Build and deployment → Source: **Deploy from a
branch**, branch **gh-pages**, folder **/ (root)** (the branch exists after the first deploy).
