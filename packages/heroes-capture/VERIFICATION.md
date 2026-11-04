# Verifying a map

A map is **verified** when its render passes every item below, checked by hand against Battlefield
of Eternity, the standard. The record is `src/heroes_capture/maps/validated-maps.json`: each map's
entry carries the `checklist` version it passed, and `heroes-capture map list` shows a ✓ only for
the maps at the checklist's current version. The pack carries the same as `map.validated`.

**The reset rule.** Whenever the render changes (anything in `capture/`, `categories/`, `map/`,
the script parts, a map's own module in `maps/`, or the pack format in `PACK.md`), bump
`checklist` in `validated-maps.json`. Every map then shows as "not yet" until it has been
rendered with the new tool and passed the whole list again. There is no partial credit and no
carrying a verification over: the point is that nothing on this list is assumed to still hold.
Changes to the viewer alone (heroes-replay-stats) don't reset it; a viewer feature the list needs
(arena switching) blocks the maps that need it until it exists.

## The procedure

1. Calibrate the map and review the calibration (Calibration, below), unless it has a reviewed
   calibration from the current tool. Then render it as the elements render:
   `map render "<map>" --structures elements --force`.
2. Go through the checklist below with the pack served locally (`map view`, and the viewer in
   heroes-replay-stats with `pnpm run serve.maps`), writing down what fails.
3. Everything passes: set the map's `checklist` to the current version in `validated-maps.json`,
   with the heroes-capture version and a note of what is particular to the map. Anything fails: the
   map stays at its old number; note the failures in its entry.
4. Add the pack to heroes-maps (`pnpm run add.map`).

## The checklist

Battlefield of Eternity passes every item; where an item says "as on BoE", compare against it.

### Render

- [ ] **Elements render.** `pack.json` has `data.elements` with every structure (standing and rubble
      cut-outs), the towns, the outposts and the camps, and `map.structures` is `elements`. A render
      with the structures kept or hidden is not verifiable.
- [ ] **Calibrated.** A reviewed calibration from the current calibration version in
      `src/heroes_capture/maps/calibration/<map id>.json`, and the render prepared with it (its log:
      "copies: … on the spots the calibration checked; … fall waits picked in its review").
- [ ] **Whole map.** Every tile shot and placed; no gaps, no tile missing or black; the image reaches
      the map's content on every side (the arches past BoE's top edge).
- [ ] **Seams.** No visible seams between tiles; nothing doubled or cut along them; no unit, minion
      or effect left in a tile (the spawns cleared).
- [ ] **Void.** The void transparent: the sky showing through on a sky map, the black terrain
      removed on a black-void map, with clean edges (no halo, no stepped line).
- [ ] **The map's edge.** Nothing black left where a model reaches the map's last cell (the orb
      north of Punisher Arena, BoE's east chains): the game's edge black removed and the model
      fading out.
- [ ] **Sky layers.** On a sky map: the background art, the haze and the fixed skybox, each covering
      the whole view at every zoom, with the parallax right (the sky moving slower than the map, as
      in the game).
- [ ] **Team colour.** The map layer and every structure have a team-colour mask; swapping the
      colours turns every structure and every team-coloured doodad (lamps, crystals, walls, flags)
      from blue to red or red to blue, with nothing left in the old colour and nothing recoloured
      that shouldn't be; swapped back, the picture is as shot.
- [ ] **Loading screen and icons.** `images.loadingScreen` and `images.loadingScreenIcons` present,
      the map's own art (not the generic one, not another map's), with their smaller sizes.
- [ ] **Custom minimap.** `images.customMinimapSvg` present and lying on the map when drawn over it.
- [ ] **Arenas.** On a map of several arenas: one map layer each, without the edges of the arenas
      next to it, and `arenas` naming each with its bounds.
- [ ] **Map facts.** `map.category`, `sizeCells` and `cameraBounds` right; the map's own module
      (opening timers, minimap correction) applied where it has one.
- [ ] **Pack.** Format current; every file listed in `files` present with its size and hash; the
      pack's total size in line with BoE's for the map's size.

### Viewer (heroes-replay-stats)

