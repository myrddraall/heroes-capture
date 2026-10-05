"""The custom minimap redrawn as an SVG (minimap_svg, PACK.md: The custom minimap as SVG): its
tracing, a drawn minimap's shapes, and its place in the pack."""

import re

import numpy as np
import pytest
import pyvips
from PIL import Image
from scipy import ndimage

from heroes_capture import minimap_svg, pack

BLACK, LIGHT, BODY, PART = (0, 0, 0), (110, 80, 130), (44, 27, 55), (92, 65, 108)


def drawn_minimap(width=240, height=180) -> Image.Image:
    """A minimap drawn as the game's are: a shape edged in black, then a light line, then the body,
    with a light part in it."""
    yy, xx = np.mgrid[0:height, 0:width]
    inside = (xx > 20) & (xx < width - 20) & (yy > 25) & (yy < height - 25)
    depth = ndimage.distance_transform_edt(inside)
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[inside] = (*BODY, 255)
    rgba[inside & (depth <= 4)] = (*LIGHT, 255)
    rgba[inside & (depth <= 2)] = (*BLACK, 255)
    part = ((xx - width / 2) / 30) ** 2 + ((yy - height / 2) / 18) ** 2 <= 1
    rgba[part] = (*PART, 255)
    return Image.fromarray(rgba)


def rendered(svg: str) -> np.ndarray:
    image = pyvips.Image.svgload_buffer(svg.encode())
    return np.ndarray(buffer=image.write_to_memory(), dtype=np.uint8, shape=[image.height, image.width, image.bands]).astype(float)


def premultiplied(rgba: np.ndarray) -> np.ndarray:
    return rgba[..., :3] * rgba[..., 3:4] / 255


def test_contours_close_round_a_shape():
    yy, xx = np.mgrid[0:60, 0:60]
    field = 20 - np.hypot(xx + 0.5 - 30, yy + 0.5 - 30)
    (loop,) = minimap_svg.contours(field, 0)
    assert abs(abs(minimap_svg.signed_area(loop)) - np.pi * 20**2) < 3
    assert np.allclose(np.hypot(*(loop - 30).T), 20, atol=0.05)


def test_a_drawn_minimap_as_its_shapes():
    picture = drawn_minimap()
    svg = minimap_svg.to_svg(picture)
    assert re.search(r'viewBox="0 0 240 180"', svg) and 'preserveAspectRatio="none"' in svg
    # The outline: one path, stroked wide in black behind the main shape, which is it filled with
    # the body's colour and stroked with the light line.
    assert svg.count('<path id="outline" vector-effect="non-scaling-stroke"') == 1  # its strokes keep their width at any size
    assert re.search(r'<g id="out-stroke" class="out-stroke" fill="#000" stroke="#000">\s*<use href="#outline" fill="none" stroke-width="[\d.]+"', svg)
    assert re.search(r'<use id="body" class="bg inner-stroke" href="#outline" fill="#2c1b37" stroke="#6e5082"', svg)
    assert svg.index('id="out-stroke"') < svg.index('id="body"')
    parts = re.search(r'<g id="parts">(.*?)</g>', svg, re.S).group(1)
    assert parts.count('<path class="terrain"') == 1 and 'fill="#5c416c"' in parts
    # Drawn, it looks like the picture.
    original, drawn = np.asarray(picture).astype(float), rendered(svg)
    inside = original[..., 3] > 0
    diff = np.abs(premultiplied(drawn) - premultiplied(original)).max(axis=2)[inside]
    assert diff.mean() < 4 and (diff > 40).mean() < 0.03


def test_a_picture_with_no_minimap_edge_isn_t_redrawn():
    with pytest.raises(minimap_svg.MinimapError):
        minimap_svg.to_svg(Image.new("RGBA", (64, 64), (0, 0, 0, 0)))


CAMERA = {"left": 10, "bottom": 6, "right": 110, "top": 86}  # its middle (60, 46)


def test_the_custom_minimap_lies_at_the_map_s_scale_below_the_camera_bounds_middle():
    bounds = pack.custom_minimap_bounds(drawn_minimap(), {"width": 120, "height": 90}, CAMERA)
    # 2 px per cell: 120 x 90 cells, centred on (60, 46 - 2.25).
    assert bounds == {"left": 0.0, "bottom": -1.25, "right": 120.0, "top": 88.75}


def test_a_map_s_minimap_placement_correction_applies():
    """minimap-placement.json corrects maps that don't follow the rule exactly (Volskaya Foundry's
    is drawn 2 cells shorter: its top comes down)."""
    bounds = pack.custom_minimap_bounds(drawn_minimap(), {"width": 120, "height": 90}, CAMERA, "Volskaya Foundry")
    assert bounds == {"left": 0.0, "bottom": -1.25, "right": 120.0, "top": 86.75}


def test_the_pack_s_svg_entry(tmp_path):
    (tmp_path / "images").mkdir()
    drawn_minimap().save(tmp_path / "images" / "custom-minimap.png")
    custom = {"file": "images/custom-minimap.png", "size": [240, 180], "source": "CustomMiniMap.dds"}
    entry = pack.minimap_svg_image(tmp_path, custom, {"width": 120, "height": 90}, CAMERA)
    assert entry == {"file": "images/custom-minimap.svg", "size": [240, 180], "source": "CustomMiniMap.dds",
                     "boundsCells": {"left": 0.0, "bottom": -1.25, "right": 120.0, "top": 88.75}}
    assert (tmp_path / entry["file"]).read_text().startswith("<svg ")


def test_a_custom_minimap_that_can_t_be_redrawn_leaves_the_pack_without_its_svg(tmp_path, capsys):
    (tmp_path / "images").mkdir()
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(tmp_path / "images" / "custom-minimap.png")
    custom = {"file": "images/custom-minimap.png", "size": [64, 64], "source": "CustomMiniMap.dds"}
    assert pack.minimap_svg_image(tmp_path, custom, {"width": 32, "height": 32}, CAMERA) is None
    assert not (tmp_path / "images" / "custom-minimap.svg").exists()
