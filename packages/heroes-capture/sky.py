"""Solid-colour skyboxes, for chroma keying and difference matting (see BACKGROUND-PLAN.md).

A skybox is an ordinary model: `<CModel id=".." parent="Skybox">` pointing at a stock mesh whose
textures are referenced by path. A file inside the map archive at that same path is used instead
of the game's, so each colour gets its own stock mesh and a solid-colour DDS at every texture path
that mesh uses. The map's tileset loses its parallax layer and its fog (which would tint the sky);
the script sets the camera-fixed skybox with GameSetBackground: white at each tile, black for the
second clean shot, chat "sky <colour>" for probes.
"""

import struct

TEXTURES = "Assets\\Textures\\"
SKYBOXES = "Assets\\Skyboxes\\"

# Colour name -> the stock mesh it uses and that mesh's textures, each painted with the colour, or
# made fully transparent ('clear': cloud and star layers the mesh draws over its base). Only meshes
# that enclose a straight-down camera are any use: the Braxis bowl and the "parallax" bowls (700
# units across). The Heaven and Luxoria skyboxes are one and the same 4300-unit bowl, and a swap to
# it at run time never takes (the sky stays as it was); the Luxoria SkyDark/SkyLight meshes are flat
# planes at one height (SkyLight sat in front of the camera and tinted the whole screen). Meshes
# sharing a texture must agree on its paint. A texture painted here is painted for every model in
# the map that uses it, so none may be one a map's own sky uses: a cyan made from Hanamura's
# parallax mesh blanked Hanamura's sky and the cloud textures of Battlefield of Eternity's parallax
# sky (Storm_Heaven_SkyParallax_*). Chat "sky none" (no skybox at all) draws plain black too.
SKIES = {
    "black": {
        "rgb": (0, 0, 0),
        "mesh": f"{SKYBOXES}Storm_Skybox_ArenaHeaven_Parallax\\Storm_Skybox_ArenaHeaven_Parallax.m3",
        "textures": {"Storm_Skybox_ArenaHeaven_Parallax": "colour", "Storm_Skybox_ArenaHeaven_Clouds_Diffuse": "clear"},
    },
    "white": {
        "rgb": (255, 255, 255),
        "mesh": f"{SKYBOXES}Storm_Skybox_ArenaHell_Parallax\\Storm_Skybox_ArenaHell_Parallax.m3",
        "textures": {"Storm_Skybox_ArenaHell_Parallax": "colour", "Storm_Skybox_ArenaHell_Clouds_Hell_Diffuse": "clear"},
    },
    "magenta": {
        "rgb": (255, 0, 255),
        "mesh": f"{SKYBOXES}Storm_Skybox_SCBraxis\\Storm_Skybox_SCBraxis.m3",
        "textures": {"Storm_Doodad_SCBraxis_Skybox_Diff": "colour", "Storm_Doodad_SCBraxis_Skybox_Stars_Diff": "clear"},
    },
    "lime": {
        "rgb": (0, 255, 0),
        "mesh": f"{SKYBOXES}Storm_Skybox_ArenaHvH_Parallax\\Storm_Skybox_ArenaHvH_Parallax.m3",
        "textures": {
            "Storm_Skybox_ArenaHvH_Parallax": "colour",
            "Storm_Skybox_ArenaHell_Clouds_Hell_Diffuse": "clear",
            "Storm_Skybox_ArenaHeaven_Clouds_Diffuse": "clear",
        },
    },
}


def model_id(colour: str) -> str:
    return f"HrsSky{colour[0].upper()}{colour[1:]}"


# Our white and black skies again at larger scales (models HrsSkyWhitex3, ...; chat "sky whitex3"):
# a bigger shell sits further out, so it can be a key behind the map's own sky shells (the parallax
# draws in front of a 4300-unit bowl, behind our 700-unit ones).
SCALED = {"colours": ["white", "black"], "scales": [3, 10]}

# The map's own parallax sky models we can make keyed copies of: the model file (a copy of the
# game's, in local-assets/, fetched for probes and never committed) and its background texture.
# Each copy points that texture at a white or a black one (chat "sky parallaxwhite" /
# "sky parallaxblack") while sharing the haze textures, so the haze can be matted over white and
# black within one match. The name is replaced by one of the same length, which leaves the rest of
# the model file valid.
PARALLAX_KEYS = {
    "HeavenSkyboxParallax": {
        "file": "Storm_Doodad_Heaven_SkyParallax.m3",
        "base": "Storm_Heaven_SkyParallax_Base_Diffuse",
        "haze": ["Storm_Heaven_SkyParallax_Clouds_Diffuse", "Storm_Heaven_SkyParallax_Clouds_Hell_Diffuse"],
    },
}

