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
 *   --keep <0..1>            share of each screenshot used, centred           (default 0.6)
 *   --no-lens                leave field of view and far clip to the map
 *   --keep-mechanics         keep map-mechanic units such as altars (experimental)
 *   --freeze                 pause model animations (experimental)
 *   --markers                show registration markers around each screenshot (experimental)
 *   --show-ui                leave the HUD up (diagnostic)
 *   --margin <cells>         capture past the camera bounds (lifts them)      (default 0)
 *   --out <dir>              working folder                                   (default work)
 */
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import {
  Archive,
  MPQ_COMPRESSION_ZLIB,
  MPQ_FILE_COMPRESS,
  MPQ_FILE_REPLACEEXISTING,
} from '@jamiephan/stormlib';
import { captureScript } from './capture-script.mjs';

const S2MA_MAPS = 'https://raw.githubusercontent.com/jamiephan/HeroesOfTheStorm_S2MA/main/maps';

function parseArgs(argv) {
  const opts = {
    map: null,
    structures: 'keep',
    pxPerCell: 48,
    screen: { w: 3840, h: 2160 },
    fov: 20,
    keep: 0.6,
    lens: true,
    freeze: false,
    keepMechanics: false,
    markers: false,
    showUi: false,
    margin: 0,
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
    else if (a === '--keep') opts.keep = Number(next());
    else if (a === '--no-lens') opts.lens = false;
    else if (a === '--freeze') opts.freeze = true;
    else if (a === '--keep-mechanics') opts.keepMechanics = true;
    else if (a === '--markers') opts.markers = true;
    else if (a === '--show-ui') opts.showUi = true;
    else if (a === '--margin') opts.margin = Number(next());
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
    const grid = planGrid(opts, info);
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

    const original = archive.readFileAsString('MapScript.galaxy');
    const init = original.lastIndexOf('void InitMap () {');
    if (init < 0) throw new Error('MapScript.galaxy has no InitMap; not a battleground script?');
    const eol = original.includes('\r\n') ? '\r\n' : '\n';
    const close = original.indexOf(`${eol}}`, init);
    const script = captureScript({
      tiles: grid.tiles,
      hideStructures: opts.structures === 'hide',
      distance: grid.distance,
      lens,
      unbound: opts.margin > 0,
      keepMechanics: opts.keepMechanics,
      freeze: opts.freeze,
      showUi: opts.showUi,
      markers,
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
      mapSize: { width: info.width, height: info.height },
      cameraBounds: info.bounds,
      area: grid.area,
      step: grid.step,
      cols: grid.cols,
      rows: grid.rows,
      tiles: grid.tiles,
      markers,
      pageShare,
    };
    const manifestPath = resolve(opts.out, `${id}.json`);
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

    console.error(`${source.name}: ${info.width}x${info.height} cells, camera bounds`, info.bounds);
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
