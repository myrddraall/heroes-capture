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
 * A texture painted here is painted for every model in the map that uses it, so none may be one
 * a map's own sky uses: a cyan made from Hanamura's parallax mesh blanked Hanamura's sky and
 * the cloud textures of Battlefield of Eternity's parallax sky (Storm_Heaven_SkyParallax_*).
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
};

export const modelId = (colour) => `HrsSky${colour[0].toUpperCase()}${colour.slice(1)}`;

/**
 * Our white and black skies again at larger scales (models HrsSkyWhitex3, ...; chat
 * "sky whitex3"): a bigger shell sits further out, so it can be a key behind the map's own
 * sky shells (the parallax draws in front of a 4300-unit bowl, behind our 700-unit ones).
 */
const SCALED = { colours: ['white', 'black'], scales: [3, 10] };

/**
 * The map's own parallax sky models we can make keyed copies of: the model file (a copy of the
 * game's, in local-assets/, fetched for probes and never committed) and its background texture.
 * Each copy points that texture at a white or a black one (chat "sky parallaxwhite" /
 * "sky parallaxblack") while sharing the haze textures, so the haze can be matted over white
 * and black within one match. The name is replaced by one of the same length, which leaves the
 * rest of the model file valid.
 */
export const PARALLAX_KEYS = {
  HeavenSkyboxParallax: {
    file: 'Storm_Doodad_Heaven_SkyParallax.m3',
    base: 'Storm_Heaven_SkyParallax_Base_Diffuse',
    haze: ['Storm_Heaven_SkyParallax_Clouds_Diffuse', 'Storm_Heaven_SkyParallax_Clouds_Hell_Diffuse'],
  },
};

/**
 * The keyed copies (chat "sky parallax<name>"): what each puts in place of the background art
 * (null: the real art) and whether the haze stays. white/black: the haze over white and black
 * (its matte); bare: the background art without the haze (its own layer); whitebare: white
 * without the haze (the white level the game's lighting gives the key, for an exact matte).
 */
const KEY_VARIANTS = {
  white: { base: 'white', haze: true },
  black: { base: 'black', haze: true },
  bare: { base: null, haze: false },
  whitebare: { base: 'white', haze: false },
};

/** A texture name of the same length as `like`, starting with the same stem, tagged `tag`. */
function keyName(like, stem, tag) {
  const length = like.length - stem.length;
  if (tag.length > length) throw new Error(`key tag ${tag} doesn't fit in ${like}`);
  return `${stem}${tag}${'0'.repeat(length - tag.length)}`;
}

/** Every occurrence of "/<from>.dds" in the model file replaced by "/<to>.dds" (same length). */
function renameTexture(m3, from, to) {
  const needle = Buffer.from(`/${from}.dds`, 'latin1');
  let count = 0;
  for (let at = m3.indexOf(needle); at >= 0; at = m3.indexOf(needle, at + 1)) {
    m3.write(to, at + 1, 'latin1');
    count++;
  }
  if (!count) throw new Error(`${from}.dds not found in the model file`);
}

/**
 * Model entries and files for the keyed copies of a parallax model (see PARALLAX_KEYS and
 * KEY_VARIANTS), from the model file's bytes. Names are replaced by ones of the same length, so
 * the rest of the model file stays valid.
 */
export function parallaxKeys(model, m3) {
  const { base, haze } = PARALLAX_KEYS[model];
  const stem = base.slice(0, base.lastIndexOf('_', base.lastIndexOf('_') - 1) + 1); // "..._SkyParallax_"
  const models = [];
  const textures = new Map(); // key texture name -> 'white' | 'black' | 'clear'
  const files = [];
  for (const [variant, { base: paint, haze: keepHaze }] of Object.entries(KEY_VARIANTS)) {
    const copy = Buffer.from(m3);
    if (paint) {
      const name = keyName(base, stem, paint === 'white' ? 'HrsKeyWhite' : 'HrsKeyBlack');
      renameTexture(copy, base, name);
      textures.set(name, paint);
    }
    if (!keepHaze) {
      haze.forEach((h, k) => {
        const name = keyName(h, stem, `HrsClear${k}`);
        renameTexture(copy, h, name);
        textures.set(name, 'clear');
      });
    }
    const path = `Assets\\Skyboxes\\HrsParallaxKeys\\${model}_${variant}.m3`;
    models.push(`    <CModel id="${modelId(`parallax${variant}`)}" parent="Skybox">\n        <Model value="${path}"/>\n    </CModel>`);
    files.push({ name: path, data: copy });
  }
  for (const [name, paint] of textures) {
    const make = paint === 'clear' ? clearDds : (w, h) => solidDds(SKIES[paint].rgb, w, h);
    files.push({ name: `${TEXTURES}${name}.dds`, data: make(1024, 512) });
    files.push({ name: `${TEXTURES}${name}.lvl0`, data: make(64, 32) });
  }
  return { models, files };
}

