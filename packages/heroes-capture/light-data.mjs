/**
 * The map's main light, for the lighting-refit look (see capture-script.mjs): the map's
 * tileset (t3Terrain.xml) names a light set (CTerrain Lighting), and the light set's "Key"
 * directional light has a direction. Tileset and light-set definitions live in the game's
 * data (light-sets.json, built by generate-light-sets.mjs); a map can override either in its
 * own TerrainData.xml and LightData.xml.
 */

/** CTerrain entries: id -> { parent?, lighting? }. */
export function parseTerrains(xml, into = {}) {
  for (const { attrs, body } of entries(xml, 'CTerrain')) {
    if (!attrs.id) continue;
    const t = (into[attrs.id] ||= {});
    if (attrs.parent) t.parent = attrs.parent;
    const l = body.match(/<Lighting value="([^"]*)"/);
    if (l) t.lighting = l[1];
  }
  return into;
}

/** CLight entries: id -> { parent?, key?: [x, y, z] } (the first time of day's Key light). */
export function parseLights(xml, into = {}) {
  for (const { attrs, body } of entries(xml, 'CLight')) {
    if (!attrs.id) continue;
    const l = (into[attrs.id] ||= {});
    if (attrs.parent) l.parent = attrs.parent;
    const tod = body.match(/<ToDInfoArray index="0"[^>]*>([\s\S]*?)<\/ToDInfoArray>/);
    const key = keyDirection(tod ? tod[1] : body);
    if (key) l.key = key;
  }
  return into;
}

function keyDirection(xml) {
  // <DirectionalLight index="Key" ... Direction="x,y,z"/>, or with a child
  // <Direction value="x,y,z"/> or <Direction X=".." Y=".." Z=".."/>.
  const m = xml.match(/<DirectionalLight index="Key"(\s[^>]*?)?(\/>|>([\s\S]*?)<\/DirectionalLight>)/);
  if (!m) return null;
  const attr = (m[1] || '').match(/Direction="([^"]+)"/);
  if (attr) return attr[1].split(',').map(Number);
  const body = m[3] || '';
  const value = body.match(/<Direction value="([^"]+)"/);
  if (value) return value[1].split(',').map(Number);
  const xyz = body.match(/<Direction X="([^"]+)" Y="([^"]+)" Z="([^"]+)"/);
  return xyz ? [Number(xyz[1]), Number(xyz[2]), Number(xyz[3])] : null;
}

function entries(xml, tag) {
  const out = [];
  const re = new RegExp(`<${tag}\\s+([^>]*?)(/>|>([\\s\\S]*?)</${tag}>)`, 'g');
  for (const m of xml.matchAll(re)) {
    const attrs = Object.fromEntries([...m[1].matchAll(/(\w+)="([^"]*)"/g)].map((a) => [a[1], a[2]]));
    out.push({ attrs, body: m[3] || '' });
  }
  return out;
}

/** A field of a definition, or of its parents. */
function inherited(table, id, field, seen = new Set()) {
  const def = table[id];
  if (!def || seen.has(id)) return undefined;
  seen.add(id);
  if (def[field] !== undefined) return def[field];
  return def.parent ? inherited(table, def.parent, field, seen) : undefined;
}

/**
 * The map's main light and the yaw that faces it. `mapFiles` are the map's own t3Terrain.xml,
 * TerrainData.xml and LightData.xml (the last two optional); `table` is light-sets.json.
 * Returns { tileset, lighting, key, yaw } with what could be resolved; yaw is null if not.
 */
export function mainLight(mapFiles, table) {
  const tileset = (mapFiles.t3Terrain.match(/\btileSet="([^"]+)"/i) || [])[1] || null;  // <heightMap tileSet="...">
  // Copies, so a map's overrides never leak into the table.
  const terrains = structuredClone(table.terrains);
  const lights = structuredClone(table.lights);
  if (mapFiles.terrainData) parseTerrains(mapFiles.terrainData, terrains);
  if (mapFiles.lightData) parseLights(mapFiles.lightData, lights);
  // A tileset that names no light set uses the one with its own name (Sky Temple's
  // StormEgyptWorld).
  const lighting = tileset ? inherited(terrains, tileset, 'lighting') || (lights[tileset] ? tileset : undefined) : undefined;
  const key = lighting ? inherited(lights, lighting, 'key') : undefined;
  let yaw = null;
  if (key) {
    // The light travels along `key`; the look faces where it comes from. Yaw 90 looks along +y.
    yaw = (Math.atan2(-key[1], -key[0]) * 180) / Math.PI;
    yaw = ((yaw % 360) + 360) % 360;
  }
  return { tileset, lighting: lighting || null, key: key || null, yaw };
}
