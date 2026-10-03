# heroes-capture: plan

## What it is

A Windows command-line tool that drives an installed Heroes of the Storm client to capture game
assets as images. It starts with battlegrounds (transparent map images, the map's own sky layers
for a parallax viewer, composites, a prototype viewer). Other kinds of assets, such as units in
frozen poses at different angles over chroma-keyable backdrops, come later as further
subcommands; they are not designed yet.

It is Windows-only because it automates the game client.

## The release

One file, `heroes-capture.exe`, attached to every GitHub release:

```sh
heroes-capture map "Battlefield of Eternity"
heroes-capture --version
```

- Nothing is required beyond what to capture; a run is unattended from start to finish.
- What a render needs is built in, and what can be read from the map or the game is read there.
  Overrides exist only for a named need; diagnostics (the probes) are hidden flags.
- A run prints one line saying what it is doing, then its progress.

## Decisions

### Python only

The tool is about 5,600 lines: 4,000 Python (capture, stitching, sky layers, matting, numerics)
and a 1,600-line Node injector. The injector is ported to Python; nothing is ported the other
way. Weighed against an all-TypeScript tool:

|                              | Python                                 | Node / TypeScript                                                                         |
| ---------------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------- |
| Screen capture               | `dxcam` (DXGI Desktop Duplication)     | no maintained Node library; a native addon or DXGI COM through FFI                        |
| Image maths                  | numpy, scipy, pyvips, vectorised in C  | mostly hand-written typed-array loops, several times slower                               |
| libvips                      | the full API                           | `sharp` is a subset; `wasm-vips` has a 4 GB ceiling (composites need about 2 GB in float) |
| Fitting (least squares, FFT) | `scipy.optimize`, `numpy.fft`          | smaller libraries or our own                                                              |
| Single executable            | PyInstaller, mature                    | Node SEA cannot embed native addons; `pkg` is archived                                    |
| Port size                    | the injector, mostly a script template | the numeric and capture code, plus the gaps above                                         |
| Tests                        | the simulated game exists in Python    | rewritten                                                                                 |

TypeScript would match the other repositories and their tooling. That would decide it only if the
capture code had to run in a browser or be shared with `heroes-replay-stats`; only its JSON
outputs are shared.

### Packaging

- PyInstaller, one-file. It bundles numpy, scipy and libvips, so expect 150 to 250 MB, and a few
  seconds to start.
- The release version is embedded in the executable's version resource (`VS_VERSIONINFO`), which
  the release checks (see the release section below).
- StormLib (MPQ archives: the maps) and CascLib (the game's CASC storage) are called through
  ctypes, their DLLs inside the executable. Both are MIT, both by Ladislav Zezula.

### Assets at runtime

Nothing generated is checked in: the program reads or downloads what it needs when it runs.

- Game data comes from the local game install's CASC storage: the light sets (today the generated
  `light-sets.json`), the sky models for the keyed copies (today the uncommitted `local-assets/`),
  the map libraries. It always matches the installed build, and a capture needs the game
  installed anyway.
- Maps come from the local install's CASC too, if the battleground `.stormmap` files are there.
- Without an install (CI, the simulated-game tests on Linux), the same data comes from Blizzard's
  CDN through CascLib's online storage (`CascOpenOnlineStorage`, product code `hero`).
  The GitHub mirror `jamiephan/HeroesOfTheStorm_S2MA` stays only if that does not work.
- Cache: `%LOCALAPPDATA%\heroes-capture\cache`, keyed by game build.
- `opening-timers.json` stays in the code: it is hand-curated knowledge, not generated data.

## Repository

On the [cpdevtools git-flow template](https://github.com/cpdevtools/git-flow-template):

- the root `package.json` holds git-flow (1.2.0 or later, for the `executable` artifact type),
  versioning, husky, and root aliases for the commands people run (`verb.noun`, e.g.
  `pnpm run build.exe`, `pnpm run test.harness`);
- `packages/heroes-capture/` holds the Python project (`pyproject.toml`, managed with uv), a
  `package.json` with `github.actions.build` (PyInstaller at the release version) and
  `github.actions.test`, and a `release-artifacts.yml` declaring the executable;
- `tests/` holds the simulated game (a fake client with a virtual clock that runs `capture.py`
  end to end on Linux) and the fault, resume and regression scripts, which until now existed only
  outside any repository;
- `dev/` holds the `update.cmd` / `run.cmd` loop for running a development copy on the Windows PC
  (paths personal, so `update.cmd` stays untracked).

## Release: git-flow, built on Windows

`build-pack` runs on Ubuntu by default, and PyInstaller cannot cross-compile, so this repository's
`build-pack-publish.yml` runs `build-pack` on `windows-latest`. That needs the `build-pack` action
to work there: its bash steps, zx calls and paths under Git Bash on Windows runners, with no
Docker assumed.

### The `executable` artifact type (git-flow 1.2.0)

Built into git-flow; `release-artifacts.yml` declares it:

```yaml
artifacts:
  - type: executable
    name: heroes-capture
    path: dist/heroes-capture.exe
    platform: win-x64
```

- The release gets `heroes-capture-win-x64.exe` and `heroes-capture-win-x64.exe.sha256`. The
  version is not in the file name (the release tag carries it), so
  `releases/latest/download/heroes-capture-win-x64.exe` stays a permanent link.
- Pack verifies rather than builds: for an `.exe` it reads the version resource's
  `ProductVersion` string without running the file, and refuses the binary unless it equals the
  release version, so a stale `dist` can't ship under a new tag.
- So `github.actions.build` stamps `PROJECT_VERSION` into PyInstaller's version file as the
  `ProductVersion` string (the four-part numeric version can't hold a prerelease such as
  `0.2.0-beta.1`, and isn't read).

## Stages

Proposed; the boundaries are chosen before each starts.

1. **Injector in Python.** Port `inject.mjs`, `capture-script.mjs`, `sky.mjs` and
   `light-data.mjs`; MPQ writing through StormLib. Node leaves the package.
2. **Game data from CASC.** CascLib, local install first; a spike on its online storage for Heroes;
   light sets and the keyed sky models read at runtime; `light-sets.json`,
   `generate-light-sets.mjs` and `local-assets/` go.
3. **Package and tests.** The `heroes-capture` command, `pyproject.toml`, the package scripts; the
   simulated game and regression scripts into `tests/`, run by `test.yml`.
4. **Release.** PyInstaller build with the version resource; `build-pack` on Windows; the
   `executable` artifact; the first release.
5. **Switch over.** The development loop on the PC points here; `tools/map-capture` leaves
   `heroes-replay-stats`.

## To verify

- CascLib's online storage works for Heroes (product `hero`).
- The battleground `.stormmap` files are in the local CASC storage.
- The `build-pack` action runs on `windows-latest`.
- The executable's size and start-up time, and whether antivirus flags the one-file build.