/**
 * A DDS of one repeated 4x4 block with a full mip chain, the layout of the game's own skybox
 * textures (1024x512, 11 levels; the streaming `.lvl0` copy is 64x32, 7 levels).
 */
function blockDds(fourCC, block, width, height) {
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
  header.write(fourCC, 84, 'ascii');
  header.writeUInt32LE(0x8 | 0x400000 | 0x1000, 108); // complex, mipmaps, texture
  return Buffer.concat([header, ...levels]);
}

/** A DXT1 DDS of one colour. */
export function solidDds([r, g, b], width = 1024, height = 512) {
  const c565 = ((Math.round((r * 31) / 255) << 11) | (Math.round((g * 63) / 255) << 5) | Math.round((b * 31) / 255)) & 0xffff;
  const block = Buffer.alloc(8);
  block.writeUInt16LE(c565, 0);
  block.writeUInt16LE(c565, 2); // both colours the same, every index 0: the whole block is c565
  return blockDds('DXT1', block, width, height);
}

/** A DXT5 DDS that is fully transparent (every texel alpha 0). */
export function clearDds(width = 1024, height = 512) {
  return blockDds('DXT5', Buffer.alloc(16), width, height); // alpha0 = alpha1 = 0, every index 0; colour block all zero
}

/**
 * Files that paint textures of the map's own sky one solid colour or fully transparent
 * (inject.mjs --paint-texture, sky probes). A sky model's look comes from several textures:
 * Battlefield of Eternity's parallax model has its background art
 * (Storm_Heaven_SkyParallax_Base_Diffuse) and two smoke layers
 * (Storm_Heaven_SkyParallax_Clouds_Diffuse, ..._Clouds_Hell_Diffuse) in one mesh, so the smoke
 * is separated from its background by painting textures, not by switching layers.
 * `paints` maps a texture name to a colour of SKIES or "clear".
 */
export function paintedTextureFiles(paints) {
  const ours = new Set(Object.values(SKIES).flatMap((sky) => Object.keys(sky.textures)));
  return Object.entries(paints).flatMap(([name, paint]) => {
    if (ours.has(name)) throw new Error(`${name} is one of our own sky textures (sky.mjs SKIES)`);
    const make = paint === 'clear' ? clearDds : SKIES[paint] ? (w, h) => solidDds(SKIES[paint].rgb, w, h) : null;
    if (!make) throw new Error(`unknown paint ${paint}; one of ${Object.keys(SKIES).join(', ')}, clear`);
    return [
      { name: `${TEXTURES}${name}.dds`, data: make(1024, 512) },
      { name: `${TEXTURES}${name}.lvl0`, data: make(64, 32) },
    ];
  });
}

/** Append entries to a catalog XML (the map's own, if it has one), keeping it well-formed. */
export function appendToCatalog(existing, entries) {
  const eol = existing?.includes('\r\n') ? '\r\n' : '\n';
  if (existing && existing.includes('</Catalog>')) {
    return existing.replace('</Catalog>', `${entries.join(eol)}${eol}</Catalog>`);
  }
  return `<?xml version="1.0" encoding="us-ascii"?>${eol}<Catalog>${eol}${entries.join(eol)}${eol}</Catalog>${eol}`;
}

/**
 * The files to add to the map for the solid-colour skies: model entries, the tileset override
 * (sky drawn under the map, starting colour, no parallax layer, no fog) and the textures.
 * `read(name)` returns the map's current copy of a file or null; `extra` adds model entries and
 * files (the keyed parallax copies).
 */
export function skyFiles(tileset, start, read, extra = { models: [], files: [] }) {
  const files = [...extra.files];
  const models = Object.entries(SKIES).map(
    ([colour, sky]) => `    <CModel id="${modelId(colour)}" parent="Skybox">\n        <Model value="${sky.mesh}"/>\n    </CModel>`,
  );
  for (const colour of SCALED.colours) {
    for (const s of SCALED.scales) {
      models.push(
        `    <CModel id="${modelId(`${colour}x${s}`)}" parent="Skybox">\n        <Model value="${SKIES[colour].mesh}"/>\n` +
          `        <ScaleMax X="${s}.000000" Y="${s}.000000" Z="${s}.000000"/>\n        <ScaleMin X="${s}.000000" Y="${s}.000000" Z="${s}.000000"/>\n    </CModel>`,
      );
    }
  }
  models.push(...extra.models);
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
