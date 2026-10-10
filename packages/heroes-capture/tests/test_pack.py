"""The map pack (pack.py, PACK.md): the tile pyramids, the pack written from a stitch's files, and
the local server the reference viewer is opened through."""

import io
import json
import urllib.request

import numpy as np
import pytest
import pyvips
from PIL import Image
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import TileType

from heroes_capture import pack


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
    source = image_file(tmp_path / "layer.png", 1300, 700)
    meta = pack.write_pyramid(tmp_path / "layer.png", tmp_path / "layer.pmtiles", "layer")
    # The overview: the finest level no longer than OVERVIEW_MAX on a side, whole (here the full size).
    assert meta == {"size": [1300, 700], "levels": 3, "tileSize": 512,
                    "overview": {"file": "layer-overview.webp", "level": 2, "size": [1300, 700]}}
    overview = np.asarray(Image.open(tmp_path / "layer-overview.webp").convert("RGBA"))
    assert overview.shape == (700, 1300, 4)
    assert np.abs(overview[:400, :500, :3].astype(int) - source[:400, :500, :3]).mean() < 4
    with open(tmp_path / "layer.pmtiles", "rb") as f:
        reader = Reader(MmapSource(f))
        header = reader.header()
        assert header["tile_type"] == TileType.WEBP and (header["min_zoom"], header["max_zoom"]) == (0, 2)
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
        # Level 0, one tile: the whole image a quarter size, padded with transparency.
        whole = read_tile(reader, 0, 0, 0)
        assert whole[50, 50, 3] == 255 and whole[50, 300, 3] == 0 and whole[400, 50, 3] == 0


def test_a_big_layer_s_overview_is_a_coarser_level(tmp_path):
    """A layer wider than OVERVIEW_MAX: the overview is the finest level that fits, the same pixels
    as that level's tiles."""
    image_file(tmp_path / "big.png", 2600, 1000)  # levels: 325, 650, 1300, 2600 wide -> the overview is level 2
    meta = pack.write_pyramid(tmp_path / "big.png", tmp_path / "big.pmtiles", "big")
    assert meta["levels"] == 4 and meta["overview"] == {"file": "big-overview.webp", "level": 2, "size": [1300, 500]}
    overview = np.asarray(Image.open(tmp_path / "big-overview.webp").convert("RGBA"))
    with open(tmp_path / "big.pmtiles", "rb") as f:
        tile = read_tile(Reader(MmapSource(f)), 2, 0, 0)
    assert np.abs(overview[:500, :512, :3].astype(int) - tile[:500, :512, :3]).mean() < 3


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
    assert pack.write_pyramid(tmp_path / "empty.png", tmp_path / "empty.pmtiles", "empty") is None


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
    from heroes_capture import game_data

    monkeypatch.setattr(game_data, "open_storage", lambda install: (_ for _ in ()).throw(OSError("no game here")))
    monkeypatch.setattr(game_data, "find_install", lambda: None)
    out = tmp_path / "maps" / "test-map"
    manifest = stitched(out)
    folder = pack.write(out, manifest, ["test-map-structures"])
    assert folder == out / "pack"
    assert sorted(p.name for p in out.iterdir()) == ["pack", "raw"]  # the stitch's files moved or removed
    assert sorted(p.name for p in (out / "raw").iterdir()) == ["background.png", "composite.png", "fixed.png", "haze.png", "layers.json", "map.png"]
    description = json.loads((folder / "pack.json").read_text())
    assert description["format"] == 3 and description["gameBuild"] is None and description["arenas"] is None
    assert description["map"] == {"id": "test-map", "name": "Test Map", "category": None, "validated": False, "structures": "keep",
                                  "sizeCells": [64, 64], "cameraBounds": {"left": 14, "bottom": 14, "right": 50, "top": 50}}
    assert [(l["id"], l["kind"], l["file"]) for l in description["layers"]] == [
        ("fixed", "fixed", "fixed.webp"), ("background", "parallax", "background.pmtiles"),
        ("haze", "parallax", "haze.pmtiles"), ("map", "map", "map.pmtiles")]
    map_layer = description["layers"][-1]
    assert map_layer["originCell"] == [2.0, 60.0] and map_layer["levels"] == 2 and map_layer["size"] == [900, 600]
    assert description["layers"][1]["pxPerCell"] == 21.6 and description["layers"][1]["rate"] == 0.45
    # Each tiled layer's overview (here the layers are small: the full size).
    assert description["layers"][1]["overview"] == {"file": "background-overview.webp", "level": 1, "size": [700, 400]}
    assert map_layer["overview"] == {"file": "map-overview.webp", "level": 1, "size": [900, 600]}
    assert list(description["images"]) == ["thumbnail"]
    files = description["files"]
    assert set(files) == {"fixed.webp", "background.pmtiles", "background-overview.webp", "haze.pmtiles", "haze-overview.webp",
                          "map.pmtiles", "map-overview.webp", "thumbnail.webp", "index.html"}
    import hashlib
    assert files["map.pmtiles"]["sha256"] == hashlib.sha256((folder / "map.pmtiles").read_bytes()).hexdigest()
    assert pack.is_written(out, "keep") and not pack.is_written(out, "hide")


