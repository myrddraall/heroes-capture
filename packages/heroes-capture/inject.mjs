#!/usr/bin/env node
/**
 * Prepares a battleground for capture: copies the .stormmap, injects the capture script
 * (capture-script.mjs) and writes the capture grid the capture and stitch steps follow.
 *
 *   node inject.mjs "Towers of Doom" [options]
 *   node inject.mjs "C:\path\to\Some Map.stormmap" [options]
 *
 * A bare name is downloaded from jamiephan/HeroesOfTheStorm_S2MA (refreshed from the live
 * game every few hours). Options:
 *
 *   --structures keep|hide   keep or hide forts, towers, cores and gates      (default keep)
 *   --px-per-cell <n>        output resolution, pixels per map cell         (default 48)
 *   --screen <w>x<h>         the game's resolution while capturing           (default 3840x2160)
 *   --fov <deg>              vertical field of view; narrower is flatter      (default 20)
 *   --pitch <deg>            camera pitch (default 90, straight down)
 *   --refit-yaw <deg>        yaw of the lighting-refit look before each tile; by default it
 *                            faces the map's main light, found from the map's tileset and the
 *                            game's light sets (light-sets.json); only a shallow look towards
 *                            the light clears the dark boxes around holes
 *   --distance <units>       camera distance instead: the field of view is chosen to keep
 *                            --px-per-cell (beyond about 120 the game renders the terrain
 *                            in low detail: dark squares around holes, dark wedges)
 *   --keep <0..1>            share of each screenshot used, centred           (default 0.6)
 *   --no-lens                leave field of view and far clip to the map
 *   --freeze                 pause model animations (experimental)
 *   --markers                show registration markers around each screenshot (experimental)
 *   --show-ui                leave the HUD up (diagnostic)
 *   --keep-intro             let the intro cutscene play out instead of skipping it (diagnostic)
 *   --margin <cells>         capture past the camera bounds (lifts them)      (default 0)
 *   --crop-margin <cells>    the stitched image reaches this far past the camera bounds
 *                            (or past each arena area, see below)             (default 12)
 *   --out <dir>              working folder                                   (default work)
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import {
  Archive,
  MPQ_COMPRESSION_ZLIB,
  MPQ_FILE_COMPRESS,
  MPQ_FILE_REPLACEEXISTING,
} from '@jamiephan/stormlib';
import { captureScript } from './capture-script.mjs';
import { SKIES, skyFiles, solidDds } from './sky.mjs';
import { hasSky } from './light-data.mjs';
import { STATUS_CELLS, STATUS_CELL_UNITS } from './capture-script.mjs';
import { mainLight } from './light-data.mjs';

const S2MA_MAPS = 'https://raw.githubusercontent.com/jamiephan/HeroesOfTheStorm_S2MA/main/maps';

function parseArgs(argv) {
  const opts = {
    map: null,
    structures: 'keep',
    pxPerCell: 48,
    screen: { w: 3840, h: 2160 },
    fov: 20,
    distance: null,
    pitch: 90,
    refitYaw: null,
    keep: 0.6,
    lens: true,
    freeze: false,
    keepMechanics: true,  // map-mechanic units such as altars stay: removing them leaves holes in the terrain
    markers: false,
    showUi: false,
    keepIntro: false,
    margin: 0,
    cropMargin: 12,
    out: 'work',
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === '--structures') opts.structures = next();
    else if (a === '--px-per-cell') opts.pxPerCell = Number(next());
    else if (a === '--screen') {
      const [w, h] = next().split('x').map(Number);
      opts.screen = { w, h };
    } else if (a === '--fov') opts.fov = Number(next());
    else if (a === '--distance') opts.distance = Number(next());
    else if (a === '--pitch') opts.pitch = Number(next());
    else if (a === '--refit-yaw') opts.refitYaw = Number(next());
    else if (a === '--keep') opts.keep = Number(next());
    else if (a === '--no-lens') opts.lens = false;
    else if (a === '--freeze') opts.freeze = true;
    else if (a === '--keep-intro') opts.keepIntro = true;
    else if (a === '--markers') opts.markers = true;
    else if (a === '--show-ui') opts.showUi = true;
    else if (a === '--margin') opts.margin = Number(next());
    else if (a === '--crop-margin') opts.cropMargin = Number(next());
    else if (a === '--out') opts.out = next();
    else if (!a.startsWith('--') && !opts.map) opts.map = a;
    else throw new Error(`unknown option ${a}`);
  }
  if (!opts.map) throw new Error('usage: node inject.mjs "<map name or .stormmap path>" [options]');
  if (!['keep', 'hide'].includes(opts.structures)) throw new Error('--structures is keep or hide');
  if (!(opts.keep > 0 && opts.keep <= 1)) throw new Error('--keep is between 0 and 1');
  return opts;
}

const slug = (s) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');

async function resolveMap(map, outDir) {
  if (map.toLowerCase().endsWith('.stormmap')) {
    if (!existsSync(map)) throw new Error(`no such file: ${map}`);
    return { file: resolve(map), name: basename(map, '.stormmap') };
  }
  const dir = join(outDir, 'original');
  mkdirSync(dir, { recursive: true });
  const file = resolve(dir, `${map}.stormmap`);
  if (!existsSync(file)) {
    const url = `${S2MA_MAPS}/${encodeURIComponent(map)}.stormmap`;
    console.error(`downloading ${url}`);
    const res = await fetch(url);
    if (!res.ok)
      throw new Error(`${res.status} downloading "${map}" — check the name against ${S2MA_MAPS}`);
    writeFileSync(file, Buffer.from(await res.arrayBuffer()));
  }
  return { file, name: map };
}

/**
 * The yaw of the lighting-refit look: facing the map's main light (see light-data.mjs), or
 * 180 with a warning when the light can't be found.
 */
