"""The map pack (PACK.md at the repository root): what a map viewer loads, written from the
stitch's images at the end of every stitch.

    <output-dir>/<map id>/pack/   pack.json, a tile pyramid and an overview per layer
                                  (<layer>.pmtiles, <layer>-overview.webp), the fixed skybox
                                  (fixed.webp), a thumbnail, the custom minimap as SVG (images/)
    <output-dir>/<map id>/raw/    the layers (and composites) as full-resolution PNGs, layers.json,
                                  the game's own pictures of the map (images/), the reference viewer
                                  (index.html)

A render with the structures hidden goes to <map id>/terrain/ the same way. The stitch's own
files (named by the render's id) are moved into raw/ or removed.
"""

import hashlib
import io
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pyvips
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from heroes_capture.capture import minimap_svg
from heroes_capture.capture import viewer
from heroes_capture.capture.team_colour import MASK_SCALE, TEAM_COLOURS
from heroes_capture.common import game_data
from heroes_capture.common import ui
from heroes_capture.common.jsonio import read_json, write_json
from heroes_capture.common.runlog import log, stage, warn
from heroes_capture.common.stormlib import Archive
from heroes_capture.common.workers import ordered_map
from heroes_capture.map.specifics import Specifics

FORMAT = 6
TILE = 512  # tile size in pixels
QUALITY = 90  # the fixed skybox's and the elements' atlases' WebP quality
# The tiles' and overviews' WebP encoding (chosen from side-by-side crops of a render): the map's
# and the background art's colour lossy, their transparency exact; the haze, mostly transparency,
# lossy in that too.
TILE_WEBP = {"Q": 85, "alpha_q": 100}
HAZE_WEBP = {"Q": 90, "alpha_q": 50}
THUMBNAIL_WIDTH = 512
# The loading screen's pictures a viewer shows (pack images/): the picture lossy, opaque; its icons
# exact, with their transparency (small, drawn with sharp edges).
LOADING_SCREEN_WEBP = {"Q": 85}
LOADING_SCREEN_ICON_WEBP = {"lossless": True}
SMALLER = (2, 4)  # each also at a half and a quarter of its size, for a viewer to pick the one it needs
OVERVIEW_MAP = 2048  # a map layer's overview: its finest pyramid level no longer than this on a side (the opening view)
OVERVIEW_MAX = 4096  # a sky layer's overview: the level the opening view needs, no longer than this on a side
ATLAS_WIDTH = 4096  # the elements' atlases are at most this wide
BACKDROP = [48, 48, 48]  # what the thumbnail's transparency is shown over
SKY = ("background", "haze")  # the parallax layers, back to front
COMPOSITES = ("", "-on-black", "-with-fixed")  # the stitch's composites: <id>-composite<suffix>.png

# The map's pictures in its archive: pack key, file in the archive, file in the pack. The custom
# minimap's files are the ones MapInfo names (a .tga on some maps); these are the usual names.
ARCHIVE_IMAGES = (
    ("minimap", "Minimap.tga", "minimap.png"),
    ("customMinimap", "CustomMiniMap.dds", "custom-minimap.png"),
    ("customMinimapHover", "CustomMiniMap_Hover.dds", "custom-minimap-hover.png"),
    ("replayPreview", "ReplaysPreviewImage.tga", "replay-preview.png"),
)


def variant_folder(out: Path, structures: str) -> Path:
    """Where a render's pack and raw layers go in its map's folder: the map itself, terrain/ for
    the structures hidden, or elements/ for the elements render (ELEMENTS-PLAN.md)."""
    return {"hide": out / "terrain", "elements": out / "elements"}.get(structures, out)


def is_written(out: Path, structures: str) -> bool:
    """Whether the map's folder has this variant's pack (a render already done)."""
    return (variant_folder(out, structures) / "pack" / "pack.json").exists()


def levels_for(width: int, height: int) -> int:
    """How many levels a pyramid of an image this size has: the finest is the image, each one
    below half of it, down to one that fits in a tile."""
    longest = max(width, height)
    return 1 if longest <= TILE else math.ceil(math.log2(longest / TILE)) + 1


def rgba(image: pyvips.Image) -> pyvips.Image:
    if image.bands == 3:
        image = image.bandjoin(255)
    return image.cast("uchar")