def test_the_local_server_answers_byte_ranges(tmp_path):
    from heroes_capture.serve import serve

    (tmp_path / "a.pmtiles").write_bytes(bytes(range(256)) * 4)
    server = serve(tmp_path, open_browser=False)
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
    from heroes_capture.frames import save_frame

    white = np.full((30, 40, 3), 230, np.uint8)
    black = np.zeros((30, 40, 3), np.uint8)
    white[v:v + 2, u:u + 2] = black[v:v + 2, u:u + 2] = 100
    save_frame(folder / key, white)
    save_frame(folder / f"{key}-black", black)


def test_an_element_s_cut_out_lands_on_its_cell_in_the_map_image(tmp_path):
    """stitch.write_elements: a tile whose camera stood at cell (10, 10) is placed at (100, 50) on the
    canvas (2 px per cell, so that cell is at (120, 65)), and the map image is the canvas from
    (4, 5). An element at cell (11, 9) lands where the image's geo puts that cell, whether its shot
    was taken from the tile's camera position or half a cell east of it."""
    from heroes_capture import stitch

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
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {0}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert stitch.write_elements({"id": "m"}, base, out, layout, scale, ax, ay, "m") == 2
    index = json.loads((out / "m-elements.json").read_text())
    # Cell (11, 9) in the image: ((11 + 48) * 2, (40 - 9) * 2) = (118, 62).
    assert [c["rect"] for c in index["cutouts"]] == [[118, 62, 2, 2], [118, 62, 2, 2]]
    cut = np.asarray(Image.open(out / "m-elements" / "structure-5-standing.png"))
    assert cut.shape == (2, 2, 4) and (cut[..., 3] == 255).all() and (cut[..., :3] == 100).all()