function resolveRefitYaw(archive) {
  const read = (name) => {
    try {
      return archive.readFile(name).toString('utf8');
    } catch {
      return null;
    }
  };
  const table = JSON.parse(readFileSync(new URL('./light-sets.json', import.meta.url), 'utf8'));
  const light = mainLight(
    {
      t3Terrain: read('t3Terrain.xml') || '',
      terrainData: read('Base.StormData\\GameData\\TerrainData.xml'),
      lightData: read('Base.StormData\\GameData\\LightData.xml'),
    },
    table,
  );
  if (light.yaw === null) {
    console.error(`warning: the map's main light wasn't found (tileset ${light.tileset}, light set ${light.lighting}); the refit look faces yaw 180. Pass --refit-yaw, or regenerate light-sets.json.`);
    return 180;
  }
  console.error(`main light: tileset ${light.tileset}, light set ${light.lighting}, from ${light.yaw.toFixed(0)} degrees (the refit look faces it)`);
  return Math.round(light.yaw);
}

/**
 * MapInfo: map size at bytes 16 and 20, and the camera bounds (left, bottom, right, top) as
 * four uint32 right after a string. The bounds' offset moves with the strings before them, so
 * take the first aligned-to-a-string quadruple that fits the map; this matches all 35 current
 * battlegrounds and brawls.
 */
function readMapInfo(buf) {
  const w = buf.readUInt32LE(16);
  const h = buf.readUInt32LE(20);
  for (let o = 32; o + 16 <= buf.length; o++) {
    if (buf[o - 1] !== 0) continue;
    const [l, b, r, t] = [0, 4, 8, 12].map((k) => buf.readUInt32LE(o + k));
    if (l < r && r <= w && b < t && t <= h && r - l >= w / 3 && t - b >= h / 3) {
      return { width: w, height: h, bounds: { left: l, bottom: b, right: r, top: t } };
    }
  }
  return { width: w, height: h, bounds: { left: 0, bottom: 0, right: w, top: h } };
}

/**
 * A map that is several arenas in one (Punisher Arena: one arena per round, stacked on the
 * map, the camera bounds moved to the round's arena at run time) marks each arena with a
 * region named "..._MapBounds" in its Regions file. Two or more of them: capture areas, each
 * rendered to its own image. `<quad value="left,bottom,right,top"/>`.
 */
function parseAreas(regionsXml) {
  const areas = [];
  for (const m of regionsXml.matchAll(/<region\b[\s\S]*?<\/region>/g)) {
    const name = m[0].match(/<name value="([^"]*)"/)?.[1];
    const quad = m[0].match(/<shape type="rect">[\s\S]*?<quad value="([^"]*)"/)?.[1];
    if (!name || !quad || !/MapBounds$/i.test(name)) continue;
    const [left, bottom, right, top] = quad.split(',').map(Number);
    if ([left, bottom, right, top].some(Number.isNaN) || left >= right || bottom >= top) continue;
    areas.push({ name: name.replace(/_?MapBounds$/i, ''), bounds: { left, bottom, right, top } });
  }
  areas.sort((a, b) => a.name.localeCompare(b.name));
  return areas.length >= 2 ? areas : [];
}

