"""The map pack (PACK.md at the repository root): what a map viewer loads, written from the
stitch's images at the end of every stitch.

    <output-dir>/<map id>/pack/   pack.json, a tile pyramid per layer (<layer>.pmtiles), the fixed
                                  skybox (fixed.webp), a thumbnail, the map's own pictures from
                                  the game (images/), and the reference viewer (index.html)
    <output-dir>/<map id>/raw/    the layers and composites as full-resolution PNGs, layers.json

A render with the structures hidden goes to <map id>/terrain/ the same way. The stitch's own
files (named by the render's id) are moved into raw/ or removed.
"""

import hashlib
import io
import json
import math
import re
import shutil
from pathlib import Path

import pyvips
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from . import minimap_svg, ui, viewer
from .runlog import log, stage, warn
from .stormlib import Archive
from .workers import ordered_map

FORMAT = 3
TILE = 512  # tile size in pixels
QUALITY = 90  # the tiles' WebP quality (their alpha is lossless)
THUMBNAIL_WIDTH = 512
OVERVIEW_MAX = 2048  # a tiled layer's overview: its finest pyramid level no longer than this on a side
ATLAS_WIDTH = 4096  # the elements' atlases are at most this wide
BACKDROP = [48, 48, 48]  # what the thumbnail's transparency is shown over
SKY = ("background", "haze")  # the parallax layers, back to front
COMPOSITES = ("", "-on-black", "-with-fixed")  # the stitch's composites: <id>-composite<suffix>.png
VALIDATED = Path(__file__).with_name("validated-maps.json")

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


def _rgba(image: pyvips.Image) -> pyvips.Image:
    if image.bands == 3:
        image = image.bandjoin(255)
    return image.cast("uchar")


def write_pyramid(png: Path, dest: Path, name: str) -> dict | None:
    """A PNG as a PMTiles tile pyramid (PACK.md, Tile pyramids): the image padded with transparency
    to whole blocks of the coarsest level's pixel, each level a box-filtered downscale of it (on
    premultiplied colour, so transparent edges don't darken), cut into tiles, the empty ones left
    out, the rest WebP. The layer's size, level count and tile size; None if it is empty."""
    image = _rgba(pyvips.Image.new_from_file(str(png)))
    width, height = image.width, image.height
    levels = levels_for(width, height)
    block = 2 ** (levels - 1)
    padded = image.embed(0, 0, math.ceil(width / block) * block, math.ceil(height / block) * block,
                         extend="background", background=[0, 0, 0, 0])
    tiles = []
    overview = None
    for level in range(levels):
        factor = 2 ** (levels - 1 - level)
        scaled = padded if factor == 1 else padded.premultiply().shrink(factor, factor).unpremultiply().cast("uchar")
        scaled = scaled.copy_memory()
        cols, rows = math.ceil(scaled.width / TILE), math.ceil(scaled.height / TILE)
        # The overview (PACK.md, Overviews): the finest level that fits OVERVIEW_MAX, whole, as one
        # picture, so a viewer has the level in one request; the same pixels as the level's tiles.
        level_w, level_h = math.ceil(width / factor), math.ceil(height / factor)
        if max(level_w, level_h) <= OVERVIEW_MAX:
            overview_path = dest.with_name(f"{dest.stem}-overview.webp")
            scaled.crop(0, 0, level_w, level_h).webpsave(str(overview_path), Q=QUALITY, alpha_q=100)
            overview = {"file": overview_path.name, "level": level, "size": [level_w, level_h]}

        def encode(xy, scaled=scaled, level=level):
            x, y = xy
            tile = scaled.crop(x * TILE, y * TILE, min(TILE, scaled.width - x * TILE), min(TILE, scaled.height - y * TILE))
            if tile.extract_band(3).max() == 0:
                return None  # empty: not stored (a missing tile is transparent)
            if tile.width < TILE or tile.height < TILE:
                tile = tile.embed(0, 0, TILE, TILE, extend="background", background=[0, 0, 0, 0])
            return zxy_to_tileid(level, x, y), tile.webpsave_buffer(Q=QUALITY, alpha_q=100)

        with ui.bar(cols * rows, f"{name}, level {level + 1} of {levels}") as advance:
            for result in ordered_map(encode, [(x, y) for y in range(rows) for x in range(cols)]):
                if result:
                    tiles.append(result)
                advance()
        del scaled
    if not tiles:
        return None
    tiles.sort(key=lambda t: t[0])
    meta = {"size": [width, height], "levels": levels, "tileSize": TILE, "overview": overview}
    with open(dest, "wb") as f:
        writer = Writer(f)
        for tile_id, data in tiles:
            writer.write_tile(tile_id, data)
        writer.finalize({"tile_type": TileType.WEBP, "tile_compression": Compression.NONE, "center_zoom": 0},
                        {"name": name, "format": "webp", "size": meta["size"], "levels": levels, "tileSize": TILE})
    return meta