# The keyed copies (chat "sky parallax<name>"): what each puts in place of the background art (None:
# the real art) and whether the haze stays. white/black: the haze over white and black (its matte);
# bare: the background art without the haze (its own layer); whitebare: white without the haze (the
# white level the game's lighting gives the key, for an exact matte).
KEY_VARIANTS = {
    "white": {"base": "white", "haze": True},
    "black": {"base": "black", "haze": True},
    "bare": {"base": None, "haze": False},
    "whitebare": {"base": "white", "haze": False},
}


def _key_name(like: str, stem: str, tag: str) -> str:
    """A texture name of the same length as `like`, starting with the same stem, tagged `tag`."""
    length = len(like) - len(stem)
    if len(tag) > length:
        raise ValueError(f"key tag {tag} doesn't fit in {like}")
    return f"{stem}{tag}{'0' * (length - len(tag))}"


def _rename_texture(m3: bytearray, old: str, new: str) -> None:
    """Every occurrence of "/<old>.dds" in the model file replaced by "/<new>.dds" (same length)."""
    needle = f"/{old}.dds".encode("latin-1")
    count, at = 0, m3.find(needle)
    while at >= 0:
        m3[at + 1 : at + 1 + len(new)] = new.encode("latin-1")
        count += 1
        at = m3.find(needle, at + 1)
    if not count:
        raise ValueError(f"{old}.dds not found in the model file")


def _model_entry(id_: str, path: str, extra: str = "") -> str:
    return f'    <CModel id="{id_}" parent="Skybox">\n        <Model value="{path}"/>\n{extra}    </CModel>'


def parallax_keys(model: str, m3: bytes) -> dict:
    """Model entries and files for the keyed copies of a parallax model (see PARALLAX_KEYS and
    KEY_VARIANTS), from the model file's bytes. Names are replaced by ones of the same length, so
    the rest of the model file stays valid."""
    spec = PARALLAX_KEYS[model]
    base, haze = spec["base"], spec["haze"]
    stem = base[: base.rindex("_", 0, base.rindex("_")) + 1]  # "..._SkyParallax_"
    models, files = [], []
    textures: dict[str, str] = {}  # key texture name -> 'white' | 'black' | 'clear'
    for variant, plan in KEY_VARIANTS.items():
        copy = bytearray(m3)
        if plan["base"]:
            name = _key_name(base, stem, "HrsKeyWhite" if plan["base"] == "white" else "HrsKeyBlack")
            _rename_texture(copy, base, name)
            textures[name] = plan["base"]
        if not plan["haze"]:
            for k, layer in enumerate(haze):
                name = _key_name(layer, stem, f"HrsClear{k}")
                _rename_texture(copy, layer, name)
                textures[name] = "clear"
        path = f"Assets\\Skyboxes\\HrsParallaxKeys\\{model}_{variant}.m3"
        models.append(_model_entry(model_id(f"parallax{variant}"), path))
        files.append((path, bytes(copy)))
    for name, paint in textures.items():
        make = clear_dds if paint == "clear" else (lambda w, h, rgb=SKIES[paint]["rgb"]: solid_dds(rgb, w, h))
        files.append((f"{TEXTURES}{name}.dds", make(1024, 512)))
        files.append((f"{TEXTURES}{name}.lvl0", make(64, 32)))
    return {"models": models, "files": files}