def level_size(width: int, height: int, levels: int, level: int) -> tuple[int, int]:
    """A pyramid level's size in its own pixels."""
    factor = 2 ** (levels - 1 - level)
    return math.ceil(width / factor), math.ceil(height / factor)


def overview_level(width: int, height: int, wanted: int, longest: int) -> int:
    """The level of a layer's overview (PACK.md, Overviews): `wanted`, or the finest coarser level
    no longer than `longest` on a side; never the finest level (its tiles are the archive's), but
    on a layer of one level."""
    levels = levels_for(width, height)
    wanted = min(wanted, max(0, levels - 2))
    return max(level for level in range(wanted + 1) if max(level_size(width, height, levels, level)) <= longest or level == 0)


def map_overview_level(width: int, height: int) -> int:
    """A map layer's overview level: its finest level no longer than OVERVIEW_MAP on a side."""
    return overview_level(width, height, levels_for(width, height) - 1, OVERVIEW_MAP)


def sky_overview_level(width: int, height: int, rate: float, opening_zoom: float) -> int:
    """A parallax layer's overview level: the level a viewer draws it at in the opening view, the
    zoom at which the map layer's overview is drawn a pixel to a window pixel (further back, the sky
    is drawn larger than the map: PACK.md, Layers and drawing), within OVERVIEW_MAX."""
    levels = levels_for(width, height)
    s = 1 / (1 + rate * (1 / opening_zoom - 1))
    return overview_level(width, height, levels - 1 - math.floor(math.log2(1 / s)), OVERVIEW_MAX)


def tile_webp(layer_id: str) -> dict:
    """A tiled layer's WebP encoding."""
    return HAZE_WEBP if layer_id == "haze" else TILE_WEBP


def write_pyramid(png: Path, dest: Path, name: str, overview: int, webp: dict, mask: bool = False) -> dict | None:
    """A PNG as a PMTiles tile pyramid (PACK.md, Tile pyramids): the image padded with transparency
    to whole blocks of the coarsest level's pixel, each level a box-filtered downscale of it (on
    premultiplied colour, so transparent edges don't darken). Level `overview` whole as one
    picture, the overview; the levels finer than it cut into tiles, the empty ones left out, the
    rest WebP (encoded with `webp`). The layer's size, level count, tile size and overview; None if
    it is empty.

    A team-colour `mask` (team_colour.py: its amounts in red and green, opaque) is averaged as it
    is, padded with zero amounts, and a tile of it is empty where both amounts are."""
    image = rgba(pyvips.Image.new_from_file(str(png)))
    width, height = image.width, image.height
    pad = [0, 0, 0, 255] if mask else [0, 0, 0, 0]

    def empty(picture: pyvips.Image) -> bool:
        return (picture.extract_band(0, n=2) if mask else picture.extract_band(3)).max() == 0

    if empty(image):
        return None
    levels = levels_for(width, height)
    block = 2 ** (levels - 1)
    padded = image.embed(0, 0, math.ceil(width / block) * block, math.ceil(height / block) * block,
                         extend="background", background=pad)
    tiles = []
    premultiplied = padded.premultiply()
    for level in range(levels - 1, overview - 1, -1):
        # Each level halves the one finer (the same box filter as shrinking the image by the level's
        # factor at once, the image read once).
        if level == levels - 1:
            scaled = padded.copy_memory()
        else:
            premultiplied = premultiplied.shrink(2, 2).copy_memory()
            scaled = premultiplied.unpremultiply().cast("uchar").copy_memory()
        if level == overview:
            # The overview (PACK.md, Overviews): the level whole, as one picture, so a viewer has it
            # in one request; the same pixels its tiles would have.
            level_w, level_h = level_size(width, height, levels, level)
            overview_path = dest.with_name(f"{dest.stem}-overview.webp")
            scaled.crop(0, 0, level_w, level_h).webpsave(str(overview_path), keep="none", **webp)
            if level < levels - 1:
                break
        cols, rows = math.ceil(scaled.width / TILE), math.ceil(scaled.height / TILE)

        def encode(xy, scaled=scaled, level=level):
            x, y = xy
            tile = scaled.crop(x * TILE, y * TILE, min(TILE, scaled.width - x * TILE), min(TILE, scaled.height - y * TILE))
            if empty(tile):
                return None  # empty: not stored (a missing tile is transparent, or no team colour)
            if tile.width < TILE or tile.height < TILE:
                tile = tile.embed(0, 0, TILE, TILE, extend="background", background=pad)
            return zxy_to_tileid(level, x, y), tile.webpsave_buffer(keep="none", **webp)

        with ui.bar(cols * rows, f"{name}, level {level + 1} of {levels}") as advance:
            for result in ordered_map(encode, [(x, y) for y in range(rows) for x in range(cols)]):
                if result:
                    tiles.append(result)
                advance()
        del scaled
    tiles.sort(key=lambda t: t[0])
    meta = {"size": [width, height], "levels": levels, "tileSize": TILE,
            "overview": {"file": f"{dest.stem}-overview.webp", "level": overview, "size": list(level_size(width, height, levels, overview))}}
    with open(dest, "wb") as f:
        writer = Writer(f)
        for tile_id, data in tiles:
            writer.write_tile(tile_id, data)
        writer.finalize({"tile_type": TileType.WEBP, "tile_compression": Compression.NONE, "center_zoom": 0},
                        {"name": name, "format": "webp", "size": meta["size"], "levels": levels, "tileSize": TILE})
    return meta


