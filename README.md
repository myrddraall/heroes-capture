# heroes-capture

A Windows command-line tool that drives an installed Heroes of the Storm client to capture game
assets as images. It starts with battlegrounds: transparent map images, the map's own sky layers
for a parallax viewer, composites, and a prototype viewer.

The tool currently lives in [`packages/heroes-capture`](packages/heroes-capture) in the form it
had in `heroes-replay-stats` (Python with a Node injector, run through `update.cmd`); see its
README. [PLAN.md](PLAN.md) describes where it is going: a single `heroes-capture.exe` released
through git-flow.

## Workspace

A pnpm workspace on the [cpdevtools git-flow](https://github.com/cpdevtools/git-flow) template:
pushing a branch opens a release PR, and merging it builds and publishes the release.

```sh
pnpm install
```
