# Map elements: plan

**Goal:** one render of a map gives the bare terrain plus each structure, mercenary camp and
objective as its own cut-out, so a viewer can show or hide each one: by hand, or from a replay at
a chosen time ("the map at 10:00"). It replaces the two renders `--structures keep|hide` make now.

**Prototype map:** Battlefield of Eternity. Everything below marked *probed* was tried there with
the elements probe (`map render "Battlefield of Eternity" --probe-elements`, probes.py) and seen
to work; the rest is still to build.

## How an element is cut out

Each element is shot **alone over the sky**: from the grid tile nearest it, everything else is
hidden, and the element is shot once over the white skybox and once over the black one. The pair
is matted exactly as the stitch mattes a tile (`stitch.matte`): its colour, and its transparency
from how much of the sky shows through, so soft edges, glass and glows come out right with no
threshold. *Probed:* a tower, a camp's defenders and the Immortal came out clean, and the two
white shots of each were identical or nearly so (0.006% of pixels at most, against 0.2–0.7%
between shots of the full scene, where the map's effects keep moving).

What is hidden, while the element is shot (`el isolate on <x> <y> <radius>`), and shown again
after (`el isolate off`; *probed:* the view afterwards matched the view before to within the
usual noise):

| What | How | Note |
| --- | --- | --- |
| All terrain | `TerrainShowRegion(whole map, false)` | a StarCraft II native, accepted by Heroes (*probed*); the cores' holes opened again after |
| Every doodad | `SetVisibility 0` to every doodad actor (as `libNtve_gf_ShowHideDoodadsInRegion` sends it to one type) | the cloud layers the capture keeps hidden are hidden again after |
| Units outside a circle round the element | `libNtve_gf_ShowHideUnit`, the ones hidden remembered and shown again | units already hidden (other structures) aren't touched |
| Model actors outside the circle | `SetVisibility 0` | a loot banner stand's banner is a model of its own |
| Other structures | hidden all at once (`libNtve_gf_ShowHideUnit`), the element shown alone | |

What can't be hidden: the map's **cliff doodads** (`CCliffDoodad`, listed in `t3Terrain.xml`'s
`cliffDoodadList`): terrain objects the terrain draws itself, with no actor; neither the terrain
switch nor actor messages reach them, and no Blizzard script touches them. So the cut-out is
cropped to the circle (`elements.cut_out`); an element whose circle reaches a cliff doodad would
carry a piece of it (none of the prototype's did).

Then the cut-out is cleaned (`elements.cut_out`, measured on the prototype's three elements):

- opacity of 8 or less (of 255) is made transparent: the white sky isn't quite even, and 99.9%
  of the pixels that are sky in both shots came out at 7 or less;
- opacity of 235 or more is made opaque, its colour the black shot's: the white sky brightens
  solid parts by up to about 14–19 levels of its 230;
- soft edges and glows lie between and stay as they are.

**Shadows don't come with it:** there is no ground for an element's shadow to fall on while it is
alone over the sky. The bare terrain has no structure shadows either (the structures are gone
when it is shot). If that shows, the refinement: shoot each element once more with the ground
under it kept, and take its shadow from that shot against the bare terrain, stored as how much it
darkens and drawn with multiply under the element.

## Elements

Every element has a kind, its unit type and placed cell (so a replay can find it), a group, and
one or more states, each a cut-out. Colours: the observer's (the left team blue) only; a
colouring per team was dropped (the client draws its own colours whatever the script sets with
`PlayerSetColorIndex`: *probed*).

| Kind | What | Where it comes from | Group | States |
| --- | --- | --- | --- | --- |
| Structure | core, towns' halls, towers, gates, walls, moonwells | the map's `Objects` (placed units, `elements.parse_objects`) | its town: the map script's `libGame_gv_townTownData` (region, owner, lane) | standing, rubble |
| Camp | a mercenary camp's defenders | the map script's jungle data (`libMapM_gv_jungleCreepCamps`) | the camp | spawned (gone is "hidden") |
| Objective | the map's event units and markers, without progress | per map (below) | the objective | per map |

**No progress is captured for objectives**, only what's worth showing while an event is on. For
example Hanamura: the paths and the payload in the centre for the whole event, and the tower a
team captured for about a minute after the capture. Battlefield of Eternity: the two Immortals at
their duel positions while the duel is on, and nothing once it's won.

Battlefield of Eternity has about 72 structures in 8 towns (2 cores, 26 towers, 10 gates, 8 halls,
10 moonwells, about 16 wall pieces), 4 camps (2 siege, 2 bruiser), and the Immortals.

## Getting the game to show each element

The capture script already removes units as they spawn, pauses the map's animations
(`AnimSetPausedAll` over the map region) and drives the camera tile by tile. The elements add
commands to it (`el ...`, capture_script.galaxy):

- **Structures:** hidden all at once and shown one at a time (`el hideall`, `el show <x> <y>`,
  the structure nearest the cell). *Probed.* A structure that is only hidden leaves a dark patch
  on the ground (part of its model, drawn even when the unit is hidden; `SetVisibility` and the
  lighting refit leave it too, `ModelSwap Invisible` doesn't): harmless for the cut-outs, which
  are shot over the sky, but it is why the bare terrain is shot with the structures destroyed
  (below).
- **Camps, on demand:** `libMapM_gf_JungleRespawnCreepsForCamp(camp)`, what the respawn timer
  calls, spawns a camp's defenders at once (`el camp <n>`, 0 for all). While they're shot, the
  sweep that removes new units leaves camps and objectives alone (`el keep 1`). *Probed.*
- **Objectives, on demand:** each map's library spawns its own. Battlefield of Eternity:
  `libMLBD_gf_MMBOESpawnBoss(team, point)` (`el boss`). *Probed.* Per map, written as each map is
  done.
- **Spawned units frozen:** their animations paused and the units paused (`el freeze`). *Probed:*
  they hold still.
- **What still moves** in the full scene (structures' glows and lightning, animated doodads, lava)
  can't be frozen: pausing units, `AnimSetTimeScaleGlobal 0`, `AnimSetPausedAll` again and game
  time scale 0.1 or 0 changed nothing (*probed*: drawn by the client, whatever the game's time).
  Shooting each element alone over the sky leaves it out.

## Rubble

A fallen structure shows its rubble, shot alone over the sky like the standing structure, after
every standing shot of its tile (a kill can't be undone):

- **Towers** don't die: the game turns a fallen tower into a `TownCannonTowerDead` unit (the
  rubble) by giving it the `TownCannonTowerInvulnerable` behaviour and ordering the
  `TowerDeadMorph` ability (the AI library does it in a normal match). `el kill` does the same
  for a tower; `UnitKill` would remove it with no rubble. *Probed.*
- **Halls, gates, walls, moonwells** die normally (`UnitKill`) and leave their death models.
  *Probed.* Each death is let play out (its animations unpaused, then paused again; a core's
  debris takes over 6 seconds to settle).
- **Cores:** a core's death would end the match, but everything that ends it listens for the
  death of whatever unit `libGame_gv_teams[team].lv_core` holds at that moment. So a replacement
  core is created first (same type and cell, hidden) and `lv_core` pointed at it; the original is
  killed and the match carries on (`el core <team> <seconds>`). *Probed:* the clock keeps running.
  The `quit` command still ends the match by killing whatever `lv_core` holds.
- **Side effects:** a town's structures falling runs the map's scripts (announcements, the core
  turning vulnerable, lanes changing); the sweep clears messages and units.

## The bare terrain

The terrain layer is the tile grid shot with **every structure gone**: destroyed (as for the
rubble) and their remains cleared, so the ground under each is drawn as it is
(`el clear`, as Blizzard's maps clear a destroyed town: the towers' rubble units removed, the
death models destroyed by alias, `_DeathModel`, `_Clearable`, `_DeadClearable`, and the debris
particles). *Probed:* clean ground, no patches, nothing else touched.

**The cores' holes:** each core stands on a small hole in the terrain (bit 4 of the cell's byte
in `t3CellFlags`: not drawn, the sky shows through; Battlefield of Eternity: 24 and 30 cells),
which the core's centre shows the sky through. With the core gone the hole is open to the sky.

- Filled in the map file when it is prepared (`elements.fill_structure_holes`: the hole flag
  cleared on any hole a structure stands on), the ground is drawn there: the floor the map's
  author painted runs straight through, no seam. *Probed.*
- But the standing core then shows the floor through its centre instead of the sky, which isn't
  how the game looks, and the file can't change while the match runs. `TerrainShowRegion` can't
  fill a hole the file marks, but it can open one (*probed*: hidden and shown again on plain
  ground). So every prepared map has the holes filled in the file (inject.py), and the script
  opens them again as it starts, over exactly their cells (a region of one small circle per
  cell's centre: the switch goes by cells), so the map looks as the game draws it; `el holes
  show` shows the ground for the bare pass. Anything that shows all the terrain (`el isolate
  off`) opens them again after. *Probed:* the holes the script opens look the same as the
  file's own (the core's area differs between the two by no more than between two runs with the
  file's holes), and the standing core's cut-out is opaque over its whole hole, so in the viewer
  it hides the ground under it. The core's rubble is shot with the hole open: where the sky shows
  through it, its cut-out is see-through and the viewer shows the floor there instead.
- Not used: the replacement core shown in the state it starts the match in (the empty pedestal);
  restarting the original's opening animation doesn't take it back (*probed*).

## Where each element is shot from

From one of the tiles the map is already shot from, so its geometry is already known and the
cut-out drops into the map's pixels without a new projection: the tile that has the element
nearest its centre (the least perspective lean). Its place on that shot is
`elements.screen_point` (the camera straight down at the tile's centre).

## The run

1. **Element pass:** for each tile that's an element's source: the camera on it; each of its
   structures shown alone and shot alone over the sky; its camps and objectives spawned, frozen,
   shot alone over the sky, removed.
2. **Rubble pass:** for each source tile, its structures brought down one at a time, each left to
   settle and its rubble shot alone over the sky; the cores with their holes opened.
3. **Clearing:** every structure on the map destroyed (the cores after their replacements take
   over) and the remains cleared; the cores' holes shown.
4. **Bare pass:** the tile grid as now: the terrain layer.

Battlefield of Eternity: about 72 structures standing and as rubble, 4 camps and the Immortals,
each a pair of shots plus the isolate and restore around it (a few seconds each in the probe),
from a few dozen source tiles. An estimate until stage 2 times it: some minutes more than a
render now.

## The pack

`pack.json` gets an `elements` list (in `data`, reserved for this): each element's id, kind, unit
type, placed cell, group, and per state its cut-out file (`elements/<id>-<state>.webp`) and
rectangle in the map layer's pixels. The terrain layer is the bare pass. `PACK.md` documents it.

## The viewer

An elements layer between the terrain and the minimap, drawn by placed cell's y (further from the
camera first), each element in the state chosen for it. Toggles per kind, per group and per
element. Later, driven by a replay and a time selector instead (below).

## Driven by a replay

The replay's tracker events say when each element changes:

| Element | Event |
| --- | --- |
| Structure | `SUnitBornEvent` at the start (type and cell: matched to the element), `SUnitDiedEvent` when it falls: rubble from then on |
| Camp | its defenders' births and deaths; the capture from `SStatGameEvent` |
| Objective | per map, its `SStatGameEvent`s (event start, capture, end) |

Matching is by unit type and cell, both of which the pack stores, so it's a lookup, not a guess.
To confirm by decoding a real replay with heroprotocol: the cells' scale in the tracker events,
and each map's stat-event names.

## Stages

1. **Prototype, Battlefield of Eternity** (the probes, done): showing and hiding structures,
   isolating an element over the sky and its cut-out, camps and the objective on demand, rubble,
   the core replacement, clearing, the cores' holes (filled in the file, opened by the script).
2. **All structures and camps** of Battlefield of Eternity through the run above: the element,
   rubble, clearing and bare passes; the pack format; the viewer's toggles.
3. **Battlefield of Eternity's objective** (the Immortals' duel), with its event states.
4. **Replay link:** element states from a replay's tracker events at a chosen time.
5. **Other maps:** structures and camps are generic (read from each map); objectives per map.
6. **Retire `--structures keep|hide`:** one render gives both.

## Decided

- **Battlefield of Eternity after the duel:** nothing shown (not the winner's Immortal).
- **Fallen structures:** rubble.
- **Colours:** the observer's only.
- **Cut-outs:** each element alone over the sky, matted, cropped to its circle.
- **Bare terrain:** structures destroyed and cleared, not hidden.

## Dropped

- **Cut-outs from differences** (the element shown against hidden, the pixels that change): the
  map's moving effects end up in them, and hidden structures leave patches.
- **Higher resolution for camps and objectives** (a closer camera): the perspective lean changes
  with the camera's distance, so a closer shot wouldn't lie on the map exactly.
- **A colouring per team:** the client ignores the colours the script sets; a second launch with
  the player on the other team would be needed.
