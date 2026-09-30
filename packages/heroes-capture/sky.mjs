/**
 * Solid-colour skyboxes, for chroma keying and difference matting (see BACKGROUND-PLAN.md).
 *
 * A skybox is an ordinary model: `<CModel id=".." parent="Skybox">` pointing at a stock mesh
 * whose textures are referenced by path. A file inside the map archive at that same path is
 * used instead of the game's, so each colour gets its own stock mesh and a solid-colour DDS at
 * every texture path that mesh uses. The map's tileset loses its parallax layer and its fog
 * (which would tint the sky); the script sets the camera-fixed skybox with GameSetBackground:
 * white at each tile, black for the second clean shot, chat "sky <colour>" for probes.
 */

const TEXTURES = 'Assets\\Textures\\';
const SKYBOXES = 'Assets\\Skyboxes\\';

/**
 * Colour name -> the stock mesh it uses and that mesh's textures, each painted with the colour,
 * or made fully transparent ('clear': cloud and star layers the mesh draws over its base).
 * Only meshes that enclose a straight-down camera are any use: the Braxis bowl and the
 * "parallax" bowls (700 units across). The Heaven and Luxoria skyboxes are one and the same
 * 4300-unit bowl, and a swap to it at run time never takes (the sky stays as it was); the
 * Luxoria SkyDark/SkyLight meshes are flat planes at one height (SkyLight sat in front of the
 * camera and tinted the whole screen). Meshes sharing a texture must agree on its paint.
 * Chat "sky none" (no skybox at all) draws plain black too.
 */
export const SKIES = {
  black: {
    rgb: [0, 0, 0],
    mesh: `${SKYBOXES}Storm_Skybox_ArenaHeaven_Parallax\\Storm_Skybox_ArenaHeaven_Parallax.m3`,
    textures: { Storm_Skybox_ArenaHeaven_Parallax: 'colour', Storm_Skybox_ArenaHeaven_Clouds_Diffuse: 'clear' },
  },
  white: {
    rgb: [255, 255, 255],
    mesh: `${SKYBOXES}Storm_Skybox_ArenaHell_Parallax\\Storm_Skybox_ArenaHell_Parallax.m3`,
    textures: { Storm_Skybox_ArenaHell_Parallax: 'colour', Storm_Skybox_ArenaHell_Clouds_Hell_Diffuse: 'clear' },
  },
  magenta: {
    rgb: [255, 0, 255],
    mesh: `${SKYBOXES}Storm_Skybox_SCBraxis\\Storm_Skybox_SCBraxis.m3`,
    textures: { Storm_Doodad_SCBraxis_Skybox_Diff: 'colour', Storm_Doodad_SCBraxis_Skybox_Stars_Diff: 'clear' },
  },
  lime: {
    rgb: [0, 255, 0],
    mesh: `${SKYBOXES}Storm_Skybox_ArenaHvH_Parallax\\Storm_Skybox_ArenaHvH_Parallax.m3`,
    textures: {
      Storm_Skybox_ArenaHvH_Parallax: 'colour',
      Storm_Skybox_ArenaHell_Clouds_Hell_Diffuse: 'clear',
      Storm_Skybox_ArenaHeaven_Clouds_Diffuse: 'clear',
    },
  },
  cyan: {
    rgb: [0, 255, 255],
    mesh: `${SKYBOXES}Storm_Skybox_OWHana_Parallax\\Storm_Skybox_OWHana_Parallax.m3`,
    textures: {
      Storm_Skybox_OWHana_Diffuse: 'colour',
      Storm_Heaven_SkyParallax_Clouds_Diffuse: 'clear',
      Storm_Heaven_SkyParallax_Clouds_Hell_Diffuse: 'clear',
    },
  },
};

export const modelId = (colour) => `HrsSky${colour[0].toUpperCase()}${colour.slice(1)}`;

/**
 * A DXT1 DDS of one colour with a full mip chain, the format of the game's own skybox
 * textures (1024x512, 11 levels; the streaming `.lvl0` copy is 64x32, 7 levels).
 */
export function solidDds([r, g, b], width = 1024, height = 512) {
  const c565 = ((Math.round((r * 31) / 255) << 11) | (Math.round((g * 63) / 255) << 5) | Math.round((b * 31) / 255)) & 0xffff;
  const block = Buffer.alloc(8);
  block.writeUInt16LE(c565, 0);
  block.writeUInt16LE(c565, 2); // both colours the same, every index 0: the whole block is c565
  const levels = [];
  for (let w = width, h = height; ; w = Math.max(1, w >> 1), h = Math.max(1, h >> 1)) {
    levels.push(Buffer.concat(Array(Math.ceil(w / 4) * Math.ceil(h / 4)).fill(block)));
    if (w === 1 && h === 1) break;
  }
  const header = Buffer.alloc(128);
  header.write('DDS ', 0, 'ascii');
  header.writeUInt32LE(124, 4); // header size
  header.writeUInt32LE(0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, 8); // caps, height, width, pixel format, mipmap count, linear size
  header.writeUInt32LE(height, 12);
  header.writeUInt32LE(width, 16);
  header.writeUInt32LE(levels[0].length, 20);
  header.writeUInt32LE(levels.length, 28);
  header.writeUInt32LE(32, 76); // pixel format size
  header.writeUInt32LE(0x4, 80); // four-character code follows
  header.write('DXT1', 84, 'ascii');
  header.writeUInt32LE(0x8 | 0x400000 | 0x1000, 108); // complex, mipmaps, texture
  return Buffer.concat([header, ...levels]);
}

