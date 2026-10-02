/**
 * Build light-sets.json: for every tileset in the game, its light set and skybox, for every light
 * set the direction of its main ("Key") light. inject.mjs uses it to point the lighting-refit look
 * at a map's main light (--refit-yaw) without a per-map setting.
 *
 *   node generate-light-sets.mjs <dir with the game's TerrainData.xml and LightData.xml files>
 *
 * The files come from the game's data (mods/.../GameData/TerrainData.xml and LightData.xml in
 * every mod), extracted from its CASC storage; file names are the CASC paths with the
 * separators replaced by "__", so that base mods sort before the battleground mods that
 * override them. A field set later replaces one set earlier; missing fields come from a
 * definition's parent.
 */
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { parseLights, parseTerrains } from './light-data.mjs';

const dir = process.argv[2];
if (!dir) throw new Error('usage: node generate-light-sets.mjs <dir>');

const terrains = {}; // id -> { parent, lighting }
const lights = {}; // id -> { parent, key: [x, y, z] }

const order = (name) => (/mods__core/i.test(name) ? 0 : /mods__heroesdata/i.test(name) ? 1 : /mods__heroes\.stormmod/i.test(name) ? 2 : 3);
const files = readdirSync(dir).sort((a, b) => order(a) - order(b) || a.localeCompare(b));
for (const name of files) {
  const xml = readFileSync(join(dir, name), 'utf8');
  if (/terraindata\.xml$/i.test(name)) parseTerrains(xml, terrains);
  else if (/lightdata\.xml$/i.test(name)) parseLights(xml, lights);
}

const out = { generated: new Date().toISOString().slice(0, 10), terrains, lights };
writeFileSync(new URL('./light-sets.json', import.meta.url), JSON.stringify(out, null, 1) + '\n');
console.log(`${Object.keys(terrains).length} tilesets, ${Object.keys(lights).length} light sets, from ${files.length} files`);
