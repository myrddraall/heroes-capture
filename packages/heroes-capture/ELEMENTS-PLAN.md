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

What can't be hidden that way: the map's **cliff doodads** (`CCliffDoodad`, listed in
`t3Terrain.xml`'s `cliffDoodadList`): terrain objects the terrain draws itself, with no actor;
neither the terrain switch (`TerrainShowRegion`) nor actor messages reach them, and no Blizzard
script touches them. But the game's executable lists every function and constant a map script
can use, and next to `TerrainShowRegion` it has `EnvironmentShow` with `c_environmentTerrain`,
`c_environmentDoodads` and `c_environmentWater`: the terrain switched off as a whole, which should
take its terrain objects with it (`el env off`; *to see in the next render*). Each source tile is
also shot once with nothing shown: whatever is still there is left out of its elements' cut-outs
(what is the same in both), and the stitch logs how much there was.

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
- **Shot at its best moment:** each kind on each team filmed falling (a frame every quarter
  second) and the moment its rubble looked best chosen: towers 2 to 3 seconds after the fall,
  gates 1.2 to 1.3, walls 1.3 to 2.5, moonwells 1.8 to 3.3, forts and keeps 2.6 to 3, cores 4.8
  (`element_capture.FALL_WAITS`); in a circle as far as it reaches then
  (`elements.RUBBLE_RADII`: a wall's or moonwell's 6 cells to a core's 22, measured each brought down
  alone and widened where renders still reached the edge).
  The Hell team's gates leave no rubble. Scorch marks are drawn on the terrain, which is switched
  off for these shots.
- **Brought down in view:** a fall gives off its particles (smoke, dust, sparks, a core's lava)
  only near the camera. *Probed:* with the camera far away, or far back over the whole map, a
  copy's rubble had only its chunks; at the edge of the capture camera's view a wall's had none;
  with the camera 2.5 times further back and the fall in the middle of its view, every kind had
  all of them. So whatever falls together falls a group at a time (`elements.view_groups`: the
  middle half of that view either way), the camera over each group (`el wide`).
- **Prepared by copies:** a copy of a structure (`el copyfall`: same type, owner and facing) falls
  on a spare spot while the structures stand, and its rubble is shot there, recorded as if from
  its structure's cell (the camera moved by the difference: the view straight down is the same
  anywhere). *Probed:* a copy's rubble matches its structure's under the same lighting; on
  Battlefield of Eternity the Hell side lights a copy orange, so each spot is chosen under its
  structure's lighting (the map's `LightingMap.tga`, where it has one), clear of every other
  circle, on the same part of a cell (`elements.copy_spots`, when the map is prepared). A core's
  copy lacks the statue and shield crystals the map's script gives the real core, and a keep's
  copy (`TownTownHallL3`) crashed the game in every launch (the forts' were fine), so cores and
  keeps fall themselves; so does any type whose copy crashes the game (remembered across the
  run's relaunches) and any structure the map has no spot for. *Tried and dropped:* copies on
  their structures' own cells with their rubble hidden: a hall's and most gates' and walls'
  remains hide and come back exactly (`el dmsg c`: the actors whose scope contains `_DeathModel`,
  by centre), but some gates' and walls' didn't show again, and a fallen tower's remains neither
  hide nor move.

## The run

1. **Element pass:** the whole scene hidden once (every structure and unit; the terrain, doodads and
   water switched off: *probed*, nothing is left, the cliff doodads included) over the white sky.
   The structures aren't hidden but faded out by opacity (`el fadeall 0`; "el env off" leaves
   them): each keeps its look as the map paused it once ready, its effects built up while the
   map ran. *Probed:* faded out a structure leaves nothing (a gate a trace), faded back in it is
   exactly as it stood, effects and a core's shield crystals included; hidden and shown again it
   restarts its animations (a level 3 tower's into its birth: a Heaven tower's golden glow, then
   white wings; a Hell tower's red flare, then a closed claw) and gives off no particles until
   it plays. Then the camera straight above
   each structure in turn, in the middle of the screen (`el at`: the lighting refitted there as
   the tiles do, without the rest of their scene; *probed:* without a refit at the element,
   structures away from the last one came out duller, the keeps' glows half gone), the structure
   faded in, shot over white and over black (the black sky detected as the tiles do, not waited
   for), faded out. The camps spawned with the scene hidden (those clear of every structure's
   circle), born during the play, shot after the structures.
2. **Rubble pass:** prepared during the first standing wave: every copy brought down on its spot
   (the slowest to settle first, so each has settled when everything is paused); each wave after
   plays only its own structures, so the copies' rubble stays paused. Then each copy's rubble shot
   on its spot, with no waiting. The rest then fall in waves (`elements.rubble_waves`: none in a
   wave near enough another for their rubble to share a shot; the cores in the last, after hidden
   replacements with no model take their places), each wave left to settle, each one's rubble
   shot alone, then cleared away.
3. **Clearing:** the scene shown again, the remains cleared, and the fallen town structures' own
   actors (a town hall's outlives it and goes on drawing its patch: `Signal
   ClearTownStructureDeathModel`); the cores' holes shown.
4. **Bare pass:** the tile grid as now: the terrain layer.

Battlefield of Eternity: 72 structures, 4 camps. The element phase took 14 minutes with each
element isolated and restored on its own and each structure brought down on its own, 3.4 with the
scene hidden once and the rubble shot all at once.

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
   rubble, clearing and bare passes; the pack format; the viewer's toggles. *Built, not yet run in
   the game:* the element list read when a map is prepared (`elements.element_list`: structures
   with their towns from the script's town data and the map's regions, camps from the script's
   jungle data and the map's points), the passes (`element_capture.py`, `--structures elements`,
   its own pack in `elements/`), the cut-outs placed by the stitch (`stitch.write_elements`), the
   pack's `data.elements` (PACK.md, Elements) and the reference viewer's elements layer. Still to
   do: the seams routed so each element's area of the map comes from the tile it was shot from;
   maps of several arenas.
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