def pack_atlas(pictures: list[tuple[str, pyvips.Image]], max_width: int = ATLAS_WIDTH) -> tuple[pyvips.Image, dict[str, tuple[int, int]]]:
    """Pictures (RGBA) packed into one atlas, in shelves: the tallest first, each shelf filled left
    to right up to `max_width`, the next shelf below. Returns the atlas and where each picture's
    top-left corner is in it, by key."""
    rows: list[list[tuple[str, pyvips.Image]]] = []
    for key, picture in sorted(pictures, key=lambda kp: (-kp[1].height, kp[0])):
        if rows and sum(p.width for _, p in rows[-1]) + picture.width <= max_width:
            rows[-1].append((key, picture))
        else:
            rows.append([(key, picture)])
    width = max((sum(p.width for _, p in row) for row in rows), default=1)
    height = max(1, sum(row[0][1].height for row in rows))
    atlas = pyvips.Image.black(width, height, bands=4)
    at: dict[str, tuple[int, int]] = {}
    y = 0
    for row in rows:
        x = 0
        for key, picture in row:
            atlas = atlas.insert(picture, x, y)
            at[key] = (x, y)
            x += picture.width
        y += row[0][1].height
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
    map-select picture DocumentInfo names, and the loading screen and its icons MapInfo names
    (from the game's textures, through `storage`). Each {file, size, source}; the icons a list.
    Any the map hasn't (or that can't be read) is left out."""
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

    def texture(path: str) -> bytes | None:
        if storage is None:
            return None
        hits = storage.find("*" + Path(path.replace("\\", "/")).name.lower())
        return storage.read(hits[0]) if hits else None

    try:
        archive = Archive(stormmap)
    except OSError as e:
        warn(f"the map's pictures couldn't be read ({e}); the pack has none")
        return found
    with archive:
        info = archive.read("MapInfo") if archive.has("MapInfo") else b""
        named = {bool(m.group(1)): m.group(0).decode() for m in re.finditer(rb"(?i)customminimap(_hover)?\.(?:dds|tga)", info)}
        for key, inside, name in ARCHIVE_IMAGES:
            if key.startswith("customMinimap"):
                inside = named.get(key.endswith("Hover"), inside)
            if archive.has(inside) and (entry := keep(key, name, archive.read(inside), inside)):
                found[key] = entry
        document = (archive.read_text("DocumentInfo") or "") if archive.has("DocumentInfo") else ""
        pictures = re.findall(r"<Value>([^<]+\.(?:png|dds|tga))</Value>", document)
        select = next((p for p in pictures if "mapselect" in p.lower()), pictures[0] if pictures else None)
        if select and archive.has(select) and (entry := keep("mapSelect", "map-select.png", archive.read(select), select)):
            found["mapSelect"] = entry
    screen = re.search(rb"Assets[\\/]Textures[\\/]([\w.-]+\.dds)", info)
    if screen:
        name = screen.group(1).decode()
        if entry := keep("loadingScreen", "loading-screen.png", texture(name), name):
            found["loadingScreen"] = entry
    layout = re.search(rb"UI/Layout/LoadingScreens/([\w.-]+\.StormLayout)", info)
    if layout and storage is not None:
        hits = storage.find("*" + layout.group(1).decode().lower())
        text = (storage.read(hits[0]) or b"").decode("utf-8", errors="replace") if hits else ""
        icons = list(dict.fromkeys(v for v in re.findall(r'Texture val="([^"]+\.dds)"', text, re.I)))
        entries = [e for n, icon in enumerate(icons, 1) if (e := keep("loadingScreenIcons", f"loading-screen-icon-{n}.png", texture(icon), icon))]
        if entries:
            found["loadingScreenIcons"] = entries
    return found


MINIMAP_BELOW_CAMERA = 2.25  # cells the custom minimap's middle lies below the camera bounds' (measured)
MINIMAP_PLACEMENT = Path(__file__).with_name("minimap-placement.json")  # per-map corrections


def custom_minimap_bounds(picture: Image.Image, map_size: dict, camera: dict, map_name: str | None = None) -> dict:
    """The map cells the custom minimap covers (PACK.md, The custom minimap as SVG). It is drawn at
    the map's scale, a whole number of pixels per cell (the picture's width over the map's), and
    centred on the camera bounds' middle, MINIMAP_BELOW_CAMERA lower (measured over the maps with
    one, against their renders); then any correction minimap-placement.json has for the map."""
    k = picture.width / map_size["width"]
    w, h = picture.width / k, picture.height / k
    left = (camera["left"] + camera["right"]) / 2 - w / 2
    top = (camera["bottom"] + camera["top"]) / 2 - MINIMAP_BELOW_CAMERA + h / 2
    bounds = {"left": left, "bottom": top - h, "right": left + w, "top": top}
    correction = json.loads(MINIMAP_PLACEMENT.read_text(encoding="utf-8"))["maps"].get(map_name or "", {})
    return {edge: round(float(value + correction.get(edge, 0)), 3) for edge, value in bounds.items()}


def minimap_svg_image(pack: Path, custom: dict, map_size: dict, camera: dict, map_name: str | None = None) -> dict | None:
    """The custom minimap redrawn as an SVG (minimap_svg) next to its PNG: its entry (PACK.md,
    Images), with the map cells it covers; None, with a warning, if it can't be redrawn."""
    picture = Image.open(pack / custom["file"])
    try:
        svg = minimap_svg.to_svg(picture)
    except Exception as e:  # noqa: BLE001 - the pack is written without it
        warn(f"the custom minimap couldn't be redrawn as an SVG ({e}); the pack has only its PNG")
        return None
    (pack / "images" / "custom-minimap.svg").write_text(svg, encoding="utf-8")
    return {"file": "images/custom-minimap.svg", "size": custom["size"], "source": custom["source"],
            "boundsCells": custom_minimap_bounds(picture, map_size, camera, map_name)}


