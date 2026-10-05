# heroes-capture

A Windows command-line tool that drives an installed Heroes of the Storm client to capture game
assets as images. It starts with battlegrounds: transparent top-down map images, the map's own
sky layers for a parallax viewer, composites, and a small viewer to look at them in.

## Download or update

The whole tool is one file, `heroes-capture.exe` (nothing to install). In a Command Prompt or
PowerShell, in the folder you want it in (`curl` comes with Windows 10 and 11):

```bat
curl.exe -L -o heroes-capture.exe https://github.com/myrddraall/heroes-capture/releases/latest/download/heroes-capture-win-x64.exe
```

Run the same command again to update to the latest release. Type `curl.exe`, not just `curl`:
in PowerShell `curl` means something else. `-L` follows GitHub's redirect to the file.

You can also download `heroes-capture-win-x64.exe` from the
[releases page](https://github.com/myrddraall/heroes-capture/releases/latest) in a browser. The
file isn't signed, so Windows may warn the first time: choose **More info**, then **Run anyway**.
Each release also has a `.sha256` file to check the download against.

## Basic usage

1. Start Heroes of the Storm from the Battle.net app and set its display mode to
   **Windowed (Fullscreen)**.
2. In a Command Prompt, in the folder with `heroes-capture.exe`:

```bat
rem The game's maps, by category:
heroes-capture map list

rem One map:
heroes-capture map render "Battlefield of Eternity"

rem Every map of a category (battleground, arena, brawl, other, or all):
heroes-capture map render --category battleground

rem Without forts, towers and cores:
heroes-capture map render "Cursed Hollow" --structures hide
```

In PowerShell, start each with `.\heroes-capture.exe` instead of `heroes-capture`.

While it renders, leave the keyboard and mouse alone. Alt-tabbing away, or opening the game's
Esc menu, pauses it until you come back.

- Each map ends up in `maps\<map id>\`, for example `maps\battlefield-of-eternity\`: `pack\`
  is the map for a viewer ([PACK.md](PACK.md)), `raw\` the full-size images.
  `heroes-capture map view "Battlefield of Eternity"` opens it in your browser to look around.
- Running the same command again skips maps already rendered (`--force` renders them again), and
  picks up a render that failed or was stopped from where it got to.
- `logs\heroes-capture.log` keeps what each run printed. Working files go to `tmp\` and are
  removed after a successful render; `heroes-capture clean-up` removes those a failed run left
  behind.
- `heroes-capture --help` and `heroes-capture map render --help` list the options.

The tool's own README, [`packages/heroes-capture`](packages/heroes-capture), covers everything
else: how a render works, the options, troubleshooting, and working on the tool.
[PLAN.md](PLAN.md) records the decisions behind it.

## Workspace

A pnpm workspace on the [cpdevtools git-flow](https://github.com/cpdevtools/git-flow) template:
pushing a branch opens a release PR, and merging it builds and publishes the release.

```sh
pnpm install
```
