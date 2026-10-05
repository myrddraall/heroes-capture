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
    assert meta == {"size": [1300, 700], "levels": 3, "tileSize": 512}
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
    assert description["format"] == 1 and description["gameBuild"] is None and description["arenas"] is None
    assert description["map"] == {"id": "test-map", "name": "Test Map", "category": None, "validated": False, "structures": "keep",
                                  "sizeCells": [64, 64], "cameraBounds": {"left": 14, "bottom": 14, "right": 50, "top": 50}}
    assert [(l["id"], l["kind"], l["file"]) for l in description["layers"]] == [
        ("fixed", "fixed", "fixed.webp"), ("background", "parallax", "background.pmtiles"),
        ("haze", "parallax", "haze.pmtiles"), ("map", "map", "map.pmtiles")]
    map_layer = description["layers"][-1]
    assert map_layer["originCell"] == [2.0, 60.0] and map_layer["levels"] == 2 and map_layer["size"] == [900, 600]
    assert description["layers"][1]["pxPerCell"] == 21.6 and description["layers"][1]["rate"] == 0.45
    assert list(description["images"]) == ["thumbnail"]
    files = description["files"]
    assert set(files) == {"fixed.webp", "background.pmtiles", "haze.pmtiles", "map.pmtiles", "thumbnail.webp", "index.html"}
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