def pack_atlas(pictures: list[tuple[str, pyvips.Image]], max_width: int = ATLAS_WIDTH, gap: int = 0) -> tuple[pyvips.Image, dict[str, tuple[int, int]]]:
    """Pictures (RGBA) packed into one atlas, in shelves: the tallest first, each shelf filled left
    to right up to `max_width`, the next shelf below, `gap` transparent pixels between pictures.
    Returns the atlas and where each picture's top-left corner is in it, by key."""
    rows: list[list[tuple[str, pyvips.Image]]] = []
    row_width = lambda row: sum(p.width for _, p in row) + gap * (len(row) - 1)  # noqa: E731
    for key, picture in sorted(pictures, key=lambda kp: (-kp[1].height, kp[0])):
        if rows and row_width(rows[-1]) + gap + picture.width <= max_width:
            rows[-1].append((key, picture))
        else:
            rows.append([(key, picture)])
    width = max((row_width(row) for row in rows), default=1)
    height = max(1, sum(row[0][1].height for row in rows) + gap * (len(rows) - 1))
    atlas = pyvips.Image.black(width, height, bands=4)
    at: dict[str, tuple[int, int]] = {}
    y = 0
    for row in rows:
        x = 0
        for key, picture in row:
            atlas = atlas.insert(picture, x, y)
            at[key] = (x, y)
            x += picture.width + gap
        y += row[0][1].height + gap
    return atlas, at


