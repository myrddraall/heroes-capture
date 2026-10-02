# Render speed: plan

Steps to make a render faster, agreed after the full Battlefield of Eternity layer render was
evaluated. Most of a render is the in-game capture (about 5 to 8 minutes for 125 to 160 tiles,
plus the sky passes); the stitch was about 2 minutes. The game is one instance drawing one frame
at a time, so the capture can only get faster by doing less per tile.

## Done

1. **Stage timings.** The capture and the stitch each end their log with a table of how long each
   stage took (launch and load, start-up, tiles, tiles past the grid, sky depth measurement, sky
   layer shots, leaving; matching, solving, seams, composing, void transparency, writing, sky
   layer images, composites). The baseline for everything below.
2. **`clean` folded into `tile`.** The tile command clears transmissions and messages just before
   it acknowledges; the capture then waits the settle time and keeps a fresh frame in which the
   strip still shows the tile's number, instead of sending `clean` (one chat round-trip per tile
   saved). `clean` stays for the probes.
3. **Multithreading in the stitch, and faster PNG writing.** Loading and matting the tiles, the
   neighbour matching, the seams and the sky layer shots run on a thread pool that keeps results
   in order (`workers.py`); the white level is fixed in tile order before the parallel loading, so
   nothing depends on which thread finishes first. The big PNGs are written at zlib level 1
   instead of 6, several at once, each composite rendered into memory once. Checked against the
   previous stitch's output on three saved runs (Battlefield of Eternity with its sky layers and
   composites, Hanamura Temple, Dragon Shire): every output pixel-identical. Battlefield of
   Eternity alone: 112 s before, 64 s after (on the development machine); the PNGs are about a
   quarter larger.

## Done after the waits probe

4. **Shorter waits.** The probe shot six sample tiles across Battlefield of Eternity (corners,
   edges, the middle, each arrived at from its neighbour) with the old waits twice and with
   shorter ones, and a sky swap likewise. On the tiles, every shot, the repeat at the old waits
   included, differed only in the same places: the cores' portal swirls, the blue orbs and small
   effects that animate on the renderer's own clock; no dark boxes around holes, no lighting
   change, nothing else. The sky swap's shots after 0.5, 0.25 and 0.1 s were within one level of
   the 1 s one. Now:

   | Wait | Was | Now |
   |---|---|---|
   | lighting-refit look before each tile | 0.25 s | 0.1 s |
   | settle from the tile's acknowledgement to the kept shot | 0.5 s | 0.25 s |
   | a sky swap drawn before its shot (five per sky position) | 1 s | 0.25 s |
   | the depth measurement's map shots | 0.5 s | 0.25 s |

5. **Sky positions further apart.** 0.8 of a screen between sky positions instead of 0.6: 12
   positions on Battlefield of Eternity instead of 20. Not identical to the old layers (mean 2.7
   levels in the composite): sharper, as fewer slightly misaligned shots (the shells are curved)
   are blended at each point.

The probe stays (`--probe-waits`), set to try still shorter values against the new ones.

## Second round

Measured on the full Battlefield of Eternity render after the first round: capture 277 s (tiles
156 s, sky layer shots 61 s, start-up 28 s), stitch 67 s.

6. ~~No fast-forward wait on maps without opening timers.~~ Undone: it brought the pause on
   Battlefield of Eternity back to 10 s after the gates, with the cores still mid-animation. The
   pause now comes 18 real seconds after the gates on every map, the fast-forward included.
7. **One command to set up a sky shot.** `hidemap <near clip>` clips the map away, freezes sky
   swaps (speed 1) and takes our sky down, in place of three commands at every sky position.
8. **Raw tile files.** Tiles and sky shots are saved as `.npy` arrays instead of PNG: about 3.5
   times larger, read 7 times faster than a PNG decodes. The stitch still reads PNG tiles from
   older runs. On the development machine this alone didn't speed the stitch up: the matting was
   the limit, the threads waiting on memory. So the matting now works in bands of 32 rows (kept in
   the processor's cache) and skips the un-premultiply division where a pixel is opaque (most of
   them), and each tile's region weights are worked out on the thread pool. Battlefield of
   Eternity: composing about 30 s down to 21-25 s, the stitch about 64 s down to 51-55 s; every
   output pixel-identical to the PNG-based stitch.

After the second waits probe:

9. **Chat box wait 0.15 s to 0.06 s.** 30 of 30 commands were taken first time at every wait down
   to 0.03 s; a command took 0.32 s at 0.15 and 0.20 s at 0.06 (0.19 at 0.03). Two commands per
   tile, more at each sky position.
10. **Settle 0.25 s to 0.1 s, and the sky swap 0.25 s to 0.1 s.** On the sample tiles the shots
    differed only where the repeat at the old settle did too (the energy column, the pulsing
    orbs, faint patches on the walkways); the sky swap's shots within one level down to 0.05 s.
    The lighting-refit look stays at 0.1 s: at 0 it differed visibly more on one tile.

11. **The sky work during the start-up.** The sky depth measurement and the sky layer shots
    don't need the map paused (the map is clipped away; the sky models freeze themselves at speed
    1), so a render does them as soon as the map takes commands, in the 18 s the map takes to get
    ready, instead of after the tiles; the tiles start when both are done. Saves about that wait
    (the two take about 38 s together on Battlefield of Eternity). The probes keep the old order.

## Third round

Measured on the full Battlefield of Eternity render after the second round (the pause back at
18 s, the sky work during the start-up): capture 182 s (tiles 112 s, sky work 45 s, leaving
12 s, launch and load 5 s), stitch 57 s.

12. **The black shot from one key.** Number pad 5 swaps to the black sky (a key-press trigger,
    `TriggerAddEventKeyPressed`, as Blizzard's own UILib and StartingExperienceLib use): no chat
    box to open, type into and send. A key carries no sequence number, so the capture waits for
    a frame whose strip still shows the tile's number and shows the sky as black; if the key went
    missing it sends `black` as before. One chat command fewer per tile.
13. **The stitch while the game leaves.** A render sends `quit` and goes on once the strip says
    the match is ending; the next launch waits for the menu first (a match left running is sent
    quit). The stitch no longer waits the 12 s or so the game takes to get back to the menu.
14. **No fixed 3 s sleep after the map loads**: the wait for the status strip covers it.

## Dropped: skipping the black shot where no sky is in view

Tested offline on the real shots of two full renders (each tile has both shots, so the
transparency a skip would lose is known exactly). A tile was allowed to skip only if its white
shot had no flat, neutral pixel near the white sky's level:

| Map | Tiles that could skip | Transparency lost |
|---|---|---|
| Battlefield of Eternity | 0 of 126 (every tile shows holes or void: from 0.16% to over half of each tile see-through) | none |
| Hanamura Temple | 14 of 150 | 109 pixels at most 17% see-through (likely animation noise) |

About ten seconds saved on Hanamura and nothing on Battlefield of Eternity; any looser threshold
skips real holes. Not worth it.