/**
 * Camera and grid. Straight down, the camera sees a world rectangle of height
 * 2·distance·tan(fov/2) at its target; picking the resolution fixes that height
 * (screen height / px-per-cell), and so the distance. Each screenshot contributes its centre
 * `keep` share at most.
 *
 * Camera targets stay inside the map's camera bounds, since the game clamps any beyond them
 * (which duplicated the edge columns). So the first and last columns sit on the bounds and the
 * rest are spread evenly between, at most `keep` of a screen apart; the kept strips then reach
 * half a spacing past the bounds, which the screenshots still cover.
 */
function planGrid(opts, info) {
  const viewH = opts.screen.h / opts.pxPerCell;
  const viewW = opts.screen.w / opts.pxPerCell;
  if (opts.distance) {
    // The field of view that shows viewH cells from that distance.
    opts.fov = (2 * Math.atan(viewH / 2 / opts.distance) * 180) / Math.PI;
  }
  const distance = viewH / 2 / Math.tan((opts.fov * Math.PI) / 180 / 2);
  const m = opts.margin;
  const area = {
    left: info.bounds.left - m,
    bottom: info.bounds.bottom - m,
    right: info.bounds.right + m,
    top: info.bounds.top + m,
  };
  const spread = (span, maxStep) => {
    const count = Math.max(1, Math.ceil(span / maxStep) + 1);
    return { count, step: count > 1 ? span / (count - 1) : maxStep };
  };
  const across = spread(area.right - area.left, viewW * opts.keep);
  const down = spread(area.top - area.bottom, viewH * opts.keep);
  const tiles = [];
  for (let row = 0; row < down.count; row++) {
    for (let col = 0; col < across.count; col++) {
      tiles.push({
        index: tiles.length,
        row,
        col,
        x: area.left + col * across.step,
        y: area.top - row * down.step, // row 0 is the top (north) edge
      });
    }
  }
  return {
    distance,
    step: { x: across.step, y: down.step },
    cols: across.count,
    rows: down.count,
    tiles,
    area,
  };
}

/**
 * Galaxy is single-pass: a function must be defined before any call to it, and the game gives
 * no error when it isn't; the whole map script silently fails and the map runs with no
 * triggers at all (which shows as every interface panel visible at once). Refuse to emit a
 * script where any hrsCap_ function is called before its definition.
 */