def _png(data: bytes) -> tuple[bytes, list[int]] | None:
    """A game picture (TGA, DDS, PNG) as PNG bytes and its size; None if it can't be read."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # noqa: BLE001 - a picture the decoder doesn't know is left out
        return None
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue(), list(image.size)


def map_images(stormmap: Path | None, storage, folder: Path) -> dict:
    """The map's own pictures (PACK.md, Images) into `folder` as PNG: those in its archive, the
    map-select picture DocumentInfo names, and from the game's textures (through `storage`) its
    loading screen and the icons the loading screen's layout places. The loading screen is the
    one MapInfo names, or else the picture of the game's map entry it names (game_data.map_entries:
    Battlefield of Eternity, Towers of Doom, Hanamura Temple). Each {file, size, source}; the icons
    a list. Any the map hasn't (or that can't be read) is left out."""
    found: dict = {}
    if not stormmap or not stormmap.exists():
        return found

    def keep(key: str, name: str, data: bytes | None, source: str) -> dict | None:
        converted = _png(data) if data else None
        if not converted:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(converted[0])
        return {"file": f"images/{name}", "size": converted[1], "source": source}

    aliases = game_data.asset_aliases(storage) if storage is not None else {}

    def texture(path: str) -> bytes | None:
        """A game texture, by its path (asset names resolved first: game_data.asset_aliases)."""
        if storage is None or path.startswith("@"):  # an asset name the game's lists don't have
            return None
        hits = storage.find("*" + Path(path.replace("\\", "/")).name.lower())
        return storage.read(hits[0]) if hits else None

    try:
        archive = Archive(stormmap)
    except OSError as e:
        warn(f"the map's pictures couldn't be read ({e}); the pack has none")
        return found
    with archive:
        info = archive.read_optional("MapInfo") or b""
        named = {bool(m.group(1)): m.group(0).decode() for m in re.finditer(rb"(?i)customminimap(_hover)?\.(?:dds|tga)", info)}
        for key, inside, name in ARCHIVE_IMAGES:
            if key.startswith("customMinimap"):
                inside = named.get(key.endswith("Hover"), inside)
            if archive.has(inside) and (entry := keep(key, name, archive.read(inside), inside)):
                found[key] = entry
        document = archive.read_text("DocumentInfo") or ""
        pictures = re.findall(r"<Value>([^<]+\.(?:png|dds|tga))</Value>", document)
        select = next((p for p in pictures if "mapselect" in p.lower()), pictures[0] if pictures else None)
        if select and archive.has(select) and (entry := keep("mapSelect", "map-select.png", archive.read(select), select)):
            found["mapSelect"] = entry
    screen = re.search(rb"Assets[\\/]Textures[\\/]([\w.-]+\.dds)", info)
    name = screen.group(1).decode() if screen else None
    if name is None and storage is not None:
        # MapInfo names the map's entry instead, as a short string (its length, a zero byte, the id).
        entries = game_data.map_entries(storage)
        named = [i for i in entries if bytes([len(i)]) + b"\x00" + i.encode() in info]
        if named:
            name = entries[max(named, key=len)]
    name = aliases.get(name[1:], name) if name and name.startswith("@") else name
    if name and (entry := keep("loadingScreen", "loading-screen.png", texture(name), name.split("\\")[-1])):
        found["loadingScreen"] = entry
    # The loading screen's layout (UI/Layout/LoadingScreens/<map>.StormLayout, or on Towers of Doom
    # UI/Layout/TowersofDoom_Loading.StormLayout); its first picture is the loading screen itself on some.
    layout = re.search(rb"(?i)UI/Layout/(?:[\w.-]+/)*([\w.-]*loading[\w.-]*\.StormLayout)", info)
    if layout and storage is not None:
        hits = storage.find("*" + layout.group(1).decode().lower())
        text = (storage.read(hits[0]) or b"").decode("utf-8", errors="replace") if hits else ""
        files = (aliases.get(v[1:], v) if v.startswith("@") else v
                 for v in re.findall(r'Texture val="([^"]+)"', text) if v.lower().endswith(".dds") or v.startswith("@"))
        screen_file = (found.get("loadingScreen") or {}).get("source", "").lower()
        icons = [f for f in dict.fromkeys(files) if f.split("\\")[-1].lower() != screen_file]
        key = re.search(r"(?:homescreenbackground|loadscreen)_(\w+)\.dds$", screen_file)
        if not icons and key:
            # The layout MapInfo names isn't in the game's data (Warhead Junction's): the icons by
            # the loading screen's name, ui_ingame_mapmechanic_loadscreen_<its last part>_icon<n>.
            icons = sorted(storage.find(f"*\\ui_ingame_mapmechanic_loadscreen_{key.group(1)}_icon*.dds"))
        entries = [e for n, icon in enumerate(icons, 1)
                   if (e := keep("loadingScreenIcons", f"loading-screen-icon-{n}.png", texture(icon), icon.split("\\")[-1]))]
        if entries:
            found["loadingScreenIcons"] = entries
    return found


def loading_screen_images(raw: Path, pack: Path, pictures: dict) -> dict:
    """The loading screen and its icons (map_images' PNGs in raw/) as WebP in the pack's images/,
    for a viewer: {loadingScreen, loadingScreenIcons} as found, each {file, size, source, smaller}:
    the picture at its own size, and `smaller`, the same at each of SMALLER's fractions of it
    (<name>-<width>w.webp), largest first."""
    def webp(entry: dict, options: dict) -> dict:
        stem = Path(entry["file"]).stem
        (pack / "images").mkdir(parents=True, exist_ok=True)
        picture = rgba(pyvips.Image.new_from_file(str(raw / entry["file"])))
        if not options.get("lossless"):
            picture = picture.flatten(background=[0, 0, 0])
        picture.webpsave(str(pack / "images" / f"{stem}.webp"), keep="none", **options)
        smaller = []
        for factor in SMALLER:
            width, height = max(1, round(picture.width / factor)), max(1, round(picture.height / factor))
            small = picture.thumbnail_image(width, height=height, size="force")  # transparency premultiplied
            name = f"{stem}-{small.width}w.webp"
            small.webpsave(str(pack / "images" / name), keep="none", **options)
            smaller.append({"file": f"images/{name}", "size": [small.width, small.height]})
        return {**entry, "file": f"images/{stem}.webp", "smaller": smaller}

    found: dict = {}
    if "loadingScreen" in pictures:
        found["loadingScreen"] = webp(pictures["loadingScreen"], LOADING_SCREEN_WEBP)
    if pictures.get("loadingScreenIcons"):
        found["loadingScreenIcons"] = [webp(e, LOADING_SCREEN_ICON_WEBP) for e in pictures["loadingScreenIcons"]]
    return found


MINIMAP_BELOW_CAMERA = 2.25  # cells the custom minimap's middle lies below the camera bounds' (measured)


def custom_minimap_bounds(picture: Image.Image, map_size: dict, camera: dict, correction: dict | None = None) -> dict:
    """The map cells the custom minimap covers (PACK.md, The custom minimap as SVG). It is drawn at
    the map's scale, a whole number of pixels per cell (the picture's width over the map's), and
    centred on the camera bounds' middle, MINIMAP_BELOW_CAMERA lower (measured over the maps with
    one, against their renders); then the map's own correction, cells added to each edge (maps/)."""
    k = picture.width / map_size["width"]
    w, h = picture.width / k, picture.height / k
    left = (camera["left"] + camera["right"]) / 2 - w / 2
    top = (camera["bottom"] + camera["top"]) / 2 - MINIMAP_BELOW_CAMERA + h / 2
    bounds = {"left": left, "bottom": top - h, "right": left + w, "top": top}
    correction = correction or {}
    return {edge: round(float(value + correction.get(edge, 0)), 3) for edge, value in bounds.items()}


