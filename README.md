# heroes-capture

A Windows command-line tool that drives an installed Heroes of the Storm client to capture game
assets as images. It starts with battlegrounds: transparent top-down map images, the map's own
sky layers for a parallax viewer, composites, and a small viewer to look at them in.

## Download or update

The whole tool is one file, `heroes-capture.exe` (nothing to install). In PowerShell, in the
folder you want it in:

```powershell
Invoke-WebRequest https://github.com/myrddraall/heroes-capture/releases/latest/download/heroes-capture-win-x64.exe -OutFile heroes-capture.exe
```

Run the same command again to update to the latest release. You can also download
`heroes-capture-win-x64.exe` from the [releases page](https://github.com/myrddraall/heroes-capture/releases/latest)
in a browser. The file isn't signed, so Windows may warn the first time: choose **More info**,
then **Run anyway**. Each release also has a `.sha256` file to check the download against.

## Basic usage

1. Start Heroes of the Storm from the Battle.net app and set its display mode to
   **Windowed (Fullscreen)**.
2. In the folder with `heroes-capture.exe`:

```powershell
.\heroes-capture.exe map list                                  # the game's maps by category
.\heroes-capture.exe map render "Battlefield of Eternity"      # one map
.\heroes-capture.exe map render --category battleground        # every map of a category (or all)
.\heroes-capture.exe map render "Cursed Hollow" --structures hide   # without forts, towers and cores
```

While it renders, leave the keyboard and mouse alone. Alt-tabbing away, or opening the game's
Esc menu, pauses it until you come back.

- Each map ends up in `maps\<map id>\`, for example `maps\battlefield-of-eternity\`. Open
  `…-viewer\index.html` there to look around the map.
- Running the same command again skips maps already rendered (`--force` renders them again), and
  picks up a render that failed or was stopped from where it got to.
- `logs\heroes-capture.log` keeps what each run printed. Working files go to `tmp\` and are
  removed after a successful render; `.\heroes-capture.exe clean-up` removes those a failed run
  left behind.
- `.\heroes-capture.exe --help` and `.\heroes-capture.exe map render --help` list the options.

The tool's own README, [`packages/heroes-capture`](packages/heroes-capture), covers everything
else: how a render works, the options, troubleshooting, and working on the tool.
[PLAN.md](PLAN.md) records the decisions behind it.

## Workspace

A pnpm workspace on the [cpdevtools git-flow](https://github.com/cpdevtools/git-flow) template:
pushing a branch opens a release PR, and merging it builds and publishes the release.

```sh
pnpm install
```
