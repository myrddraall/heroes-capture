"""The map pack (pack.py, PACK.md): the tile pyramids, the pack written from a stitch's files, and
the local server the reference viewer is opened through."""

import io
import json
import urllib.request

import numpy as np
import pyvips
from PIL import Image
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import TileType

from heroes_capture.capture import matting
from heroes_capture.capture import pack
from heroes_capture.categories.battleground import element_pack, element_stitch
from heroes_capture.map.specifics import Specifics


def test_levels_reach_down_to_one_tile():
    assert pack.levels_for(512, 300) == 1
    assert pack.levels_for(513, 10) == 2
    assert pack.levels_for(1024, 1024) == 2
    assert pack.levels_for(12866, 9260) == 6  # Battlefield of Eternity's map: 12866 / 2^5 = 402 px at level 0


def image_file(path, width, height):
    """An RGBA PNG: a smooth gradient on the left, fully transparent on the right."""
    yy, xx = np.mgrid[0:height, 0:width]
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[..., 0] = (xx * 255 // width).astype(np.uint8)
    rgba[..., 1] = (yy * 255 // height).astype(np.uint8)
    rgba[..., 2] = 128
    rgba[..., 3] = np.where(xx < width // 2, 255, 0)
    Image.fromarray(rgba).save(path)
    return rgba


def read_tile(reader, level, x, y):
    data = reader.get(level, x, y)
    return None if data is None else np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


def test_a_pyramid_reads_back_level_by_level(tmp_path):
    """The overview is its level whole (level 0 here: the image a quarter size, padded with
    transparency to whole pixels of it); the archive holds the levels finer than it, and no other."""
    source = image_file(tmp_path / "layer.png", 1300, 700)
    meta = pack.write_pyramid(tmp_path / "layer.png", tmp_path / "layer.pmtiles", "layer", 0, pack.TILE_WEBP)
    assert meta == {"size": [1300, 700], "levels": 3, "tileSize": 512,
                    "overview": {"file": "layer-overview.webp", "level": 0, "size": [325, 175]}}
    overview = np.asarray(Image.open(tmp_path / "layer-overview.webp").convert("RGBA"))
    assert overview.shape == (175, 325, 4)
    assert overview[50, 50, 3] == 255 and overview[50, 300, 3] == 0
    assert np.abs(overview[25, 25, :3].astype(int) - source[100:104, 100:104, :3].mean(axis=(0, 1))).max() < 6
    assert not Image.open(tmp_path / "layer-overview.webp").getexif()  # no metadata
    with open(tmp_path / "layer.pmtiles", "rb") as f:
        reader = Reader(MmapSource(f))
        header = reader.header()
        assert header["tile_type"] == TileType.WEBP and (header["min_zoom"], header["max_zoom"]) == (1, 2)
        assert reader.metadata()["size"] == [1300, 700]
        # The full image at level 2: its left tile like the source (lossy), the right one empty.
        full = read_tile(reader, 2, 0, 0)
        assert full.shape == (512, 512, 4)
        assert np.abs(full[:400, :500, :3].astype(int) - source[:400, :500, :3]).mean() < 4
        assert (full[:400, :500, 3] == 255).all()
        assert read_tile(reader, 2, 2, 0) is None  # x 1024-1300: transparent, not stored
        # Level 1 (half size): pixel (i, j) covers full pixels (2i..2i+1, 2j..2j+1).
        half = read_tile(reader, 1, 0, 0)
        assert np.abs(half[100, 100, :3].astype(int) - source[200:202, 200:202, :3].mean(axis=(0, 1))).max() < 6
        assert read_tile(reader, 0, 0, 0) is None  # the overview's level: not in the archive


def test_each_layer_s_overview_level():
    """A map layer's overview: its finest level no longer than OVERVIEW_MAP (2048) on a side, never
    the finest level (but on a layer of one level). A sky layer's: the level the opening view draws
    it at, the zoom where the map's overview is a pixel to a window pixel (further back, the sky is
    drawn larger), within OVERVIEW_MAX (4096): Battlefield of Eternity's map at level 2, its haze at 3."""
    assert pack.map_overview_level(2600, 1000) == 2  # levels 325, 650, 1300, 2600 wide
    assert pack.map_overview_level(12865, 9260) == 2  # 1609 x 1158
    assert pack.map_overview_level(1300, 700) == 1  # level 2 would fit, but is the full image
    assert pack.map_overview_level(400, 300) == 0  # one level: the full image
    opening = 2.0 ** (2 - 5)  # the map's level 2 of 6 a pixel to a window pixel
    assert pack.sky_overview_level(14360, 7312, 0.45, opening) == 3  # 3590 x 1828
    assert pack.sky_overview_level(14360, 7312, 1.0, opening) == 2  # at the map's rate: as the map
    assert pack.sky_overview_level(40000, 20000, 0.45, opening) == 3  # of 8 levels: 5 wanted, 10000 wide


def test_each_layer_s_encoding():
    """The map's and the background art's tiles: colour at quality 85, transparency exact; the
    haze's transparency lossy too (PACK.md, Tile pyramids)."""
    assert pack.tile_webp("map") == pack.tile_webp("map-m1") == pack.tile_webp("background") == {"Q": 85, "alpha_q": 100}
    assert pack.tile_webp("haze") == {"Q": 90, "alpha_q": 50}


def test_a_big_layer_s_archive_starts_past_its_overview(tmp_path):
    """The overview's pixels are those its level's tiles would have had: the finer level's, halved."""
    source = image_file(tmp_path / "big.png", 2600, 1000)
    meta = pack.write_pyramid(tmp_path / "big.png", tmp_path / "big.pmtiles", "big", 2, pack.TILE_WEBP)
    assert meta["levels"] == 4 and meta["overview"] == {"file": "big-overview.webp", "level": 2, "size": [1300, 500]}
    overview = np.asarray(Image.open(tmp_path / "big-overview.webp").convert("RGBA"))
    assert np.abs(overview[100, 100, :3].astype(int) - source[200:202, 200:202, :3].mean(axis=(0, 1))).max() < 6
    with open(tmp_path / "big.pmtiles", "rb") as f:
        reader = Reader(MmapSource(f))
        assert (reader.header()["min_zoom"], reader.header()["max_zoom"]) == (3, 3)
        assert read_tile(reader, 2, 0, 0) is None


def test_atlases_pack_pictures_in_shelves_without_overlap():
    """pack_atlas: the tallest pictures first, each shelf filled up to the width, the next below;
    every picture whole in the atlas, none over another."""
    pictures = [(f"p{k}", (pyvips.Image.black(w, h, bands=4) + [k + 1, 0, 0, 255]).cast("uchar"))
                for k, (w, h) in enumerate([(30, 10), (50, 20), (40, 20), (20, 5)])]
    atlas, at = pack.pack_atlas(pictures, max_width=100)
    assert at == {"p1": (0, 0), "p2": (50, 0), "p0": (0, 20), "p3": (30, 20)}  # heights 20, 20, 10, 5: two shelves
    assert (atlas.width, atlas.height) == (90, 30)
    pixels = np.asarray(Image.fromarray(atlas.numpy()))
    for key, (x, y) in at.items():
        k = int(key[1:])
        w, h = pictures[k][1].width, pictures[k][1].height
        assert (pixels[y : y + h, x : x + w, 0] == k + 1).all()
    assert int((pixels[..., 3] > 0).sum()) == sum(p.width * p.height for _, p in pictures)


def test_an_empty_layer_has_no_pyramid(tmp_path):
    Image.new("RGBA", (600, 600), (0, 0, 0, 0)).save(tmp_path / "empty.png")
    assert pack.write_pyramid(tmp_path / "empty.png", tmp_path / "empty.pmtiles", "empty", 0, pack.TILE_WEBP) is None


def stitched(out, out_id="test-map-structures"):
    """A stitch's files in a map's folder: the map image and geo file, the sky layers and their
    layers.json, composites, and the extras an older render left (a preview, a tile folder)."""
    out.mkdir(parents=True)
    image_file(out / f"{out_id}.png", 900, 600)
    (out / f"{out_id}.geo.json").write_text(json.dumps({"map": "Test Map", "area": None, "image": f"{out_id}.png", "width": 900, "height": 600,
                                                       "pxPerCell": 48.0, "originCell": {"x": 2.0, "y": 60.0}}))
    for layer in ("background", "haze"):
        image_file(out / f"{out_id}-layer-{layer}.png", 700, 400)
    pyvips.Image.black(300, 200).bandjoin([10, 20]).cast("uchar").write_to_file(str(out / f"{out_id}-layer-fixed.png"))
    sky = {"rate": [0.45, 0.45], "centreCell": [32.0, 32.0], "centrePixel": [350.0, 200.0], "pxPerMapCell": [21.6, 21.6], "width": 700, "height": 400}
    (out / f"{out_id}-layers.json").write_text(json.dumps({
        "map": {"image": f"{out_id}.png"}, "fixed": {"image": f"{out_id}-layer-fixed.png"},
        "background": {"image": f"{out_id}-layer-background.png", **sky}, "haze": {"image": f"{out_id}-layer-haze.png", **sky}}))
    Image.new("RGBA", (900, 600), (1, 2, 3, 255)).save(out / f"{out_id}-composite.png")
    (out / f"{out_id}-preview.jpg").write_bytes(b"old")
    (out / f"{out_id}-tiles").mkdir()
    return {"map": "Test Map", "id": out_id, "structures": "keep", "pxPerCell": 48, "screen": {"w": 3440, "h": 1440}, "fov": 8.0,
            "mapSize": {"width": 64, "height": 64}, "cameraBounds": {"left": 14, "bottom": 14, "right": 50, "top": 50},
            "stormmap": str(out / "missing.stormmap")}


def test_a_pack_from_a_stitch(tmp_path, monkeypatch):
    from heroes_capture.common import game_data

    monkeypatch.setattr(game_data, "open_storage", lambda install: (_ for _ in ()).throw(OSError("no game here")))
    monkeypatch.setattr(game_data, "find_install", lambda: None)
    out = tmp_path / "maps" / "test-map"
    manifest = stitched(out)
    folder = pack.write(out, manifest, ["test-map-structures"], Specifics("battleground"))
    assert folder == out / "pack"
    assert sorted(p.name for p in out.iterdir()) == ["pack", "raw"]  # the stitch's files moved or removed
    assert sorted(p.name for p in (out / "raw").iterdir()) == ["background.png", "composite.png", "fixed.png", "haze.png", "index.html",
                                                               "layers.json", "map.png"]
    text = (folder / "pack.json").read_text()
    assert "\n" not in text and ", " not in text  # compact
    description = json.loads(text)
    assert description["format"] == 6 and description["gameBuild"] is None and description["arenas"] is None
    assert description["map"] == {"id": "test-map", "name": "Test Map", "category": None, "validated": False, "structures": "keep",
                                  "sizeCells": [64, 64], "cameraBounds": {"left": 14, "bottom": 14, "right": 50, "top": 50}}
    assert [(l["id"], l["kind"], l["file"]) for l in description["layers"]] == [
        ("fixed", "fixed", "fixed.webp"), ("background", "parallax", "background.pmtiles"),
        ("haze", "parallax", "haze.pmtiles"), ("map", "map", "map.pmtiles")]
    map_layer = description["layers"][-1]
    assert map_layer["originCell"] == [2.0, 60.0] and map_layer["levels"] == 2 and map_layer["size"] == [900, 600]
    assert description["layers"][1]["pxPerCell"] == 21.6 and description["layers"][1]["rate"] == 0.45
    # Each tiled layer's overview (here the layers are small: the level under the full size).
    assert description["layers"][1]["overview"] == {"file": "background-overview.webp", "level": 0, "size": [350, 200]}
    assert map_layer["overview"] == {"file": "map-overview.webp", "level": 0, "size": [450, 300]}
    assert list(description["images"]) == ["thumbnail"]
    files = description["files"]
    assert set(files) == {"fixed.webp", "background.pmtiles", "background-overview.webp", "haze.pmtiles", "haze-overview.webp",
                          "map.pmtiles", "map-overview.webp", "thumbnail.webp"}
    import hashlib
    assert files["map.pmtiles"]["sha256"] == hashlib.sha256((folder / "map.pmtiles").read_bytes()).hexdigest()
    assert pack.is_written(out, "keep") and not pack.is_written(out, "hide")


def test_the_local_server_answers_byte_ranges(tmp_path):
    from heroes_capture.capture.serve import serve

    (tmp_path / "a.pmtiles").write_bytes(bytes(range(256)) * 4)
    server = serve(tmp_path, page=None)
    try:
        url = f"http://127.0.0.1:{server.server_port}/a.pmtiles"
        ranged = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=10-19"}))
        assert ranged.status == 206 and ranged.headers["Content-Range"] == "bytes 10-19/1024" and ranged.read() == bytes(range(10, 20))
        tail = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=-4"}))
        assert tail.read() == bytes(range(252, 256))
        whole = urllib.request.urlopen(url)
        assert whole.status == 200 and len(whole.read()) == 1024
    finally:
        server.shutdown()


def element_shots(folder, key, u, v):
    """An element's two shots alone over the sky: a solid 2x2 square at (u, v) of a 40x30 screen."""
    from heroes_capture.common.frames import save_frame

    white = np.full((30, 40, 3), 230, np.uint8)
    black = np.zeros((30, 40, 3), np.uint8)
    white[v:v + 2, u:u + 2] = black[v:v + 2, u:u + 2] = 100
    save_frame(folder / key, white)
    save_frame(folder / f"{key}-black", black)


def test_an_element_s_cut_out_lands_on_its_cell_in_the_map_image(tmp_path):
    """element_stitch.write_elements: a tile whose camera stood at cell (10, 10) is placed at (100, 50) on the
    canvas (2 px per cell, so that cell is at (120, 65)), and the map image is the canvas from
    (4, 5). An element at cell (11, 9) lands where the image's geo puts that cell, whether its shot
    was taken from the tile's camera position or half a cell east of it."""
    from heroes_capture.capture import stitch

    base, out = tmp_path / "run", tmp_path / "out"
    (base / "elements").mkdir(parents=True)
    out.mkdir()
    element_shots(base / "elements", "structure-5-standing", 22, 17)  # the camera on the tile's spot
    element_shots(base / "elements", "structure-5-rubble", 21, 17)  # the camera half a cell east
    record = {"kind": "structure", "element": 5, "x": 11, "y": 9, "radius": 3, "tile": 0}
    (base / "elements" / "elements.json").write_text(json.dumps({
        "structure-5-standing": {**record, "state": "standing", "camera": {"x": 10, "y": 10}},
        "structure-5-rubble": {**record, "state": "rubble", "camera": {"x": 10.5, "y": 10}}}))
    (base / "positions.json").write_text(json.dumps({"0": {"x": 10, "y": 10}}))
    scale, ax, ay = 2.0, 100.0, 85.0  # cell (10, 10) at canvas (120, 65)
    (out / "m.geo.json").write_text(json.dumps({"originCell": {"x": -(ax - 4) / scale, "y": (ay - 5) / scale}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert element_stitch.write_elements({"id": "m"}, base, out, layout, scale, ax, ay, "m") == 2
    index = json.loads((out / "m-elements.json").read_text())
    # Cell (11, 9) in the image: ((11 + 48) * 2, (40 - 9) * 2) = (118, 62).
    assert [c["rect"] for c in index["cutouts"]] == [[118, 62, 2, 2], [118, 62, 2, 2]]
    cut = np.asarray(Image.open(out / "m-elements" / "structure-5-standing.png"))
    assert cut.shape == (2, 2, 4) and (cut[..., 3] == 255).all() and (cut[..., :3] == 100).all()


def test_on_a_map_of_several_arenas_each_element_lands_on_its_own_arena_s_image(tmp_path):
    """element_stitch.write_elements, once per arena's image: an element shot from an area's tile is
    placed on that area's image, in its pixels, and the index gathers them all with their image;
    element_pack.collect_elements then names each element's layer where it isn't the first's."""
    from heroes_capture.capture import stitch

    base, out = tmp_path / "run", tmp_path / "out"
    (base / "elements").mkdir(parents=True)
    out.mkdir()
    element_shots(base / "elements", "structure-5-standing", 22, 17)  # on tile 0 (area 0)
    element_shots(base / "elements", "structure-6-standing", 22, 17)  # on tile 1 (area 1), the same screen spot
    (base / "elements" / "elements.json").write_text(json.dumps({
        "structure-5-standing": {"kind": "structure", "element": 5, "state": "standing", "x": 11, "y": 9, "radius": 3, "tile": 0, "camera": {"x": 10, "y": 10}},
        "structure-6-standing": {"kind": "structure", "element": 6, "state": "standing", "x": 11, "y": 29, "radius": 3, "tile": 1, "camera": {"x": 10, "y": 30}}}))
    (base / "positions.json").write_text(json.dumps({"0": {"x": 10, "y": 10}, "1": {"x": 10, "y": 30}}))
    scale, ax, ay = 2.0, 100.0, 85.0
    # Area 0's image from the canvas's (4, 5), area 1's from (4, 0) (its own crop).
    (out / "m-a.geo.json").write_text(json.dumps({"originCell": {"x": -(ax - 4) / scale, "y": (ay - 5) / scale}}))
    (out / "m-b.geo.json").write_text(json.dumps({"originCell": {"x": -(ax - 4) / scale, "y": ay / scale}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0]), 1: np.array([100.0, 10.0])}, {},
                           {0: {"index": 0, "x": 10, "y": 10, "area": 0}, 1: {"index": 1, "x": 10, "y": 30, "area": 1}}, 40, 30, 0)
    assert element_stitch.write_elements({"id": "m"}, base, out, layout, scale, ax, ay, "m-a", 0) == 1
    assert element_stitch.write_elements({"id": "m"}, base, out, layout, scale, ax, ay, "m-b", 1) == 1
    index = json.loads((out / "m-elements.json").read_text())
    assert [(c["key"], c["image"], c["rect"]) for c in index["cutouts"]] == [
        ("structure-5-standing", "m-a", [118, 62, 2, 2]), ("structure-6-standing", "m-b", [118, 27, 2, 2])]
    raw, packed = tmp_path / "raw", tmp_path / "pack"
    manifest = {"id": "m", "elements": {
        "structures": [{"id": 5, "type": "TownCannonTowerL2", "x": 11, "y": 9, "owner": "order", "town": None, "outpost": None, "core": False},
                       {"id": 6, "type": "TownCannonTowerL2", "x": 11, "y": 29, "owner": "chaos", "town": None, "outpost": None, "core": False}],
        "towns": [], "outposts": [], "camps": []}}
    data = element_pack.collect_elements(out, raw, packed, manifest, "map-a", small_factor=2, layers={"m-a": "map-a", "m-b": "map-b"})
    assert data["layer"] == "map-a"
    assert "layer" not in data["structures"][0] and data["structures"][1]["layer"] == "map-b"  # the first arena's is the elements' own


def test_whose_team_colour_a_mask_says_changed():
    """element_stitch.mask_team: the team whose colour changed between the sides (a structure shot
    from the wrong side changes as the other team's); too little change to tell: None."""
    order = np.zeros((30, 30, 2), np.uint8); order[..., 0] = 120
    chaos = np.zeros((30, 30, 2), np.uint8); chaos[..., 1] = 120
    faint = np.zeros((30, 30, 2), np.uint8); faint[:5, :5, 0] = 120  # 25 pixels: too few
    assert element_stitch.mask_team(order) == "order" and element_stitch.mask_team(chaos) == "chaos"
    assert element_stitch.mask_team(faint) is None


def test_the_pack_s_elements(tmp_path):
    """element_pack.collect_elements: the cut-outs packed into the pack's atlases (standing, rubble, masks,
    and small: every state at the overview scale), and data.elements listing every structure and
    camp with its states' places in them."""
    out, raw, packed = tmp_path / "out", tmp_path / "raw", tmp_path / "pack"
    (out / "m-elements").mkdir(parents=True)
    Image.new("RGBA", (3, 2), (100, 0, 0, 255)).save(out / "m-elements" / "structure-5-standing.png")
    Image.new("RGBA", (4, 3), (0, 100, 0, 255)).save(out / "m-elements" / "structure-5-rubble.png")
    Image.new("RGBA", (3, 2), (0, 0, 100, 255)).save(out / "m-elements" / "structure-6-standing.png")
    Image.new("RGBA", (3, 2), (255, 255, 255, 255)).save(out / "m-elements" / "structure-6-standing-under-5.png")
    Image.new("RGB", (3, 2), (100, 0, 0)).save(out / "m-elements" / "structure-5-standing-team.png")  # its team colour
    (out / "m-elements.json").write_text(json.dumps({"image": "m", "cutouts": [
        {"key": "structure-5-standing", "kind": "structure", "element": 5, "state": "standing", "file": "structure-5-standing.png", "rect": [118, 62, 3, 2],
         "teamMask": "structure-5-standing-team.png"},
        {"key": "structure-5-rubble", "kind": "structure", "element": 5, "state": "rubble", "file": "structure-5-rubble.png", "rect": [117, 61, 4, 3]},
        {"key": "structure-6-standing", "kind": "structure", "element": 6, "state": "standing", "file": "structure-6-standing.png", "rect": [120, 62, 3, 2],
         "hiddenBy": [{"id": 5, "file": "structure-6-standing-under-5.png"}]}]}))
    manifest = {"id": "m", "elements": {
        "structures": [{"id": 5, "type": "TownCannonTowerL2", "x": 11, "y": 9, "owner": "order", "town": 1, "outpost": 1, "core": False, "radius": 6},
                       {"id": 6, "type": "TownWallRadial2L3", "x": 12, "y": 9, "owner": "order", "town": 1, "outpost": None, "core": False, "radius": 6}],
        "towns": [{"town": 1, "lane": 1, "owner": "order", "region": 2, "name": "Lane 1 - Order - Town 1"}],
        "outposts": [{"outpost": 1, "town": 1, "lane": 1, "owner": "order", "x": 11.0, "y": 9.0}],
        "camps": [{"camp": 1, "type": "SiegeCamp1", "x": 30, "y": 31, "spawns": [], "spread": 2, "radius": 5}]}}
    data = element_pack.collect_elements(out, raw, packed, manifest, "map", small_factor=2)
    assert data["layer"] == "map" and data["towns"][0]["town"] == 1
    # Each structure's outpost (format 4), and the outposts with their middle as a cell.
    assert [u["outpost"] for u in data["structures"]] == [1, None]
    assert data["outposts"] == [{"outpost": 1, "town": 1, "lane": 1, "owner": "order", "cell": [11.0, 9.0]}]
    # The atlases: the two standing cut-outs side by side, ATLAS_GAP apart; the rubble alone; the
    # one mask; every state at half size, ceil(3/2) x ceil(2/2) and ceil(4/2) x ceil(3/2), the
    # tallest first.
    assert data["atlases"] == {
        "standing": {"file": "elements/standing.webp", "size": [10, 2], "scale": 1},
        "rubble": {"file": "elements/rubble.webp", "size": [4, 3], "scale": 1},
        "masks": {"file": "elements/masks.webp", "size": [3, 2], "scale": 1},
        "small": {"file": "elements/small.webp", "size": [14, 2], "scale": 0.5},
        "team": {"file": "elements/team.webp", "size": [3, 2], "scale": 1},
        "teamSmall": {"file": "elements/teamSmall.webp", "size": [2, 1], "scale": 0.5}}
    assert data["structures"][0]["states"] == {
        "standing": {"rect": [118, 62, 3, 2], "atlas": "standing", "at": [0, 0], "small": [6, 0], "teamMask": {"at": [0, 0], "small": [0, 0]}},
        "rubble": {"rect": [117, 61, 4, 3], "atlas": "rubble", "at": [0, 0], "small": [0, 0]}}
    assert data["structures"][1]["states"] == {
        "standing": {"rect": [120, 62, 3, 2], "atlas": "standing", "at": [7, 0], "small": [12, 0], "hiddenBy": [{"id": 5, "at": [0, 0]}]}}
    assert data["camps"] == [{"camp": 1, "type": "SiegeCamp1", "cell": [30, 31], "states": {}}]
    # The colour lossy (within a few levels), the transparency exact; the masks lossless. A cut-out
    # one pixel tall is all edge: WebP's colour, at half the pixels, mixes in the gap under it.
    near = lambda pixel, colour, within=8: np.abs(pixel.astype(int) - colour).max() <= within and pixel[3] == colour[3]  # noqa: E731
    standing = np.asarray(Image.open(packed / "elements" / "standing.webp").convert("RGBA"))
    assert near(standing[0, 0], (100, 0, 0, 255)) and near(standing[0, 7], (0, 0, 100, 255))
    small = np.asarray(Image.open(packed / "elements" / "small.webp").convert("RGBA"))
    assert small.shape == (2, 14, 4) and near(small[0, 6], (100, 0, 0, 255), within=20)  # the standing cut-out, shrunk
    masks = np.asarray(Image.open(packed / "elements" / "masks.webp").convert("RGBA"))
    assert (masks == 255).all()
    team = np.asarray(Image.open(packed / "elements" / "team.webp").convert("RGB"))
    assert (team[..., 0] == 100).all() and not team[..., 1:].any()  # exact: lossless
    assert not list((packed / "elements").glob("structure-*"))  # no file per cut-out any more
    assert (raw / "elements" / "structure-5-standing.png").exists() and not (out / "m-elements.json").exists()


def test_a_pack_stitched_away_from_the_prepared_map_reads_its_pictures_from_the_game(tmp_path, monkeypatch):
    """The prepared .stormmap stays on the computer that prepared it (a results folder leaves it
    out): stitched elsewhere, the pack reads the map's own pictures from the game's copy of the
    map, by its name, and leaves no copy behind."""
    import types

    from heroes_capture.common import game_data

    storage = types.SimpleNamespace(build=lambda: 98348, close=lambda: None)
    monkeypatch.setattr(game_data, "game_storage", lambda: storage)
    monkeypatch.setattr(game_data, "map_index", lambda s: {})
    monkeypatch.setattr(game_data, "map_file", lambda s, name: (name, b"the map's archive"))
    seen = []

    def map_images(stormmap, storage_, folder):
        seen.append(stormmap.read_bytes() if stormmap and stormmap.exists() else None)
        return {}

    monkeypatch.setattr(pack, "map_images", map_images)
    out = tmp_path / "maps" / "test-map"
    folder = pack.write(out, stitched(out), ["test-map-structures"], Specifics("battleground"))
    assert seen == [b"the map's archive"]
    assert not list((out / "raw").glob("*.stormmap"))
    assert json.loads((folder / "pack.json").read_text())["gameBuild"] == 98348


def test_the_loading_screen_and_its_icons_are_in_the_pack_as_webp(tmp_path, monkeypatch):
    """The game's pictures stay in raw/ as PNG; the loading screen and its icons, which a viewer
    shows, are also in the pack's images/ as WebP: the picture lossy and opaque, the icons exact
    with their transparency (PACK.md, Images)."""
    import types

    from heroes_capture.common import game_data

    monkeypatch.setattr(game_data, "game_storage", lambda: types.SimpleNamespace(build=lambda: 1, close=lambda: None))
    monkeypatch.setattr(game_data, "map_index", lambda s: {})
    monkeypatch.setattr(game_data, "map_file", lambda s, name: (name, b"the map's archive"))

    def map_images(stormmap, storage_, folder):
        folder.mkdir(parents=True)
        Image.new("RGB", (192, 108), (200, 100, 50)).save(folder / "loading-screen.png")
        icon = Image.new("RGBA", (30, 20), (0, 0, 0, 0))
        icon.paste((10, 200, 30, 255), (5, 5, 15, 15))
        icon.save(folder / "loading-screen-icon-1.png")
        return {"loadingScreen": {"file": "images/loading-screen.png", "size": [192, 108], "source": "screen.dds"},
                "loadingScreenIcons": [{"file": "images/loading-screen-icon-1.png", "size": [30, 20], "source": "icon1.dds"}]}

    monkeypatch.setattr(pack, "map_images", map_images)
    out = tmp_path / "maps" / "test-map"
    folder = pack.write(out, stitched(out), ["test-map-structures"], Specifics("battleground"))
    images = json.loads((folder / "pack.json").read_text())["images"]
    # Each also at a half and a quarter of its size, for a viewer to pick from.
    assert images["loadingScreen"] == {"file": "images/loading-screen.webp", "size": [192, 108], "source": "screen.dds", "smaller": [
        {"file": "images/loading-screen-96w.webp", "size": [96, 54]}, {"file": "images/loading-screen-48w.webp", "size": [48, 27]}]}
    assert images["loadingScreenIcons"] == [{"file": "images/loading-screen-icon-1.webp", "size": [30, 20], "source": "icon1.dds", "smaller": [
        {"file": "images/loading-screen-icon-1-15w.webp", "size": [15, 10]}, {"file": "images/loading-screen-icon-1-8w.webp", "size": [8, 5]}]}]
    for entry in [images["loadingScreen"], *images["loadingScreenIcons"]]:
        for version in entry["smaller"]:
            assert Image.open(folder / version["file"]).size == tuple(version["size"])
    screen = Image.open(folder / "images" / "loading-screen.webp")
    assert screen.mode == "RGB" and np.abs(np.asarray(screen)[50, 90].astype(int) - (200, 100, 50)).max() <= 8
    icon = np.asarray(Image.open(folder / "images" / "loading-screen-icon-1.webp").convert("RGBA"))
    assert tuple(icon[10, 10]) == (10, 200, 30, 255) and icon[0, 0, 3] == 0  # exact: lossless
    half = np.asarray(Image.open(folder / "images" / "loading-screen-icon-1-15w.webp").convert("RGBA"))
    # Shrunk with its transparency premultiplied: the square's colour isn't darkened at its edge.
    assert np.abs(half[5, 5, :3].astype(int) - (10, 200, 30)).max() <= 3 and half[5, 5, 3] > 200 and half[0, 0, 3] == 0
    assert (out / "raw" / "images" / "loading-screen.png").exists()


def test_a_map_layer_s_team_colour_mask_is_tiled_with_it(tmp_path, monkeypatch):
    """The stitch's team-colour mask of a map image (<image>-team.png: Order's team's amount in red,
    Chaos's in green) becomes the map layer's teamMask: a lossless pyramid tiled and levelled as the
    layer, its empty tiles left out; and pack.json gives the colours it was shot in (PACK.md, Team
    colour)."""
    from heroes_capture.common import game_data

    monkeypatch.setattr(game_data, "open_storage", lambda install: (_ for _ in ()).throw(OSError("no game here")))
    monkeypatch.setattr(game_data, "find_install", lambda: None)
    out = tmp_path / "maps" / "test-map"
    manifest = stitched(out)
    mask = np.zeros((600, 900, 3), np.uint8)
    mask[100:140, 100:140, 0] = 200  # Order's team, in the first tile only
    mask[110:120, 600:620, 1] = 90  # Chaos's, in the second
    Image.fromarray(mask).save(out / "test-map-structures-team.png")
    folder = pack.write(out, manifest, ["test-map-structures"], Specifics("battleground"))
    description = json.loads((folder / "pack.json").read_text())
    layer = description["layers"][-1]
    assert layer["teamMask"] == {"file": "map-team.pmtiles", "overview": {"file": "map-team-overview.webp", "level": 0, "size": [450, 300]}}
    assert description["teamColours"] == {"order": [101, 159, 255], "chaos": [255, 57, 48], "maskScale": 2.0}
    assert {"map-team.pmtiles", "map-team-overview.webp"} <= set(description["files"])
    with open(folder / "map-team.pmtiles", "rb") as f:
        reader = Reader(MmapSource(f))
        first, second, third = read_tile(reader, 1, 0, 0), read_tile(reader, 1, 1, 0), read_tile(reader, 1, 0, 1)
    assert tuple(first[120, 120, :2]) == (200, 0) and tuple(second[115, 610 - 512, :2]) == (0, 90)  # exact: lossless
    assert third is None  # nothing there: not stored
    assert (out / "raw" / "map-team.png").exists()


def test_the_stitch_s_leftovers_are_cleared_after_the_pack_data_hooks(tmp_path, monkeypatch):
    """The pack clears the stitch's leftover files from the map's folder only once the pack_data
    hooks (map/specifics.py) have taken theirs (the battlegrounds' elements: the first elements
    render lost them), and puts what the hooks give in the pack's data."""
    from heroes_capture.common import game_data

    monkeypatch.setattr(game_data, "open_storage", lambda install: (_ for _ in ()).throw(OSError("no game here")))
    monkeypatch.setattr(game_data, "find_install", lambda: None)
    out = tmp_path / "maps" / "test-map"
    manifest = stitched(out)
    (out / "test-map-structures-elements.json").write_text("{}")
    seen = []
    specifics = Specifics("battleground", pack_data=[lambda packing: seen.append((packing.out / "test-map-structures-elements.json").exists()) or {"x": 1}])
    folder = pack.write(out, manifest, ["test-map-structures"], specifics)
    assert seen == [True] and not (out / "test-map-structures-elements.json").exists() and not (out / "test-map-structures-preview.jpg").exists()
    assert json.loads((folder / "pack.json").read_text())["data"] == {"x": 1}


def test_a_state_with_nothing_left_is_kept_as_nothing(tmp_path):
    """A fallen moonwell leaves nothing: its rubble's shots over the sky are empty. The stitch records
    the state with no file, and the pack lists it as null (PACK.md, Elements)."""
    from heroes_capture.capture import stitch
    from heroes_capture.common.frames import save_frame

    base, out = tmp_path / "run", tmp_path / "out"
    (base / "elements").mkdir(parents=True)
    out.mkdir()
    save_frame(base / "elements" / "structure-7-rubble", np.full((30, 40, 3), 230, np.uint8))
    save_frame(base / "elements" / "structure-7-rubble-black", np.zeros((30, 40, 3), np.uint8))
    (base / "elements" / "elements.json").write_text(json.dumps({"structure-7-rubble": {
        "kind": "structure", "element": 7, "state": "rubble", "x": 11, "y": 9, "radius": 3, "tile": 0, "camera": {"x": 10, "y": 10}}}))
    (out / "m.geo.json").write_text(json.dumps({"originCell": {"x": -48, "y": 40}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert element_stitch.write_elements({"id": "m"}, base, out, layout, 2.0, 100.0, 85.0, "m") == 1
    manifest = {"id": "m", "elements": {"structures": [{"id": 7, "type": "TownMoonwellL2", "x": 11, "y": 9, "owner": "order", "town": 1,
                                                        "core": False, "radius": 3}], "towns": [], "camps": []}}
    data = element_pack.collect_elements(out, tmp_path / "raw", tmp_path / "pack", manifest, "map", small_factor=8)
    assert data["structures"][0]["states"] == {"rubble": None}


def test_a_standing_structure_shot_from_both_sides_gets_its_team_colour_mask(tmp_path):
    """A standing structure's shots from Chaos's side (the record's chaos) are cut out like Order's,
    and its team-colour mask (team_colour.py) saved over the same rectangle: where its team colour
    went from blue to red, Order's team's amount."""
    from heroes_capture.capture import stitch
    from heroes_capture.capture.team_colour import MASK_SCALE, TEAM_COLOURS
    from heroes_capture.common.frames import save_frame

    base, out = tmp_path / "run", tmp_path / "out"
    (base / "elements").mkdir(parents=True)
    out.mkdir()
    body = np.array([60.0, 50.0, 40.0])
    for side, colour in (("", TEAM_COLOURS["order"]), ("-chaos", TEAM_COLOURS["chaos"])):
        for sky, level in (("", 230), ("-black", 0)):
            shot = np.full((30, 40, 3), level, np.uint8)
            shot[15:19, 20:24] = body + 0.5 * np.array(colour)  # the structure, opaque, its orb half team colour
            save_frame(base / "elements" / f"structure-5-standing{side}{sky}", shot)
    (base / "elements" / "elements.json").write_text(json.dumps({"structure-5-standing": {
        "kind": "structure", "element": 5, "state": "standing", "x": 11, "y": 9, "radius": 3, "tile": 0, "camera": {"x": 10, "y": 10},
        "chaos": "structure-5-standing-chaos"}}))
    (out / "m.geo.json").write_text(json.dumps({"originCell": {"x": -48, "y": 40}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert element_stitch.write_elements({"id": "m"}, base, out, layout, 2.0, 100.0, 85.0, "m") == 1
    cut = json.loads((out / "m-elements.json").read_text())["cutouts"][0]
    assert cut["teamMask"] == "structure-5-standing-team.png"
    mask = np.asarray(Image.open(out / "m-elements" / cut["teamMask"]))
    picture = np.asarray(Image.open(out / "m-elements" / cut["file"]))
    assert mask.shape[:2] == picture.shape[:2]
    assert abs(mask[..., 0].max() / 255 * MASK_SCALE - 0.5) < 0.02 and not mask[..., 1].any()


def test_a_town_hall_s_rubble_leaves_out_its_town_s_moonwell(tmp_path):
    """A hall's fall brings its town's moonwell down too: the moonwell's rubble lies in the hall's
    rubble shot. The hall's cut-out keeps only what is nearer the hall than the moonwell (as does
    every town structure's, of its town's moonwells and hall); another town's doesn't count."""
    from heroes_capture.capture import stitch
    from heroes_capture.common.frames import save_frame

    base, out = tmp_path / "run", tmp_path / "out"
    (base / "elements").mkdir(parents=True)
    out.mkdir()
    white = np.full((30, 40, 3), 230, np.uint8)
    black = np.zeros((30, 40, 3), np.uint8)
    for u, v in ((22, 17), (28, 17)):  # the hall's rubble at cell (11, 9); the moonwell's at (14, 9)
        white[v:v + 2, u:u + 2] = black[v:v + 2, u:u + 2] = 100
    save_frame(base / "elements" / "structure-5-rubble", white)
    save_frame(base / "elements" / "structure-5-rubble-black", black)
    (base / "elements" / "elements.json").write_text(json.dumps({"structure-5-rubble": {
        "kind": "structure", "element": 5, "state": "rubble", "x": 11, "y": 9, "radius": 6, "tile": 0, "camera": {"x": 10, "y": 10}}}))
    (out / "m.geo.json").write_text(json.dumps({"originCell": {"x": -48, "y": 40}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)

    def cut(moonwell_town):
        manifest = {"id": "m", "elements": {"structures": [
            {"id": 5, "type": "TownTownHallL3", "x": 11, "y": 9, "town": 1},
            {"id": 6, "type": "TownMoonwellL3", "x": 14.5, "y": 9, "town": moonwell_town}]}}
        assert element_stitch.write_elements(manifest, base, out, layout, 2.0, 100.0, 85.0, "m") == 1
        return json.loads((out / "m-elements.json").read_text())["cutouts"][0]["rect"]

    assert cut(1)[2] == 2  # the hall's square alone
    assert cut(2)[2] > 2  # another town's moonwell: both squares kept


def test_the_stitch_shows_what_it_took_for_moving(tmp_path):
    """A tile's pixels that changed colour between its two shots (not grey) shown for diagnosis: the shot marked, and the densest square of them over white and over
    black, next to the run's folder."""
    from heroes_capture.capture import stitch
    from heroes_capture.common.frames import save_frame

    base = tmp_path / "tmp" / "run"
    (base / "tiles").mkdir(parents=True)
    white = np.full((300, 300, 3), 120, np.uint8)
    black = np.full((300, 300, 3), 120, np.uint8)
    white[100:110, 100:110] = (200, 120, 60)  # a glow that moved: in the white shot only, tinted
    save_frame(base / "tiles" / "tile_0000", white)
    save_frame(base / "tiles" / "tile_0000-black", black)
    shots = stitch.Shots({"sky": {"mode": "matte"}}, base)
    assert matting.moved_pixels(white, black).sum() == 100
    stitch.show_moving(shots, [0])
    shown = list((tmp_path / "tmp").glob("moving-*/*.png"))
    assert sorted(f.name for f in shown) == ["tile-1-densest.png", "tile-1-marked.png"]
    densest = np.asarray(Image.open(next(f for f in shown if "densest" in f.name)))
    assert densest.shape == (256, 512, 3)


def test_a_standing_structure_is_masked_where_its_neighbour_stands_in_front(tmp_path):
    """A tower and a wall overlapping: the tower's shot over black with the wall faded in beside it
    shows the wall over part of it, so the tower's cut-out gets a mask of that part (hidden while
    the wall stands); the wall's shot with the tower beside it shows the tower nowhere in front, so
    the wall gets none."""
    from heroes_capture.capture import stitch
    from heroes_capture.common.frames import save_frame

    base, out = tmp_path / "run", tmp_path / "out"
    folder = base / "elements"
    folder.mkdir(parents=True)
    out.mkdir()

    def shots(key, squares, with_neighbour=None):
        white = np.full((30, 40, 3), 230, np.uint8)
        black = np.zeros((30, 40, 3), np.uint8)
        for (u0, v0), colour in squares:
            white[v0:v0 + 8, u0:u0 + 8] = black[v0:v0 + 8, u0:u0 + 8] = colour
        save_frame(folder / key, white)
        save_frame(folder / f"{key}-black", black)
        if with_neighbour:
            n, extra = with_neighbour
            both = black.copy()
            for (u0, v0), colour in extra:
                both[v0:v0 + 8, u0:u0 + 8] = colour
            save_frame(folder / f"{key}-with-{n}", both)

    # The tower at cell (11, 9): screen (18..26, 13..21); the wall at cell (13, 9): (22..30, 13..21).
    shots("structure-5-standing", [((18, 13), 100)], (6, [((22, 13), 50)]))  # the wall in front of it
    shots("structure-6-standing", [((22, 13), 50)], (5, [((18, 13), 100), ((22, 13), 50)]))  # the tower behind it
    camera = {"x": 10, "y": 10}
    (folder / "elements.json").write_text(json.dumps({
        "structure-5-standing": {"kind": "structure", "state": "standing", "element": 5, "x": 11, "y": 9, "radius": 3, "tile": 0,
                                 "camera": camera, "neighbours": [6]},
        "structure-6-standing": {"kind": "structure", "state": "standing", "element": 6, "x": 13, "y": 9, "radius": 3, "tile": 0,
                                 "camera": camera, "neighbours": [5]}}))
    (out / "m.geo.json").write_text(json.dumps({"originCell": {"x": -48, "y": 40}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert element_stitch.write_elements({"id": "m"}, base, out, layout, 2.0, 100.0, 85.0, "m") == 2
    cuts = {c["key"]: c for c in json.loads((out / "m-elements.json").read_text())["cutouts"]}
    assert cuts["structure-6-standing"]["hiddenBy"] == []
    [mask] = cuts["structure-5-standing"]["hiddenBy"]
    assert mask["id"] == 6
    hidden = np.asarray(Image.open(out / "m-elements" / mask["file"]))[..., 3] > 0
    assert hidden.shape == (8, 8) and hidden[:, 4:].all() and not hidden[:, :4].any()