def test_the_pack_s_elements(tmp_path):
    """pack.collect_elements: the cut-outs packed into the pack's atlases (standing, rubble, masks,
    and small: every state at the overview scale), and data.elements listing every structure and
    camp with its states' places in them."""
    out, raw, packed = tmp_path / "out", tmp_path / "raw", tmp_path / "pack"
    (out / "m-elements").mkdir(parents=True)
    Image.new("RGBA", (3, 2), (100, 0, 0, 255)).save(out / "m-elements" / "structure-5-standing.png")
    Image.new("RGBA", (4, 3), (0, 100, 0, 255)).save(out / "m-elements" / "structure-5-rubble.png")
    Image.new("RGBA", (3, 2), (0, 0, 100, 255)).save(out / "m-elements" / "structure-6-standing.png")
    Image.new("RGBA", (3, 2), (255, 255, 255, 255)).save(out / "m-elements" / "structure-6-standing-under-5.png")
    (out / "m-elements.json").write_text(json.dumps({"image": "m", "cutouts": [
        {"key": "structure-5-standing", "kind": "structure", "element": 5, "state": "standing", "file": "structure-5-standing.png", "rect": [118, 62, 3, 2]},
        {"key": "structure-5-rubble", "kind": "structure", "element": 5, "state": "rubble", "file": "structure-5-rubble.png", "rect": [117, 61, 4, 3]},
        {"key": "structure-6-standing", "kind": "structure", "element": 6, "state": "standing", "file": "structure-6-standing.png", "rect": [120, 62, 3, 2],
         "hiddenBy": [{"id": 5, "file": "structure-6-standing-under-5.png"}]}]}))
    manifest = {"id": "m", "elements": {
        "structures": [{"id": 5, "type": "TownCannonTowerL2", "x": 11, "y": 9, "owner": "order", "town": 1, "core": False, "radius": 6},
                       {"id": 6, "type": "TownWallRadial2L3", "x": 12, "y": 9, "owner": "order", "town": 1, "core": False, "radius": 6}],
        "towns": [{"town": 1, "lane": 1, "owner": "order", "region": 2, "name": "Lane 1 - Order - Town 1"}],
        "camps": [{"camp": 1, "type": "SiegeCamp1", "x": 30, "y": 31, "spawns": [], "spread": 2, "radius": 5}]}}
    data = pack.collect_elements(out, raw, packed, manifest, "map", small_factor=2)
    assert data["layer"] == "map" and data["towns"][0]["town"] == 1
    # The atlases: the two standing cut-outs side by side; the rubble alone; the one mask; every
    # state at half size, ceil(3/2) x ceil(2/2) and ceil(4/2) x ceil(3/2), the tallest first.
    assert data["atlases"] == {
        "standing": {"file": "elements/standing.webp", "size": [6, 2], "scale": 1},
        "rubble": {"file": "elements/rubble.webp", "size": [4, 3], "scale": 1},
        "masks": {"file": "elements/masks.webp", "size": [3, 2], "scale": 1},
        "small": {"file": "elements/small.webp", "size": [6, 2], "scale": 0.5}}
    assert data["structures"][0]["states"] == {
        "standing": {"rect": [118, 62, 3, 2], "atlas": "standing", "at": [0, 0], "small": [2, 0]},
        "rubble": {"rect": [117, 61, 4, 3], "atlas": "rubble", "at": [0, 0], "small": [0, 0]}}
    assert data["structures"][1]["states"] == {
        "standing": {"rect": [120, 62, 3, 2], "atlas": "standing", "at": [3, 0], "small": [4, 0], "hiddenBy": [{"id": 5, "at": [0, 0]}]}}
    assert data["camps"] == [{"camp": 1, "type": "SiegeCamp1", "cell": [30, 31], "states": {}}]
    standing = np.asarray(Image.open(packed / "elements" / "standing.webp").convert("RGBA"))
    assert tuple(standing[0, 0]) == (100, 0, 0, 255) and tuple(standing[0, 3]) == (0, 0, 100, 255)
    small = np.asarray(Image.open(packed / "elements" / "small.webp").convert("RGBA"))
    assert small.shape == (2, 6, 4) and tuple(small[0, 2]) == (100, 0, 0, 255)  # the standing cut-out, shrunk
    assert not list((packed / "elements").glob("structure-*"))  # no file per cut-out any more
    assert (raw / "elements" / "structure-5-standing.png").exists() and not (out / "m-elements.json").exists()


def test_the_stitch_s_leftovers_are_cleared_but_not_its_elements(tmp_path):
    """collect_raw clears the stitch's leftover files from the map's folder, but leaves its elements'
    cut-outs and index for collect_elements (the first elements render lost them)."""
    out, raw = tmp_path / "out", tmp_path / "raw"
    (out / "m-elements").mkdir(parents=True)
    (out / "m-elements" / "structure-5-standing.png").write_bytes(b"png")
    (out / "m-elements.json").write_text("{}")
    (out / "m-preview.jpg").write_bytes(b"jpg")
    pack.collect_raw(out, raw, "m", [])
    assert not (out / "m-preview.jpg").exists()
    assert (out / "m-elements.json").exists() and (out / "m-elements" / "structure-5-standing.png").exists()