def _block_dds(four_cc: str, block: bytes, width: int, height: int) -> bytes:
    """A DDS of one repeated 4x4 block with a full mip chain, the layout of the game's own skybox
    textures (1024x512, 11 levels; the streaming `.lvl0` copy is 64x32, 7 levels)."""
    levels = []
    w, h = width, height
    while True:
        levels.append(block * (-(-w // 4) * -(-h // 4)))
        if w == 1 and h == 1:
            break
        w, h = max(1, w >> 1), max(1, h >> 1)
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)  # header size
    struct.pack_into("<I", header, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000)  # caps, height, width, pixel format, mipmap count, linear size
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, len(levels[0]))
    struct.pack_into("<I", header, 28, len(levels))
    struct.pack_into("<I", header, 76, 32)  # pixel format size
    struct.pack_into("<I", header, 80, 0x4)  # four-character code follows
    header[84:88] = four_cc.encode("ascii")
    struct.pack_into("<I", header, 108, 0x8 | 0x400000 | 0x1000)  # complex, mipmaps, texture
    return bytes(header) + b"".join(levels)


def _js_round(value: float) -> int:
    """Math.round: halves go up."""
    return int(value // 1 + (1 if value % 1 >= 0.5 else 0))


def solid_dds(rgb, width: int = 1024, height: int = 512) -> bytes:
    """A DXT1 DDS of one colour."""
    r, g, b = rgb
    c565 = ((_js_round(r * 31 / 255) << 11) | (_js_round(g * 63 / 255) << 5) | _js_round(b * 31 / 255)) & 0xFFFF
    block = struct.pack("<HH", c565, c565) + bytes(4)  # both colours the same, every index 0: the whole block is c565
    return _block_dds("DXT1", block, width, height)


def clear_dds(width: int = 1024, height: int = 512) -> bytes:
    """A DXT5 DDS that is fully transparent (every texel alpha 0)."""
    return _block_dds("DXT5", bytes(16), width, height)  # alpha0 = alpha1 = 0, every index 0; colour block all zero


def painted_texture_files(paints: dict[str, str]) -> list[tuple[str, bytes]]:
    """Files that paint textures of the map's own sky one solid colour or fully transparent
    (inject.py --paint-texture, sky probes). A sky model's look comes from several textures:
    Battlefield of Eternity's parallax model has its background art
    (Storm_Heaven_SkyParallax_Base_Diffuse) and two smoke layers
    (Storm_Heaven_SkyParallax_Clouds_Diffuse, ..._Clouds_Hell_Diffuse) in one mesh, so the smoke is
    separated from its background by painting textures, not by switching layers. `paints` maps a
    texture name to a colour of SKIES or "clear"."""
    ours = {name for sky in SKIES.values() for name in sky["textures"]}
    files = []
    for name, paint in paints.items():
        if name in ours:
            raise ValueError(f"{name} is one of our own sky textures (sky.py SKIES)")
        if paint == "clear":
            make = clear_dds
        elif paint in SKIES:
            make = lambda w, h, rgb=SKIES[paint]["rgb"]: solid_dds(rgb, w, h)  # noqa: E731
        else:
            raise ValueError(f"unknown paint {paint}; one of {', '.join(SKIES)}, clear")
        files += [(f"{TEXTURES}{name}.dds", make(1024, 512)), (f"{TEXTURES}{name}.lvl0", make(64, 32))]
    return files


def append_to_catalog(existing: str | None, entries: list[str]) -> str:
    """Append entries to a catalog XML (the map's own, if it has one), keeping it well-formed."""
    eol = "\r\n" if existing and "\r\n" in existing else "\n"
    if existing and "</Catalog>" in existing:
        return existing.replace("</Catalog>", f"{eol.join(entries)}{eol}</Catalog>", 1)
    return f'<?xml version="1.0" encoding="us-ascii"?>{eol}<Catalog>{eol}{eol.join(entries)}{eol}</Catalog>{eol}'


def sky_files(tileset: str, start: str, read, extra: dict | None = None) -> list[tuple[str, bytes]]:
    """The files to add to the map for the solid-colour skies: model entries, the tileset override
    (sky drawn under the map, starting colour, no parallax layer, no fog) and the textures.
    `read(name)` returns the map's current copy of a file or None; `extra` adds model entries and
    files (the keyed parallax copies)."""
    extra = extra or {"models": [], "files": []}
    files = list(extra["files"])
    models = [_model_entry(model_id(colour), sky["mesh"]) for colour, sky in SKIES.items()]
    for colour in SCALED["colours"]:
        for s in SCALED["scales"]:
            scale = (f'        <ScaleMax X="{s}.000000" Y="{s}.000000" Z="{s}.000000"/>\n'
                     f'        <ScaleMin X="{s}.000000" Y="{s}.000000" Z="{s}.000000"/>\n')
            models.append(_model_entry(model_id(f"{colour}x{s}"), SKIES[colour]["mesh"], scale))
    models += extra["models"]
    files.append(("Base.StormData\\GameData\\ModelData.xml",
                  append_to_catalog(read("Base.StormData\\GameData\\ModelData.xml"), models).encode("utf-8")))
    # (FixedSkyboxModel here never took effect; the script sets the sky with GameSetBackground.
    # HideLowestLevel: the lowest terrain level isn't drawn, so the sky shows there; on a map
    # without a sky of its own the void is that level, drawn black, and would stay opaque.)
    terrain = [
        f'    <CTerrain id="{tileset}">',
        '        <HideLowestLevel value="1"/>',
        f'        <FixedSkyboxModel value="{model_id(start)}"/>',
        '        <NonFixedSkyboxModel value=""/>',
        '        <FogEnabled value="0"/>',
        "    </CTerrain>",
    ]
    files.append(("Base.StormData\\GameData\\TerrainData.xml",
                  append_to_catalog(read("Base.StormData\\GameData\\TerrainData.xml"), terrain).encode("utf-8")))
    textures: dict[str, object] = {}  # path -> rgb or 'clear'; a texture two meshes share must agree
    for sky in SKIES.values():
        for name, paint in sky["textures"].items():
            want = "clear" if paint == "clear" else sky["rgb"]
            if name in textures and textures[name] != want:
                raise ValueError(f"sky texture {name} is wanted two ways")
            textures[name] = want
    for name, want in textures.items():
        make = clear_dds if want == "clear" else (lambda w, h, rgb=want: solid_dds(rgb, w, h))
        files += [(f"{TEXTURES}{name}.dds", make(1024, 512)), (f"{TEXTURES}{name}.lvl0", make(64, 32))]
    return files