/** A DXT5 DDS that is fully transparent (every texel alpha 0), with a full mip chain. */
export function clearDds(width = 1024, height = 512) {
  const block = Buffer.alloc(16); // alpha0 = alpha1 = 0, every alpha index 0; colour block all zero
  const levels = [];
  for (let w = width, h = height; ; w = Math.max(1, w >> 1), h = Math.max(1, h >> 1)) {
    levels.push(Buffer.concat(Array(Math.ceil(w / 4) * Math.ceil(h / 4)).fill(block)));
    if (w === 1 && h === 1) break;
  }
  const header = Buffer.alloc(128);
  header.write('DDS ', 0, 'ascii');
  header.writeUInt32LE(124, 4);
  header.writeUInt32LE(0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, 8);
  header.writeUInt32LE(height, 12);
  header.writeUInt32LE(width, 16);
  header.writeUInt32LE(levels[0].length, 20);
  header.writeUInt32LE(levels.length, 28);
  header.writeUInt32LE(32, 76);
  header.writeUInt32LE(0x4, 80);
  header.write('DXT5', 84, 'ascii');
  header.writeUInt32LE(0x8 | 0x400000 | 0x1000, 108);
  return Buffer.concat([header, ...levels]);
}

/** Append entries to a catalog XML (the map's own, if it has one), keeping it well-formed. */
function appendToCatalog(existing, entries) {
  const eol = existing?.includes('\r\n') ? '\r\n' : '\n';
  if (existing && existing.includes('</Catalog>')) {
    return existing.replace('</Catalog>', `${entries.join(eol)}${eol}</Catalog>`);
  }
  return `<?xml version="1.0" encoding="us-ascii"?>${eol}<Catalog>${eol}${entries.join(eol)}${eol}</Catalog>${eol}`;
}

/**
 * The files to add to the map for the solid-colour skies: model entries, the tileset override
 * (sky drawn under the map, starting colour, no parallax layer, no fog) and the textures.
 * `read(name)` returns the map's current copy of a file or null.
 */
export function skyFiles(tileset, start, read) {
  const files = [];
  const models = Object.entries(SKIES).map(
    ([colour, sky]) => `    <CModel id="${modelId(colour)}" parent="Skybox">\n        <Model value="${sky.mesh}"/>\n    </CModel>`,
  );
  files.push({
    name: 'Base.StormData\\GameData\\ModelData.xml',
    data: Buffer.from(appendToCatalog(read('Base.StormData\\GameData\\ModelData.xml'), models), 'utf8'),
  });
  // (FixedSkyboxModel here never took effect; the script sets the sky with GameSetBackground.
  // HideLowestLevel: the lowest terrain level isn't drawn, so the sky shows there; on a map
  // without a sky of its own the void is that level, drawn black, and would stay opaque.)
  const terrain = [
    `    <CTerrain id="${tileset}">`,
    `        <HideLowestLevel value="1"/>`,
    `        <FixedSkyboxModel value="${modelId(start)}"/>`,
    `        <NonFixedSkyboxModel value=""/>`,
    `        <FogEnabled value="0"/>`,
    `    </CTerrain>`,
  ];
  files.push({
    name: 'Base.StormData\\GameData\\TerrainData.xml',
    data: Buffer.from(appendToCatalog(read('Base.StormData\\GameData\\TerrainData.xml'), terrain), 'utf8'),
  });
  const textures = new Map(); // path -> rgb or 'clear'; a texture two meshes share must agree
  for (const sky of Object.values(SKIES)) {
    for (const [name, paint] of Object.entries(sky.textures)) {
      const want = paint === 'clear' ? 'clear' : sky.rgb.join();
      const before = textures.get(name);
      if (before && before !== want) throw new Error(`sky texture ${name} is wanted two ways`);
      textures.set(name, want);
    }
  }
  for (const [name, want] of textures) {
    const make = want === 'clear' ? clearDds : (w, h) => solidDds(want.split(',').map(Number), w, h);
    files.push({ name: `${TEXTURES}${name}.dds`, data: make(1024, 512) });
    files.push({ name: `${TEXTURES}${name}.lvl0`, data: make(64, 32) });
  }
  return files;
}