def minimap_svg_image(raw: Path, pack: Path, custom: dict, map_size: dict, camera: dict, correction: dict | None = None) -> dict | None:
    """The custom minimap (its PNG in raw/) redrawn as an SVG (minimap_svg) in the pack: its entry
    (PACK.md, Images), with the map cells it covers; None, with a warning, if it can't be redrawn."""
    picture = Image.open(raw / custom["file"])
    try:
        svg = minimap_svg.to_svg(picture)
    except Exception as e:  # noqa: BLE001 - the pack is written without it
        warn(f"the custom minimap couldn't be redrawn as an SVG ({e}); the pack has only its PNG")
        return None
    (pack / "images").mkdir(parents=True, exist_ok=True)
    (pack / "images" / "custom-minimap.svg").write_text(svg, encoding="utf-8")
    return {"file": "images/custom-minimap.svg", "size": custom["size"], "source": custom["source"],
            "boundsCells": custom_minimap_bounds(picture, map_size, camera, correction)}


def _version() -> str:
    try:
        from ._version import VERSION
        return VERSION
    except ImportError:
        return "0.0.0-dev"


def collect_raw(out: Path, raw: Path, out_id: str, images: list[str]) -> tuple[list[dict], list[dict] | None]:
    """The stitch's files moved into raw/ under plain names, the ones the pack doesn't keep
    removed. The layers (back to front, as pack.json lists them, `file` naming the raw PNG) and
    the arenas (None on a map of one)."""
    raw.mkdir(parents=True, exist_ok=True)
    layers, arenas, maps = [], [], []
    old_layers = out / f"{out_id}-layers.json"
    sky = read_json(old_layers, {})
    if sky.get("fixed") and (out / sky["fixed"]["image"]).exists():
        shutil.move(out / sky["fixed"]["image"], raw / "fixed.png")
        size = Image.open(raw / "fixed.png").size
        layers.append({"id": "fixed", "kind": "fixed", "file": "fixed.png", "size": list(size)})
    for name in SKY:
        entry = sky.get(name)
        if not entry or not (out / entry["image"]).exists():
            continue
        shutil.move(out / entry["image"], raw / f"{name}.png")
        layers.append({"id": name, "kind": "parallax", "file": f"{name}.png", "size": [entry["width"], entry["height"]],
                       "rate": float(sum(entry["rate"]) / len(entry["rate"])), "centreCell": entry["centreCell"],
                       "centrePixel": entry["centrePixel"], "pxPerCell": float(sum(entry["pxPerMapCell"]) / len(entry["pxPerMapCell"]))})
    for image in images:
        geo = read_json(out / f"{image}.geo.json")
        area = geo.get("area")
        layer_id = "map" if len(images) == 1 or not area else f"map-{area.lower()}"
        shutil.move(out / f"{image}.png", raw / f"{layer_id}.png")
        maps.append({"id": layer_id, "kind": "map", "file": f"{layer_id}.png", "size": [geo["width"], geo["height"]], "rate": 1.0,
                     "originCell": [geo["originCell"]["x"], geo["originCell"]["y"]], "pxPerCell": geo["pxPerCell"]})
        if (out / f"{image}-team.png").exists():  # its team-colour mask (stitch, team_colour.py)
            shutil.move(out / f"{image}-team.png", raw / f"{layer_id}-team.png")
            maps[-1]["teamFile"] = f"{layer_id}-team.png"
        if len(images) > 1 and area:
            arenas.append({"id": area.lower(), "label": area, "layer": layer_id})
    for suffix in COMPOSITES:
        made = out / f"{out_id}-composite{suffix}.png"
        if made.exists():
            shutil.move(made, raw / f"composite{suffix}.png")
    return layers + maps, arenas or None