def test_a_state_with_nothing_left_is_kept_as_nothing(tmp_path):
    """A fallen moonwell leaves nothing: its rubble's shots over the sky are empty. The stitch records
    the state with no file, and the pack lists it as null (PACK.md, Elements)."""
    from heroes_capture import stitch
    from heroes_capture.frames import save_frame

    base, out = tmp_path / "run", tmp_path / "out"
    (base / "elements").mkdir(parents=True)
    out.mkdir()
    save_frame(base / "elements" / "structure-7-rubble", np.full((30, 40, 3), 230, np.uint8))
    save_frame(base / "elements" / "structure-7-rubble-black", np.zeros((30, 40, 3), np.uint8))
    (base / "elements" / "elements.json").write_text(json.dumps({"structure-7-rubble": {
        "kind": "structure", "element": 7, "state": "rubble", "x": 11, "y": 9, "radius": 3, "tile": 0, "camera": {"x": 10, "y": 10}}}))
    (out / "m.geo.json").write_text(json.dumps({"originCell": {"x": -48, "y": 40}}))
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {0}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert stitch.write_elements({"id": "m"}, base, out, layout, 2.0, 100.0, 85.0, "m") == 1
    manifest = {"id": "m", "elements": {"structures": [{"id": 7, "type": "TownMoonwellL2", "x": 11, "y": 9, "owner": "order", "town": 1,
                                                        "core": False, "radius": 3}], "towns": [], "camps": []}}
    data = pack.collect_elements(out, tmp_path / "raw", tmp_path / "pack", manifest, "map", small_factor=8)
    assert data["structures"][0]["states"] == {"rubble": None}


def test_a_town_hall_s_rubble_leaves_out_its_town_s_moonwell(tmp_path):
    """A hall's fall brings its town's moonwell down too: the moonwell's rubble lies in the hall's
    rubble shot. The hall's cut-out keeps only what is nearer the hall than the moonwell (as does
    every town structure's, of its town's moonwells and hall); another town's doesn't count."""
    from heroes_capture import stitch
    from heroes_capture.frames import save_frame

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
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {0}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)

    def cut(moonwell_town):
        manifest = {"id": "m", "elements": {"structures": [
            {"id": 5, "type": "TownTownHallL3", "x": 11, "y": 9, "town": 1},
            {"id": 6, "type": "TownMoonwellL3", "x": 14.5, "y": 9, "town": moonwell_town}]}}
        assert stitch.write_elements(manifest, base, out, layout, 2.0, 100.0, 85.0, "m") == 1
        return json.loads((out / "m-elements.json").read_text())["cutouts"][0]["rect"]

    assert cut(1)[2] == 2  # the hall's square alone
    assert cut(2)[2] > 2  # another town's moonwell: both squares kept


def test_the_stitch_shows_what_it_took_for_moving(tmp_path):
    """A tile's pixels that changed colour between its two shots (not grey) shown for diagnosis: the shot marked, and the densest square of them over white and over
    black, next to the run's folder."""
    from heroes_capture import stitch
    from heroes_capture.frames import save_frame

    base = tmp_path / "tmp" / "run"
    (base / "tiles").mkdir(parents=True)
    white = np.full((300, 300, 3), 120, np.uint8)
    black = np.full((300, 300, 3), 120, np.uint8)
    white[100:110, 100:110] = (200, 120, 60)  # a glow that moved: in the white shot only, tinted
    save_frame(base / "tiles" / "tile_0000", white)
    save_frame(base / "tiles" / "tile_0000-black", black)
    shots = stitch.Shots({"sky": {"mode": "matte"}}, base)
    assert stitch.moving_mask(white, black).sum() == 100
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
    from heroes_capture import stitch
    from heroes_capture.frames import save_frame

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
    layout = stitch.Layout({0: np.array([100.0, 50.0])}, {0}, {}, {0: {"index": 0, "x": 10, "y": 10}}, 40, 30, 0)
    assert stitch.write_elements({"id": "m"}, base, out, layout, 2.0, 100.0, 85.0, "m") == 2
    cuts = {c["key"]: c for c in json.loads((out / "m-elements.json").read_text())["cutouts"]}
    assert cuts["structure-6-standing"]["hiddenBy"] == []
    [mask] = cuts["structure-5-standing"]["hiddenBy"]
    assert mask["id"] == 6
    hidden = np.asarray(Image.open(out / "m-elements" / mask["file"]))[..., 3] > 0
    assert hidden.shape == (8, 8) and hidden[:, 4:].all() and not hidden[:, :4].any()