- [ ] **Loads.** The map page opens with no errors in the console and no failed requests.
- [ ] **Zoom.** Zooming from fully out to fully in, every level draws with no missing tiles, and
      the sky layers keep up.
- [ ] **Points of interest.** The list shows every town with its structures, the outposts, the
      cores and the camps; picking one centres it; its state switches (standing/rubble, spawned/off)
      and the map follows.
- [ ] **Team colour.** The swap button turns everything (the terrain's doodads and the structures),
      with no flicker to the old colours while zooming.
- [ ] **Arenas.** On a map of several arenas: each can be chosen and shown on its own.
- [ ] **Map panel.** The loading screen shows, with the objectives and their icons from the pack;
      the facts (category, size, playable area, structures, camps, game build) are right.

## Calibration

A map's elements render depends on things that differ from map to map: where a structure's copy
can stand and still look like it, which structures change colour with the side, when each kind's
rubble looks best, what keeps moving while the map is paused. The calibration measures them in a
short match of the map; what the numbers settle is settled, and the rest is reviewed by eye before
any render uses it. An elements render stops when the map has no reviewed calibration from the
current tool (`capture/calibration.py`'s `VERSION`).

1. **The probe** (`map calibrate "<map>"`, its own run of `run.cmd`). It reads what the map's
   structures and doodads are made of from the game's data (each structure's actor tree: the
   actors its unit creates, the models they show, and each model's particle systems, ribbons and
   lights), then in the game:
   - **what still moves while paused**, on every tile of the grid as the tiles see it, each moving
     patch named by what stands there (a structure, a doodad, or nothing placed: water);
   - **each structure's side switch**: whether it changes when our player switches to Chaos and
     back, and how long each way takes, what moves on it by itself left out;
   - **each structure's copy spot**: candidate spots tried with a real copy until one looks like
     the structure (drawn as much, as bright, no colour cast, no more black) and its rubble isn't
     black (Dragon Shire's void corners fail this);
   - **each kind's fall**, filmed on each team under the render's camera, a frame every quarter
     second;
   - **each kind standing**, filmed on each team from a restart of its animations (hidden and shown
     again, which starts them over: a level 3 tower from its build), a frame every quarter second.

   The results land in `maps\<map id>\calibration.json` with their pictures in
   `maps\<map id>\calibration\`.

   The stages (`motion`, `structures`, `rubble-at-home`: the last brings one structure of each kind
   down at home and shoots its rubble with the scene hidden and shown) can run on their own:
   `map calibrate "<map>" --only rubble-at-home` measures that stage and merges it into the map's
   calibration so far. Each stage has its own version, so a change to one means running just that
   one again.
2. **The review.** `heroes-capture calibration-review maps\<map id>` builds a page, published as an
   artifact, that sums up what was settled and asks the rest: the frame where each kind's rubble
   looks best, the moment each kind should be shot standing, copy spots that passed only narrowly, kinds of structure that disagreed on team
   colour. It also shows what moves while paused, with what the data says it is made of; a note
   can go with each. Submitting saves the answers into the page.
3. **Applied.** `heroes-capture calibration-apply maps\<map id> answers.json` writes the reviewed
   calibration to `src/heroes_capture/maps/calibration/<map id>.json`, which an elements render of
   the map is prepared with: the checked copy spots, which structures change colour with the side,
   the picked fall waits (other kinds keep Battlefield of Eternity's) and the standing times (each
   structure restarted and paused at its kind's time, the same moment every run).

## Status

Checklist version **3** (models a map defines in its own data frozen in their own entries: a second
entry for the same model didn't take, so Dragon Shire's forts' fires burned on while paused).
Version 2 added the calibration; version 1 had the edge black removed and the loading screen in
the pack.

| Map | Verified | What's missing |
|---|---|---|
| Battlefield of Eternity | 1 (the standard) | to calibrate, review and render again at version 3 |
| Dragon Shire | no | to calibrate (its void corners drew copies' rubble black), review and render again; its forts' side switches |
| Punisher Arena | no | to calibrate, review and render again |
| Volskaya Foundry | no | last rendered at pack format 4 |
| the rest | no | not rendered with this tool |