def remove_leftovers(out: Path, out_id: str) -> None:
    """The rest of the stitch's files (geo files, previews, an earlier render's tiles and viewer),
    once the pack has taken what it keeps."""
    for leftover in out.glob(f"{out_id}*"):
        shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink()
    (out / "vips-properties.xml").unlink(missing_ok=True)


@dataclass
class Packing:
    """A pack being written, for the pack_data hooks (map/specifics.py): the stitch's output
    folder, the pack's raw/ and pack/ folders, the manifest, the map layer's id and its overview's
    factor (finest level over overview level)."""

    out: Path
    raw: Path
    pack: Path
    manifest: dict
    layer_id: str
    small_factor: int


def write(out: Path, manifest: dict, images: list[str], specifics: Specifics) -> Path:
    """The pack and the raw layers for a stitched map in its folder `out` (its stitch's files, named
    by the render's id, and `images`, the ids of its map images), with what its category and the map
    itself add (`specifics`). Returns the pack's folder."""
    variant = variant_folder(out, manifest.get("structures", "keep"))
    pack, raw = variant / "pack", variant / "raw"
    for folder in (pack, raw):
        if folder.exists():
            shutil.rmtree(folder)
    with stage("pack"):
        layers, arenas = collect_raw(out, raw, manifest["id"], images)
        if arenas:
            areas = {a["name"].lower(): a["bounds"] for a in manifest.get("areas") or []}
            for arena in arenas:
                bounds = areas.get(arena["id"])
                if bounds:
                    arena["boundsCells"] = bounds
                    arena["middleCell"] = [(bounds["left"] + bounds["right"]) / 2, (bounds["bottom"] + bounds["top"]) / 2]
        write_json(raw / "layers.json", {"format": FORMAT, "arenas": arenas, "layers": layers}, indent=2)
        pack.mkdir(parents=True, exist_ok=True)
        # Each layer's overview level (PACK.md, Overviews): a map layer's within OVERVIEW_MAP, a sky
        # layer's the level the opening view draws it at (the first map layer's overview a pixel to
        # a window pixel).
        first_map = next(l for l in layers if l["kind"] == "map")
        opening_zoom = 2.0 ** (map_overview_level(*first_map["size"]) - (levels_for(*first_map["size"]) - 1))
        packed = []
        for layer in layers:
            entry = dict(layer)
            if layer["kind"] == "fixed":
                pyvips.Image.new_from_file(str(raw / layer["file"])).webpsave(str(pack / "fixed.webp"), Q=QUALITY, keep="none")
                entry["file"] = "fixed.webp"
            else:
                level = (map_overview_level(*layer["size"]) if layer["kind"] == "map"
                         else sky_overview_level(*layer["size"], layer["rate"], opening_zoom))
                meta = write_pyramid(raw / layer["file"], pack / f"{layer['id']}.pmtiles", layer["id"], level, tile_webp(layer["id"]))
                if meta is None:
                    warn(f"the {layer['id']} layer is empty; left out of the pack")
                    continue
                entry.update(meta, file=f"{layer['id']}.pmtiles")
                team = entry.pop("teamFile", None)
                if team:
                    # Its team-colour mask (PACK.md, Team colour), tiled and levelled as the layer,
                    # lossless: the amounts are exact.
                    found = write_pyramid(raw / team, pack / f"{layer['id']}-team.pmtiles", f"{layer['id']}-team", level, {"lossless": True}, mask=True)
                    if found:
                        entry["teamMask"] = {"file": f"{layer['id']}-team.pmtiles", "overview": found["overview"]}
            packed.append(entry)
        # The elements' small atlas is at the map's overview scale (write_pyramid), so the two
        # levels of detail a viewer shows zoomed out agree.
        map_packed = next((l for l in packed if l["id"] == first_map["id"]), None)
        overview = (map_packed or {}).get("overview")
        small_factor = 2 ** (map_packed["levels"] - 1 - overview["level"]) if map_packed and overview else 1
        data = {}
        for hook in specifics.pack_data:
            data.update(hook(Packing(out, raw, pack, manifest, first_map["id"], small_factor)))
        remove_leftovers(out, manifest["id"])
        thumbnail = pyvips.Image.thumbnail(str(raw / first_map["file"]), THUMBNAIL_WIDTH)
        thumbnail.flatten(background=BACKDROP).webpsave(str(pack / "thumbnail.webp"), Q=85, keep="none")
        found_images = {"thumbnail": {"file": "thumbnail.webp", "size": [thumbnail.width, thumbnail.height]}}

        storage, build, category = None, None, None
        try:
            storage = game_data.game_storage()
            build = storage.build()
            category = (game_data.map_index(storage).get(manifest["map"]) or {}).get("category")
        except Exception as e:  # noqa: BLE001 - the pack is written without what the game data would add
            warn(f"the game's data couldn't be read ({e}); the pack has no loading screen, category or build")
        copy = None
        try:
            stormmap = Path(manifest["stormmap"]) if manifest.get("stormmap") else None
            if (stormmap is None or not stormmap.exists()) and storage is not None:
                # Stitched on another computer than the prepared map's (it stays where it was
                # prepared): the map's own file from the game's data, by its name.
                try:
                    copy = raw / "map.stormmap"
                    copy.write_bytes(game_data.map_file(storage, manifest["map"])[1])
                    stormmap = copy
                except Exception as e:  # noqa: BLE001 - the pack is written without the map's pictures
                    warn(f"the map's own file isn't here and couldn't be read from the game ({e}); the pack has no minimap")
            # The game's pictures of the map are kept in raw/; the pack has what a viewer reads.
            pictures = map_images(stormmap, storage, raw / "images")
        finally:
            if storage is not None:
                storage.close()
            if copy is not None:
                copy.unlink(missing_ok=True)
        if "customMinimap" in pictures and manifest.get("mapSize") and manifest.get("cameraBounds"):
            if entry := minimap_svg_image(raw, pack, pictures["customMinimap"], manifest["mapSize"], manifest["cameraBounds"], specifics.minimap_correction):
                found_images["customMinimapSvg"] = entry
        found_images.update(loading_screen_images(raw, pack, pictures))

        screen = manifest.get("screen") or {}
        description = {
            "format": FORMAT,
            "tool": f"heroes-capture {_version()}",
            "gameBuild": build,
            "map": {
                "id": game_data.slug(manifest["map"]), "name": manifest["map"], "category": category, "validated": specifics.validated,
                "structures": manifest.get("structures", "keep"),
                "sizeCells": [manifest["mapSize"]["width"], manifest["mapSize"]["height"]] if manifest.get("mapSize") else None,
                "cameraBounds": manifest.get("cameraBounds"),
            },
            "capture": {"pxPerCell": manifest.get("pxPerCell"), "screen": [screen.get("w"), screen.get("h")], "fov": manifest.get("fov")},
            "arenas": arenas,
            "layers": packed,
            "images": found_images,
            "data": data,
            # The colours the pictures' team colour was shot in, and a mask byte's worth (PACK.md, Team colour).
            "teamColours": {"order": list(TEAM_COLOURS["order"]), "chaos": list(TEAM_COLOURS["chaos"]), "maskScale": MASK_SCALE},
        }
        viewer.write(raw)
        description["files"] = {
            str(f.relative_to(pack)).replace("\\", "/"): {"bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
            for f in sorted(pack.rglob("*")) if f.is_file()
        }
        write_json(pack / "pack.json", description, indent=None)
    total = sum(e["bytes"] for e in description["files"].values())
    log(f"pack -> {pack} ({len(packed)} layers, {len(found_images)} pictures, {total / 1e6:.0f} MB)")
    return pack