def _version() -> str:
    try:
        from ._version import VERSION
        return VERSION
    except ImportError:
        return "0.0.0-dev"


def _slug(name: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))


def collect_raw(out: Path, raw: Path, out_id: str, images: list[str]) -> tuple[list[dict], list[dict] | None]:
    """The stitch's files moved into raw/ under plain names, the ones the pack doesn't keep
    removed. The layers (back to front, as pack.json lists them, `file` naming the raw PNG) and
    the arenas (None on a map of one)."""
    raw.mkdir(parents=True, exist_ok=True)
    layers, arenas, maps = [], [], []
    old_layers = out / f"{out_id}-layers.json"
    sky = json.loads(old_layers.read_text()) if old_layers.exists() else {}
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
        geo = json.loads((out / f"{image}.geo.json").read_text())
        area = geo.get("area")
        layer_id = "map" if len(images) == 1 or not area else f"map-{area.lower()}"
        shutil.move(out / f"{image}.png", raw / f"{layer_id}.png")
        maps.append({"id": layer_id, "kind": "map", "file": f"{layer_id}.png", "size": [geo["width"], geo["height"]], "rate": 1.0,
                     "originCell": [geo["originCell"]["x"], geo["originCell"]["y"]], "pxPerCell": geo["pxPerCell"]})
        if len(images) > 1 and area:
            arenas.append({"id": area.lower(), "label": area, "layer": layer_id})
    for suffix in COMPOSITES:
        made = out / f"{out_id}-composite{suffix}.png"
        if made.exists():
            shutil.move(made, raw / f"composite{suffix}.png")
    # The rest of the stitch's files (geo files, previews, an earlier render's tiles and viewer),
    # but its elements' cut-outs and their index: collect_elements takes those.
    for leftover in out.glob(f"{out_id}*"):
        if leftover.name.startswith(f"{out_id}-elements"):
            continue
        shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink()
    (out / "vips-properties.xml").unlink(missing_ok=True)
    return layers + maps, arenas or None