function checkDefinitionOrder(script) {
  const defined = new Map();
  for (const m of script.matchAll(/^(?:void|bool|int|fixed|string|text) (hrsCap_\w+) \(/gm))
    defined.set(m[1], m.index);
  for (const m of script.matchAll(/\b(hrsCap_[A-Za-z]\w*)\s*\(/g)) {
    const at = defined.get(m[1]);
    if (at === undefined) continue; // a variable, or a Blizzard function
    if (m.index < at) {
      const line = script.slice(0, m.index).split('\n').length;
      throw new Error(`capture script: ${m[1]} is used at line ${line} before it is defined`);
    }
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  mkdirSync(opts.out, { recursive: true });
  const source = await resolveMap(opts.map, opts.out);
  const id = `${slug(source.name)}-${opts.structures === 'hide' ? 'terrain' : 'structures'}`;
  const target = resolve(opts.out, `${id}.stormmap`);
  copyFileSync(source.file, target);

  const archive = Archive.open(target);
  try {
    const info = readMapInfo(archive.readFile('MapInfo'));
    const refitYaw = opts.refitYaw ?? resolveRefitYaw(archive);
    // Whether the void shows the sky (the tileset has a skybox): then each tile is shot over
    // white and over black and the difference is the transparency. Otherwise the void is
    // terrain drawn black; one shot over black, and the stitch makes that black transparent.
    const skyMode = hasSky(
      { t3Terrain: archive.hasFile('t3Terrain.xml') ? archive.readFileAsString('t3Terrain.xml') : '', terrainData: archive.hasFile('Base.StormData\\GameData\\TerrainData.xml') ? archive.readFileAsString('Base.StormData\\GameData\\TerrainData.xml') : null },
      JSON.parse(readFileSync(new URL('./light-sets.json', import.meta.url), 'utf8')),
    ) ? 'matte' : 'black';
    console.error(skyMode === 'matte' ? 'void: sky (each tile shot over white and black)' : 'void: black terrain (one shot over black)');
    let areas = [];
    try {
      areas = parseAreas(archive.readFileAsString('Regions'));
    } catch {
      // no Regions file: one area, the camera bounds
    }
    let grid;
    if (areas.length) {
      // One grid per area, rows numbered on from the last area's with a gap row between, so
      // the stitch never takes the last row of one area for a neighbour of the first of the
      // next; the camera bounds are lifted (unbound) so the camera can reach every area.
      grid = null;
      let rowBase = 0;
      for (const area of areas) {
        const g = planGrid(opts, { ...info, bounds: area.bounds });
        for (const t of g.tiles) {
          t.index += grid?.tiles.length ?? 0;
          t.row += rowBase;
          t.area = areas.indexOf(area);
        }
        Object.assign(area, { cols: g.cols, rows: g.rows, firstTile: grid?.tiles.length ?? 0, tileCount: g.tiles.length });
        rowBase += g.rows + 1;
        grid = grid
          ? {
              ...grid,
              tiles: grid.tiles.concat(g.tiles),
              cols: Math.max(grid.cols, g.cols),
              rows: rowBase - 1,
              area: {
                left: Math.min(grid.area.left, g.area.left),
                bottom: Math.min(grid.area.bottom, g.area.bottom),
                right: Math.max(grid.area.right, g.area.right),
                top: Math.max(grid.area.top, g.area.top),
              },
            }
          : g;
      }
    } else {
      grid = planGrid(opts, info);
    }
    const unbound = opts.margin > 0 || areas.length > 0;
    // Far clip well past the camera, with a floor so the chat "zoom" command can pull back freely.
    const lens = opts.lens
      ? { fov: opts.fov, farClip: Math.max(800, Math.ceil(grid.distance * 3)) }
      : null;
    // Numbered markers, spread irregularly over the view. Each tile is shot twice, with them and
    // without, so they may sit anywhere on screen; within 0.32 of the view from its centre keeps
    // most of them on screen when the game holds the camera back at an edge.
    const viewW = opts.screen.w / opts.pxPerCell;
    const viewH = opts.screen.h / opts.pxPerCell;
    const LAYOUT = [
      [-0.32, -0.32],
      [-0.25, 0.05],
      [-0.32, 0.29],
      [0.07, -0.3],
      [-0.09, 0.32],
      [0.32, -0.26],
      [0.27, 0.08],
      [0.32, 0.32],
    ];
    const markers = opts.markers
      ? LAYOUT.map(([i, j]) => ({ dx: i * viewW, dy: j * viewH }))
      : null;
    // The part of each kept screenshot the stitch may use: all of it, since the kept (clean)
    // shot has no markers in it.
    const pageShare = null;

    // Cloud layers are doodads placed in the map (Battlefield of Eternity: 20 Storm_Doodad_Heaven_Clouds);
    // the script hides every doodad type with "cloud" in its name.
    let hideDoodads = [];
    try {
      hideDoodads = [...new Set([...archive.readFileAsString('Objects').matchAll(/<ObjectDoodad [^>]*Type="([^"]*[Cc]loud[^"]*)"/g)].map((m) => m[1]))];
    } catch {
      // no Objects file
    }
    if (hideDoodads.length) console.error(`cloud doodads hidden: ${hideDoodads.join(', ')}`);

    const original = archive.readFileAsString('MapScript.galaxy');
    const init = original.lastIndexOf('void InitMap () {');
    if (init < 0) throw new Error('MapScript.galaxy has no InitMap; not a battleground script?');
    const eol = original.includes('\r\n') ? '\r\n' : '\n';
    const close = original.indexOf(`${eol}}`, init);
    const script = captureScript({
      tiles: grid.tiles,
      hideStructures: opts.structures === 'hide',
      distance: grid.distance,
      pitch: opts.pitch,
      refitYaw,
      lens,
      unbound,
      keepMechanics: opts.keepMechanics,
      freeze: opts.freeze,
      showUi: opts.showUi,
      keepIntro: opts.keepIntro,
      markers,
      sky: true,
      skyColour: skyMode === 'matte' ? 'white' : 'black',
      hideDoodads,
    }).replace(/\n/g, eol);
    checkDefinitionOrder(script);
    // Galaxy is single-pass: the capture functions go before InitMap, the call at its end.
    const patched =
      original.slice(0, init) +
      script +
      original.slice(init, close) +
      `${eol}    hrsCap_Init();` +
      original.slice(close);
    const ok = archive.addBuffer('MapScript.galaxy', Buffer.from(patched, 'utf8'), {
      flags: MPQ_FILE_REPLACEEXISTING | MPQ_FILE_COMPRESS,
      compression: MPQ_COMPRESSION_ZLIB,
    });
    if (!ok) throw new Error('could not replace MapScript.galaxy');

    // The status strip's cells are a white texture tinted per cell (capture-script.mjs).
    const white = archive.addBuffer('Assets\\Textures\\HrsWhite.dds', solidDds([255, 255, 255], 64, 64), {
      flags: MPQ_FILE_REPLACEEXISTING | MPQ_FILE_COMPRESS,
      compression: MPQ_COMPRESSION_ZLIB,
    });
    if (!white) throw new Error('could not add the status strip texture');

    // The solid-colour skyboxes (sky.mjs): the capture shoots each tile over white and over
    // black, and the stitch turns the difference into transparency.
    {
      const tileset = (archive.readFileAsString('t3Terrain.xml').match(/\btileSet="([^"]+)"/i) || [])[1];
      if (!tileset) throw new Error('t3Terrain.xml names no tileset; cannot set the skybox');
      const read = (name) => (archive.hasFile(name) ? archive.readFileAsString(name) : null);
      for (const file of skyFiles(tileset, skyMode === 'matte' ? 'white' : 'black', read)) {
        const added = archive.addBuffer(file.name, file.data, {
          flags: MPQ_FILE_REPLACEEXISTING | MPQ_FILE_COMPRESS,
          compression: MPQ_COMPRESSION_ZLIB,
        });
        if (!added) throw new Error(`could not add ${file.name}`);
      }
      console.error(`skyboxes: ${Object.keys(SKIES).join(', ')} (tileset ${tileset})`);
    }

    const manifest = {
      map: source.name,
      id,
      stormmap: target,
      structures: opts.structures,
      screen: opts.screen,
      pxPerCell: opts.pxPerCell,
      fov: opts.lens ? opts.fov : null,
      keep: opts.keep,
      distance: grid.distance,
      pitch: opts.pitch,
      refitYaw,
      mapSize: { width: info.width, height: info.height },
      cameraBounds: info.bounds,
      areas: areas.length ? areas : null,
      unbound,
      cropMargin: opts.cropMargin,
      area: grid.area,
      step: grid.step,
      cols: grid.cols,
      rows: grid.rows,
      tiles: grid.tiles,
      markers,
      pageShare,
      keepIntro: opts.keepIntro,
      sky: { mode: skyMode, start: skyMode === 'matte' ? 'white' : 'black', colours: Object.keys(SKIES) },
      hideDoodads,
      // The status strip sits in the top-left corner; this many pixels of each screenshot's left
      // edge are blanked by the capture and left out by the stitch (status.py, stitch.py).
      status: { cells: STATUS_CELLS, cellUnits: STATUS_CELL_UNITS, pageLeft: 64 },
    };
    const manifestPath = resolve(opts.out, `${id}.json`);
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

    console.error(`${source.name}: ${info.width}x${info.height} cells, camera bounds`, info.bounds);
    for (const a of areas) {
      console.error(`  area ${a.name}: ${a.cols}x${a.rows} tiles, cells ${a.bounds.left}-${a.bounds.right} x ${a.bounds.bottom}-${a.bounds.top}`);
    }
    console.error(
      `camera distance ${grid.distance.toFixed(1)}, ${grid.cols}x${grid.rows} = ${grid.tiles.length} tiles, ` +
        `output about ${Math.round(grid.cols * grid.step.x * opts.pxPerCell)}x` +
        `${Math.round(grid.rows * grid.step.y * opts.pxPerCell)} px`,
    );
    console.error(`map      ${target}`);
    // The one line on stdout, so scripts (render.cmd) can pick the manifest up.
    console.log(manifestPath);
  } finally {
    archive.close();
  }
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