def collect_elements(out: Path, raw: Path, pack: Path, manifest: dict, layer_id: str, small_factor: int) -> dict | None:
    """The elements render's cut-outs (stitch.write_elements: <id>-elements/ and its index) moved
    into raw/elements/, packed into the pack's atlases (PACK.md, Elements; lossless WebP, their
    soft edges kept exact): `standing` (the standing structures and the camps), `rubble`, `masks`
    (where a standing neighbour is in front), and `small`, every state at 1/`small_factor` (the map
    layer's overview scale) for the zoomed-out view. The pack's data.elements: every structure and
    camp the map has, each with its states' places in the atlases and rectangles in the map layer's
    pixels. None when the render has no elements."""
    index_path = out / f"{manifest['id']}-elements.json"
    if not index_path.exists():
        return None
    index = json.loads(index_path.read_text())
    source = out / f"{manifest['id']}-elements"
    (raw / "elements").mkdir(parents=True, exist_ok=True)
    (pack / "elements").mkdir(parents=True, exist_ok=True)
    pictures: dict[str, list[tuple[str, pyvips.Image]]] = {"standing": [], "rubble": [], "masks": [], "small": []}
    states: dict[tuple[str, int], dict] = {}

    def small(picture: pyvips.Image) -> pyvips.Image:
        """A picture at 1/small_factor: box-filtered on premultiplied colour (as the pyramid's levels
        are), ceil(width / factor) by ceil(height / factor)."""
        if small_factor == 1:
            return picture
        w, h = math.ceil(picture.width / small_factor) * small_factor, math.ceil(picture.height / small_factor) * small_factor
        padded = picture.embed(0, 0, w, h, extend="background", background=[0, 0, 0, 0])
        return padded.premultiply().shrink(small_factor, small_factor).unpremultiply().cast("uchar")

    for cut in index["cutouts"]:
        if cut["file"] is None:  # the element leaves nothing in this state (a fallen moonwell)
            states.setdefault((cut["kind"], cut["element"]), {})[cut["state"]] = None
            continue
        shutil.move(source / cut["file"], raw / "elements" / cut["file"])
        picture = _rgba(pyvips.Image.new_from_file(str(raw / "elements" / cut["file"])))
        atlas = "rubble" if cut["state"] == "rubble" else "standing"
        pictures[atlas].append((cut["key"], picture))
        pictures["small"].append((cut["key"], small(picture)))
        state = {"rect": cut["rect"], "atlas": atlas, "key": cut["key"]}
        # Where a standing neighbour is in front of it: a mask (white: hidden) over the same rect.
        hidden = []
        for mask in cut.get("hiddenBy") or []:
            shutil.move(source / mask["file"], raw / "elements" / mask["file"])
            key = mask["file"].removesuffix(".png")
            pictures["masks"].append((key, _rgba(pyvips.Image.new_from_file(str(raw / "elements" / mask["file"])))))
            hidden.append({"id": mask["id"], "key": key})
        if hidden:
            state["hiddenBy"] = hidden
        states.setdefault((cut["kind"], cut["element"]), {})[cut["state"]] = state
    shutil.rmtree(source, ignore_errors=True)
    index_path.unlink()
    # The atlases written, and each state's places in them put in for its keys.
    atlases, places = {}, {}
    for name, found in pictures.items():
        if not found:
            continue
        atlas, at = pack_atlas(found)
        atlas.webpsave(str(pack / "elements" / f"{name}.webp"), lossless=True)
        atlases[name] = {"file": f"elements/{name}.webp", "size": [atlas.width, atlas.height], "scale": 1 / small_factor if name == "small" else 1}
        places[name] = at
    for state in (s for by_state in states.values() for s in by_state.values() if s):
        key = state.pop("key")
        state["at"] = list(places[state["atlas"]][key])
        state["small"] = list(places["small"][key])
        for mask in state.get("hiddenBy", []):
            mask["at"] = list(places["masks"][mask.pop("key")])
    found = manifest.get("elements") or {}
    structures = [{"id": u["id"], "type": u["type"], "cell": [u["x"], u["y"]], "owner": u["owner"], "town": u["town"], "core": u["core"],
                   "states": states.get(("structure", u["id"]), {})} for u in found.get("structures", [])]
    camps = [{"camp": c["camp"], "type": c["type"], "cell": [c["x"], c["y"]], "states": states.get(("camp", c["camp"]), {})}
             for c in found.get("camps", [])]
    return {"layer": layer_id, "atlases": atlases, "structures": structures, "towns": found.get("towns", []), "camps": camps}


def write(out: Path, manifest: dict, images: list[str]) -> Path:
    """The pack and the raw layers for a stitched map in its folder `out` (its stitch's files, named
    by the render's id, and `images`, the ids of its map images). Returns the pack's folder."""
    from . import game_data

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
        (raw / "layers.json").write_text(json.dumps({"format": FORMAT, "arenas": arenas, "layers": layers}, indent=2))
        pack.mkdir(parents=True, exist_ok=True)
        packed = []
        for layer in layers:
            entry = dict(layer)
            if layer["kind"] == "fixed":
                pyvips.Image.new_from_file(str(raw / layer["file"])).webpsave(str(pack / "fixed.webp"), Q=QUALITY)
                entry["file"] = "fixed.webp"
            else:
                meta = write_pyramid(raw / layer["file"], pack / f"{layer['id']}.pmtiles", layer["id"])
                if meta is None:
                    warn(f"the {layer['id']} layer is empty; left out of the pack")
                    continue
                entry.update(meta, file=f"{layer['id']}.pmtiles")
            packed.append(entry)
        first_map = next(l for l in layers if l["kind"] == "map")
        # The elements' small atlas is at the map's overview scale (write_pyramid), so the two
        # levels of detail a viewer shows zoomed out agree.
        map_packed = next((l for l in packed if l["id"] == first_map["id"]), None)
        overview = (map_packed or {}).get("overview")
        small_factor = 2 ** (map_packed["levels"] - 1 - overview["level"]) if map_packed and overview else 1
        element_data = collect_elements(out, raw, pack, manifest, first_map["id"], small_factor)
        thumbnail = pyvips.Image.thumbnail(str(raw / first_map["file"]), THUMBNAIL_WIDTH)
        thumbnail.flatten(background=BACKDROP).webpsave(str(pack / "thumbnail.webp"), Q=85)
        found_images = {"thumbnail": {"file": "thumbnail.webp", "size": [thumbnail.width, thumbnail.height]}}

        storage, build, category = None, None, None
        try:
            storage = game_data.open_storage(game_data.find_install())
            build = storage.build()
            category = (game_data.map_index(storage).get(manifest["map"]) or {}).get("category")
        except Exception as e:  # noqa: BLE001 - the pack is written without what the game data would add
            warn(f"the game's data couldn't be read ({e}); the pack has no loading screen, category or build")
        try:
            stormmap = Path(manifest["stormmap"]) if manifest.get("stormmap") else None
            found_images.update(map_images(stormmap, storage, pack / "images"))
        finally:
            if storage is not None:
                storage.close()
        if "customMinimap" in found_images and manifest.get("mapSize") and manifest.get("cameraBounds"):
            if entry := minimap_svg_image(pack, found_images["customMinimap"], manifest["mapSize"], manifest["cameraBounds"], manifest["map"]):
                found_images["customMinimapSvg"] = entry

        validated = manifest["map"] in json.loads(VALIDATED.read_text(encoding="utf-8"))["maps"]
        screen = manifest.get("screen") or {}
        description = {
            "format": FORMAT,
            "tool": f"heroes-capture {_version()}",
            "gameBuild": build,
            "map": {
                "id": _slug(manifest["map"]), "name": manifest["map"], "category": category, "validated": validated,
                "structures": manifest.get("structures", "keep"),
                "sizeCells": [manifest["mapSize"]["width"], manifest["mapSize"]["height"]] if manifest.get("mapSize") else None,
                "cameraBounds": manifest.get("cameraBounds"),
            },
            "capture": {"pxPerCell": manifest.get("pxPerCell"), "screen": [screen.get("w"), screen.get("h")], "fov": manifest.get("fov")},
            "arenas": arenas,
            "layers": packed,
            "images": found_images,
            "data": {"elements": element_data} if element_data else {},
        }
        viewer.write(pack)
        description["files"] = {
            str(f.relative_to(pack)).replace("\\", "/"): {"bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
            for f in sorted(pack.rglob("*")) if f.is_file()
        }
        (pack / "pack.json").write_text(json.dumps(description, indent=2))
    total = sum(e["bytes"] for e in description["files"].values())
    log(f"pack -> {pack} ({len(packed)} layers, {len(found_images)} pictures, {total / 1e6:.0f} MB)")
    return pack
