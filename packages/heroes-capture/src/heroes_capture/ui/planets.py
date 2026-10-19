"""Planets for the Play screen background to put up in sets of its own (heroes-replay-stats
draws a new set, with a pan, as its pages change), exported for a browser to draw, each on its
own so the app loads only the ones a set uses.

Everything comes from the game's data:
  - the Play screen's own three planets (ui/play_background.py's model, the bones PLAY_TEMPLATES):
    each about its root bone, the big painted hemisphere, the size the app gives it; its hex-grid
    shield and atmosphere ring are the nexus style's, which each wears as its own, set as its own
    shield is (see below);
  - the planets of the tutorial's flyby (its cutscene FLYBY_CUTSCENE flies its camera past the
    doodad FLYBY_MODEL): three worlds (Azeroth, Korhal, Sanctuary, named by their name plates) with
    their own layered materials and atmosphere shells, lit as the flyby lights them, and nine
    painted spheres, each seen from where the flyby shows it best (largest while its centre is
    well within the camera's view: just before the camera passes it, as the tutorial's own big
    shots of each are), under the light the cutscene has on then (the cutscene switches lights as
    the camera passes each world, so each is seen under the light of its approach), so lit as the
    tutorial shows it.

A planet's file describes its body as parts, not meshes: the app draws each part on one square
facing its camera, finding the part's shape under each pixel. Each of the game's meshes is read
for what it is: a sphere (seen from its near side, or from its far side where it is modelled
inside out, as the atmosphere shells are), or a flat ring; with the rule that maps its surface to
its texture coordinates, fitted to the mesh's own (a uv sphere about a pole, linear in the point,
or round the ring); its materials; and its vertex colour. All in the planet's own frame: x to the
right, y away from the eye, z up (the app turns it to face its camera, as the game's billboards
do), in units of the body's radius; with its light (ambient, directional lights the way they
travel in that frame, and the flyby's exposure).

    <out>/planets/index.json   the planets: id, kind (play, world, painted), the style each wears
                               unless a scene says otherwise; the Play screen's own set: each where
                               the game puts it and its size; and the styles
    <out>/planets/<id>.json    kind, parts (role, shape, mapping, mapping2?, materials, color),
                               materials, textures, light?, tuning? (TUNING; a Play screen planet's:
                               its style and how it wears it, `variants`)
    <out>/planets/style-<id>.json  a style: parts to put about any body (the same form), and a
                               shade (direction, strength, softness) darkening it from a side

A style dresses any planet as another: "nexus" is the Play screen's look for a world, from the
Play screen's large planet: its hex-grid shield and its atmosphere ring about the world's own
surface, and a shade that leaves most of it dark but a crescent, as the Play screen paints its
own (the shade is this tool's, set by eye against that painting: the game lights nothing there).
The Play screen's planets wear it as their own, each as the data has its own shield: the grid's
brightness, size (its tiling against the large one's), how far it spreads (its fresnel's floor)
and the side it spreads from (its fresnel's turn), glowing as the data has it (no boost: there
are no lights to read over), and no shade (their painting is shaded already). Where the shields
differ otherwise (each one's ring and mask), they take the large one's.
    <out>/<planet>-<texture>.webp  a layer baked into the first texture coordinates (see below)

A layer that reads a mesh's second texture coordinates gets their mapping too (mapping2) where
one fits (the worlds' cloud layers); where none does (the Play screen planets' masks and
painting: an unwrap, not a rule), the layer is baked instead: its texture, through the layer's
own transform, drawn into a texture over the first coordinates as the mesh interpolates it, and
the material rewritten to read that.
"""

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

from heroes_capture.common import ui
from heroes_capture.common.casclib import Storage
from heroes_capture.common.game_data import light_sets
from heroes_capture.common.m3 import M3
from heroes_capture.ui import play_background as play
from heroes_capture.ui.play_background import Export, model_file, texture_files, texture_image

FLYBY_MODEL = "Storm_Doodad_Nexus_PlanetsFlyby"
FLYBY_CUTSCENE = "tutorial_cutscene_planetflyby.stormcutscene"
FLYBY_ANIMATION = "Stand"
CAMERA_BONE = "SC2Camera001"  # the flyby's camera: it looks along its -Z, +Y up
# Half the flyby camera's field of view: its own isn't read (the model's camera record), and the
# Play screen's is 43 degrees. A planet shows best while its centre is within WELL_WITHIN of it.
HALF_FOV = math.radians(20)
WELL_WITHIN = 0.75
# When the tutorial's own big shot of each world is, in the flyby's animation (ms): read off the
# tutorial as the game plays it (a recording of it; its time is the animation's plus 4.3 s, set by
# when the camera passes each world), where the heuristic above lands early for Azeroth. By the
# world's name plate.
SHOT_TIMES = {"azeroth": 7200, "korhal": 13700, "sanctuary": 20700}
# A world's bones hang on a master; its name plate names it.
MASTER = re.compile(r"(\w+)_Master$")
NAMEPLATE = re.compile(r"^NamePlate_(\w+?)_\d+$")
# The painted spheres' bones, and their textures' numbering.
PAINTED = re.compile(r"^Planet\d+$")
PAINTED_TEXTURE = re.compile(r"planetsflyby-planet(\d+)$")
# A world's mesh with more vertices than this is a detail too heavy for a background (Korhal's
# space station: 9000 vertices for a speck).
MAX_MESH_VERTICES = 4000
# The Play screen's planets, by their root bones.
PLAY_TEMPLATES = {"play-large": "Dummy001", "play-medium": "Dummy002", "play-small": "Dummy003"}
SAMPLE_MS = 100
# A mapping fitted worse than this (mean texture-coordinate error) is no mapping of the mesh's:
# a rule fits the game's meshes within 0.01; the Play screen hemisphere's second coordinates (an
# unwrap) come no nearer than 0.06. The first coordinates then take a plain uv sphere instead
# (three of the painted spheres map their textures some other way), its pole up, its meridian
# facing the eye, and the export says so; the second are baked.
MAPPING_TOLERANCE = 0.02
V_SAMPLES = 32
PLAIN_MAPPING = {"kind": "sphere", "pole": [0.0, 0.0, 1.0], "meridian": [0.0, -1.0, 0.0], "flip": False, "u0": 0.5,
                 "v": [round(k / (V_SAMPLES - 1), 5) for k in range(V_SAMPLES)]}
# A mesh is flat (a ring) where its points' spread across y is within this of none.
FLAT = 1e-3
# The side of a texture a layer is baked into.
BAKE_SIZE = 512
# The nexus style's shade: a darkening from the lower left and in front, leaving a crescent at
# the upper right, as the Play screen's painting has its planets (strength: how dark; softness:
# how gradually it comes on, the facing to this power).
NEXUS_SHADE = {"direction": [-0.78, -0.3, -0.55], "strength": 0.85, "softness": 1.0}
# The hexes across one tile of the Play screen planets' grid texture (Storm_LootUI_Grid_DIff):
# counted in it, ten across and twelve rows down; the app draws the pattern standing for it as
# many round a part as the texture's tiling makes them, so its size 1 is the game's.
HEX_ACROSS = 10
# How the nexus style's shield material shows (the app's knobs, set by eye): its hex grid is
# drawn by the app (the game's grid texture is too soft to read at a planet's size: `pattern`),
# its hexes 2.5 times the game's on the large planet (24 round a world, not 60), spread over the
# face (a floor under its fresnel), its lines of a width and softly aglow about themselves, and
# glowing on their own (into the bloom), so they read over a world's own lights.
NEXUS_SHIELD = {"spread": 0.6, "scale": 2.5, "width": 0.08, "soft": 0.3, "glow": 2.5}
# How each planet is best shown where the data alone isn't it, set by eye against the tutorial's
# own shots (the app's defaults otherwise): lighting (its light times this), shade, and its
# materials' settings by name (strength, blend, glow, spread, its pattern's scale, cut, width, soft).
TUNING: dict[str, dict] = {
    # Korhal: its lights at half (the user's eye, against the tutorial's dim shot of it); its
    # edge-light haze at 0.42 (the user's choice); its blue back light's crescent at the upper
    # left shaded down, where the shot shows only a lighter patch (the shade faces where that
    # light comes from).
    "korhal": {"lighting": 0.5, "materials": {"edgeLight222-korhal": {"strength": 0.42}},
               "shade": {"direction": [-0.71, 0.47, 0.53], "strength": 0.55, "softness": 1.0}},
}


def flyby_light_schedule(storage: Storage) -> list[tuple[int, str]]:
    """When the flyby's cutscene switches its light, and to which: [(start ms, light id)]."""
    hits = [n for n in storage.find(f"*\\cutscenes\\{FLYBY_CUTSCENE}") if "\\base.stormdata\\" in n.lower()]
    if not hits:
        raise SystemExit(f"the game's data has no {FLYBY_CUTSCENE}")
    root = ET.fromstring((storage.read(hits[0]) or b"").decode("utf-8-sig"))
    node = next(n for n in root.iter("CCutsceneNodeActiveLight") if not n.get("filter"))
    return sorted((int(e.get("start", "0")), e.get("lightID")) for e in node.iter("CCutsceneElementActiveLight"))


def light_at(schedule: list[tuple[int, str]], ms: int) -> str:
    """The light the schedule has on at a time."""
    return max((s for s in schedule if s[0] <= ms), key=lambda s: s[0])[1]


def light_in(table: dict, light_id: str, turn: np.ndarray) -> dict:
    """A light as the app lights with it: {ambient, directional: [{direction, color}] (colour times
    multiplier), exposure, bloomThreshold? (what the light's HDR blooms: the flyby lights' 0.6,
    below the Play screen's 0.85, is what spreads a world's city lights into a glow)}. Missing
    fields come from the light's parents. A light's direction is
    the way it travels in the world (z up, as the game's sun lights travel downwards); `turn`
    takes world directions into the planet's frame as the flyby's camera sees it then (its bank
    included). Measured against the tutorial's own shots of each world's approach: so Azeroth is
    lit from its lower left, with a blue rim at its upper right from behind, where a reading about
    the camera lights the wrong sides."""
    from heroes_capture.common.lighting import _inherited

    directional = _inherited(table, light_id, "directional") or []
    exposure = _inherited(table, light_id, "hdrExposure")
    if exposure is None:
        raise SystemExit(f"the light {light_id} has no HDR exposure")

    def travel(direction: list[float]) -> list[float]:
        return [round(float(v), 5) for v in turn @ np.array(direction)]

    bloom = _inherited(table, light_id, "hdrBloomThreshold")
    return {"ambient": [round(c, 5) for c in (_inherited(table, light_id, "ambient") or [0, 0, 0])],
            "directional": [{"direction": travel(d["direction"]), "color": [round(c * d["multiplier"], 5) for c in d["color"]]}
                            for d in directional if any(d["direction"])],
            "exposure": exposure, **({"bloomThreshold": bloom} if bloom is not None else {})}


def facing(view: np.ndarray, up: np.ndarray) -> np.ndarray:
    """The turn (rows: the planet's axes) into the frame where the eye is along -Y (`view` points
    from the eye to the planet) and `up` is as near +Z as it can be."""
    y = view / np.linalg.norm(view)
    z = up - (up @ y) * y
    z /= np.linalg.norm(z)
    return np.array([np.cross(y, z), y, z])


def frame_of(world: np.ndarray, turn: np.ndarray, scale: float) -> np.ndarray:
    """A matrix taking the model's world into a planet's own space: about the bone's origin, in the
    bone's orientation, turned by `turn`, in units of `scale`."""
    rotation = world[:3, :3] / np.linalg.norm(world[:3, :3], axis=0)
    into = np.eye(4)
    into[:3, :3] = turn @ rotation.T / scale
    into[:3, 3] = -into[:3, :3] @ world[:3, 3]
    return into


def chain(bones: list, names: list[str], index: int) -> list[str]:
    """A bone's name and its ancestors', nearest first."""
    out = []
    while index >= 0:
        out.append(names[index])
        index = bones[index]["parent"]
    return out


def radius_of(positions) -> float:
    p = np.asarray(positions, dtype=float).reshape(-1, 3)
    return float(np.linalg.norm(p, axis=1).max())


# --- A mesh read as a part: its shape, and the rule mapping its surface to its texture ---------


def sphere_centre(positions: np.ndarray) -> np.ndarray:
    """The centre of the sphere through the points, least squares (a hemisphere's too, where its
    mean would be well inside it): |p|² = 2 c·p + k."""
    a = np.c_[2 * positions, np.ones(len(positions))]
    (cx, cy, cz, _), *_ = np.linalg.lstsq(a, np.einsum("ij,ij->i", positions, positions), rcond=None)
    return np.array([cx, cy, cz])


def shape_of(positions: np.ndarray, normals: np.ndarray) -> dict:
    """A mesh's shape about the origin, in the frame's units: flat, a ring (its inner and outer
    radii, its depth along y); else a sphere of its mean radius, seen from its near side, or from
    its far side where its normals point inward (an atmosphere shell, modelled inside out)."""
    if positions[:, 1].std() < FLAT:
        r = np.hypot(positions[:, 0], positions[:, 2])
        return {"kind": "ring", "inner": round(float(r.min()), 4), "outer": round(float(r.max()), 4),
                "depth": round(float(positions[:, 1].mean()), 4)}
    radius = float(np.linalg.norm(positions, axis=1).mean())
    inward = float(np.einsum("ij,ij->i", normals, positions).mean()) < 0
    return {"kind": "sphere", "radius": round(radius, 4), "side": "far" if inward else "near"}


def _circular_mean(values: np.ndarray) -> float:
    """The mean of values on a circle of length 1, in [0, 1)."""
    angle = values * 2 * math.pi
    return math.atan2(np.sin(angle).mean(), np.cos(angle).mean()) / (2 * math.pi) % 1.0


def fit_sphere_mapping(positions: np.ndarray, uvs: np.ndarray) -> tuple[dict, float]:
    """A uv sphere fitted to a mesh's texture coordinates: the pole (where v is least); v by the
    angle from the pole, as the mesh's rows of vertices have it (V_SAMPLES values from the pole
    to the opposite one, between rows as the mesh interpolates, beyond the last row as it is: a
    hemisphere's v isn't linear in the angle, and its mask's edge shows where it is off); u round
    the pole from the meridian, from u0, the other way round when flipped. With the mean error in
    u (round its wrap) and v, away from the poles."""
    n = positions / np.linalg.norm(positions, axis=1)[:, None]
    u, v = uvs[:, 0], uvs[:, 1]
    span = max(v.max() - v.min(), 1e-6)
    low = n[v < v.min() + 0.02 * span].mean(0)
    high = n[v > v.max() - 0.02 * span].mean(0)
    pole = low - high
    pole /= np.linalg.norm(pole)
    # A uv sphere's vertices with the same v lie on a ring about the pole (a half ring on a
    # hemisphere): the pole is the rings' planes' normal, which places it where the cluster above
    # only roughly does.
    rows: dict[float, list[int]] = {}
    for k, value in enumerate(np.round(v, 4)):
        rows.setdefault(float(value), []).append(k)
    normals = []
    for ks in rows.values():
        if len(ks) < 4:
            continue
        points = n[ks] - n[ks].mean(0)
        _, singular, axes = np.linalg.svd(points, full_matrices=False)
        if singular[1] < 1e-3:  # a line, not a ring
            continue
        normal = axes[2]
        normals.append(normal if normal @ pole >= 0 else -normal)
    if len(normals) >= 3:
        pole = np.mean(normals, axis=0)
        pole /= np.linalg.norm(pole)
    polar = np.arccos(np.clip(n @ pole, -1, 1)) / math.pi
    by_row = sorted((float(polar[ks].mean()), float(value)) for value, ks in rows.items())
    at = np.linspace(0, 1, V_SAMPLES)
    table = np.interp(at, [r[0] for r in by_row], [r[1] for r in by_row])
    meridian = np.cross(pole, [0, 0, 1]) if abs(pole[2]) < 0.9 else np.cross(pole, [1, 0, 0])
    meridian /= np.linalg.norm(meridian)
    azimuth = np.arctan2(n @ np.cross(pole, meridian), n @ meridian) / (2 * math.pi)
    away = (polar > 0.1) & (polar < 0.9)
    best = None
    for sign in (1, -1):
        u0 = _circular_mean((u - sign * azimuth)[away])
        error = np.abs(((u - (sign * azimuth + u0) + 0.5) % 1.0) - 0.5)[away].mean()
        if best is None or error < best[0]:
            best = (error, sign, u0)
    error_u, sign, u0 = best
    error_v = np.abs(np.interp(polar, at, table) - v)[away].mean()
    mapping = {"kind": "sphere", "pole": [round(float(c), 5) for c in pole],
               "meridian": [round(float(c), 5) for c in meridian], "flip": sign < 0,
               "u0": round(float(u0), 5), "v": [round(float(c), 5) for c in table]}
    return mapping, float(error_u + error_v)


def fit_quality(fits: list[tuple[dict, float]]) -> str:
    return ", ".join(f"{f[0]['kind']} off by {f[1]:.2f}" for f in fits)


def fit_linear_mapping(positions: np.ndarray, uvs: np.ndarray) -> tuple[dict, float]:
    """u and v each linear in the point (a·x + b·y + c·z + d), fitted; with the mean error."""
    a = np.c_[positions, np.ones(len(positions))]
    (cu, *_), (cv, *_) = np.linalg.lstsq(a, uvs[:, 0], rcond=None), np.linalg.lstsq(a, uvs[:, 1], rcond=None)
    error = np.abs(a @ cu - uvs[:, 0]).mean() + np.abs(a @ cv - uvs[:, 1]).mean()
    return {"kind": "linear", "u": [round(float(c), 5) for c in cu], "v": [round(float(c), 5) for c in cv]}, float(error)


def ring_mapping(positions: np.ndarray, uvs: np.ndarray) -> dict:
    """A ring's texture coordinates, as the Play screen's atmospheres have them: u greatest at
    one azimuth (radians in the xz plane, from +x towards +z), falling to nothing at the
    opposite side either way round; v nothing at both edges, all midway."""
    u = uvs[:, 0]
    azimuth = np.arctan2(positions[:, 2], positions[:, 0])
    top = azimuth[u > u.max() - 0.02]
    where = math.atan2(np.sin(top).mean(), np.cos(top).mean())
    return {"kind": "ring", "from": round(where, 5), "uMax": round(float(u.max()), 5)}


def mapping_of(shape: dict, positions: np.ndarray, uvs: np.ndarray, what: str) -> dict:
    """The rule mapping a mesh's surface to its texture coordinates: a ring's own; else whichever
    of a uv sphere and a linear rule fits its own coordinates better, or the plain uv sphere where
    neither does."""
    if shape["kind"] == "ring":
        return ring_mapping(positions, uvs)
    fits = [fit_sphere_mapping(positions, uvs), fit_linear_mapping(positions, uvs)]
    mapping, error = min(fits, key=lambda f: f[1])
    if error > MAPPING_TOLERANCE:
        ui.warn(f"{what}: its texture coordinates fit no mapping ({fit_quality(fits)}): mapped as a plain uv sphere")
        return dict(PLAIN_MAPPING)
    return mapping


def components(count: int, indices: list[int]) -> list[np.ndarray]:
    """A mesh's connected pieces (vertices joined by its triangles), largest first: a region may
    hold several spheres (a painted planet with its moons)."""
    parent = list(range(count))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for t in range(0, len(indices), 3):
        for b in indices[t + 1:t + 3]:
            parent[find(indices[t])] = find(b)
    groups: dict[int, list[int]] = {}
    for k in range(count):
        groups.setdefault(find(k), []).append(k)
    return [np.array(g) for g in sorted(groups.values(), key=len, reverse=True)]


def read_region(m3: M3, region, world: list[np.ndarray]) -> dict:
    """A mesh region placed by `world` (into the planet's frame): its largest connected piece and
    the pieces about it (a shell split at its seam, the bands of a world's city lights); what lies
    far from it (stray vertices, what a painted sphere's material is also on elsewhere) is left
    out. Arrays: positions, normals, uvs, uvs2, colors, and the triangles (indices) among them."""
    data = m3.mesh(region, world)
    positions = np.array(data["positions"], dtype=float)
    pieces = components(len(positions), data["indices"])
    main = positions[pieces[0]].mean(0)
    size = float(np.linalg.norm(positions[pieces[0]] - main, axis=1).max())
    kept = np.concatenate([p for p in pieces if len(p) >= 3 and np.linalg.norm(positions[p].mean(0) - main) < 2 * size])
    renumber = np.full(len(positions), -1)
    renumber[kept] = np.arange(len(kept))
    triangles = np.array(data["indices"]).reshape(-1, 3)
    triangles = renumber[triangles[(renumber[triangles] >= 0).all(axis=1)]]
    return {"positions": positions[kept], "normals": np.array(data["normals"], dtype=float)[kept],
            "uvs": np.array(data["uvs"], dtype=float)[kept], "uvs2": np.array(data["uvs2"], dtype=float)[kept],
            "colors": np.array(data["colors"], dtype=float)[kept], "triangles": triangles}


def part_of(data: dict, materials: list[str], what: str, second_uvs: bool = False) -> dict:
    """A region (read_region) read as a part of its body, about its own centre; with the mapping
    of its second texture coordinates where they are wanted and a rule fits them."""
    positions, normals, uvs = data["positions"], data["normals"], data["uvs"]
    flat = positions[:, 1].std() < FLAT
    centre = positions.mean(0) * [1, 0, 1] if flat else sphere_centre(positions)
    about = positions - centre
    shape = shape_of(about, normals)
    if flat:
        shape["depth"] = round(float(positions[:, 1].mean()), 4)  # in the body's plane, about its axis
    elif np.linalg.norm(centre) > 1e-3:
        shape["centre"] = [round(float(c), 4) for c in centre]
    part = {"shape": shape, "mapping": mapping_of(shape, about, uvs, what), "materials": materials}
    if second_uvs and shape["kind"] != "ring":
        mapping, error = min([fit_sphere_mapping(about, data["uvs2"]), fit_linear_mapping(about, data["uvs2"])], key=lambda f: f[1])
        if error <= MAPPING_TOLERANCE:
            part["mapping2"] = mapping
    color = [int(round(c)) for c in data["colors"].mean(0)]
    if color != [255, 255, 255, 255]:
        part["color"] = color
    return part


# --- A layer on the second texture coordinates, baked into the first --------------------------


def layer_uv(uv: np.ndarray, layer: dict) -> np.ndarray:
    """A layer's texture coordinates from a mesh's, as the app's shader has them: through the
    layer's tiling about the texture's centre, less its offset, and its rotation."""
    offset = np.array(layer.get("offset", [0, 0]), dtype=float)
    tiling = np.array(layer.get("tiling", [1, 1]), dtype=float)
    t = (uv - 0.5 - offset) * tiling
    c, s = math.cos(layer.get("angle", 0.0)), math.sin(layer.get("angle", 0.0))
    return np.c_[c * t[:, 0] - s * t[:, 1], s * t[:, 0] + c * t[:, 1]] + 0.5


def sample(image: np.ndarray, uv: np.ndarray, wrap: tuple[bool, bool]) -> np.ndarray:
    """Texels of an image (rows down v, columns along u) at coordinates, bilinear, repeating
    across an axis that wraps and stopping at the edge of one that doesn't."""
    h, w = image.shape[:2]
    x = uv[:, 0] * w - 0.5
    y = uv[:, 1] * h - 0.5
    out = np.zeros((len(uv), image.shape[2]))
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    for dx, dy, weight in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        col = (x0 + dx).astype(int)
        row = (y0 + dy).astype(int)
        col = col % w if wrap[0] else np.clip(col, 0, w - 1)
        row = row % h if wrap[1] else np.clip(row, 0, h - 1)
        out += weight * image[row, col]
    return out


def bake_layer(image: Image.Image, layer: dict, uvs: np.ndarray, uvs2: np.ndarray, triangles: np.ndarray, size: int = BAKE_SIZE) -> Image.Image:
    """A layer that reads a mesh's second texture coordinates, drawn into a texture over its first
    ones, as the mesh interpolates: each triangle's texels where the first coordinates put it,
    from the layer's texture where the second ones (interpolated across it) and the layer's
    transform read it."""
    source = np.asarray(image, dtype=float)
    out = np.zeros((size, size, 4))
    wrap = tuple(layer.get("wrap", [True, True]))
    for tri in triangles:
        a, b, c = uvs[tri] * size
        x0, x1 = int(max(0, math.floor(min(a[0], b[0], c[0])))), int(min(size - 1, math.ceil(max(a[0], b[0], c[0]))))
        y0, y1 = int(max(0, math.floor(min(a[1], b[1], c[1])))), int(min(size - 1, math.ceil(max(a[1], b[1], c[1]))))
        if x1 < x0 or y1 < y0:
            continue
        px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        px, py = px.ravel(), py.ravel()
        det = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if abs(det) < 1e-9:
            continue
        wb = ((px - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (py - a[1])) / det
        wc = ((b[0] - a[0]) * (py - a[1]) - (px - a[0]) * (b[1] - a[1])) / det
        wa = 1 - wb - wc
        inside = (wa >= -1e-3) & (wb >= -1e-3) & (wc >= -1e-3)
        if not inside.any():
            continue
        second = wa[inside, None] * uvs2[tri[0]] + wb[inside, None] * uvs2[tri[1]] + wc[inside, None] * uvs2[tri[2]]
        out[py[inside].astype(int), px[inside].astype(int)] = sample(source, layer_uv(second, layer), wrap)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def baked_material(export: Export, storage: Storage, key: str, planet: str, data: dict, baked: dict) -> str:
    """A material's copy with each layer reading the mesh's second texture coordinates baked over
    the first (into `baked`: texture key -> image, whether lossless), or the material itself where
    none does; its key."""
    material = export.materials[key]
    layers = [("diffuse", material["diffuse"]), ("emissive", material["emissive"])] + [("alphaMasks", m) for m in material["alphaMasks"]]
    if not any(layer and layer.get("uvSet") == 2 for _, layer in layers):
        return key
    new_key = f"{key}-{planet}"
    if new_key in export.materials:
        return new_key
    copy = json.loads(json.dumps(material))
    for slot in ("diffuse", "emissive", "alphaMasks"):
        found = copy[slot] if slot == "alphaMasks" else ([copy[slot]] if copy[slot] else [])
        for layer in found:
            if layer.get("uvSet") != 2:
                continue
            texture_key = f"{planet}-{layer['texture']}-{slot.lower()}"
            if texture_key not in baked:
                image = bake_layer(texture_image(storage, export.textures[layer["texture"]]), layer, data["uvs"], data["uvs2"], data["triangles"])
                baked[texture_key] = (image, slot == "alphaMasks" or layer.get("channel") in ("a", "rgba"))
                export.baked_from[texture_key] = layer["texture"]
            layer.update({"texture": texture_key, "offset": [0.0, 0.0], "angle": 0.0, "tiling": [1.0, 1.0], "wrap": [False, False]})
            del layer["uvSet"]
            layer.pop("scroll", None)
    export.materials[new_key] = copy
    return new_key


class Planet:
    """A planet's file as it is built: its parts in its own space, with the materials and textures
    they use (the export's, shared)."""

    def __init__(self, export: Export, kind: str, name: str, storage: Storage, baked: dict):
        self.export, self.kind, self.name, self.storage, self.baked = export, kind, name, storage, baked
        self.parts: list[dict] = []
        self.light: dict | None = None
        # Where the game puts it (a Play screen planet): its position, and its body's radius there.
        self.placed: tuple[list[float], float] | None = None

    def add(self, m3: M3, region, world: list[np.ndarray], into: np.ndarray, materials: list, what: str) -> dict:
        keys = [self.export.material(r) for r in materials]
        data = read_region(m3, region, [into @ w for w in world])
        second = self.export.uses_second_uvs(keys)
        part = part_of(data, keys, what, second)
        if second and "mapping2" not in part:
            part["materials"] = [baked_material(self.export, self.storage, key, self.name, data, self.baked) for key in keys]
        shape = part["shape"]
        role = "ring" if shape["kind"] == "ring" else "shell" if shape["side"] == "far" else "surface" if not self.parts else "layer"
        self.parts.append({"role": role, **part})
        return part

    def file(self) -> dict:
        out = planet_file(self.export, self.kind, self.parts, self.light)
        if self.name in TUNING:
            out["tuning"] = TUNING[self.name]
        return out


def planet_file(export: Export, kind: str, parts: list[dict], light: dict | None, shade: dict | None = None) -> dict:
    used = {key for part in parts for key in part["materials"]}
    materials = {key: export.materials[key] for key in sorted(used)}
    textures = {layer["texture"] for m in materials.values()
                for layer in (m["diffuse"], m["emissive"], *m["alphaMasks"]) if layer and "texture" in layer}
    out: dict = {"kind": kind, "parts": parts, "materials": materials, "textures": sorted(textures)}
    if light:
        out["light"] = light
    if shade:
        out["shade"] = shade
    return out


def nexus_style(export: Export, large: Planet) -> dict:
    """The nexus style from the Play screen's large planet: its surface's additive materials but
    its painting (the hex grid) as a shield about the body, its ring, and the shade."""
    surface = next(p for p in large.parts if p["role"] == "surface")
    shield = shield_materials(export, surface)
    if not shield:
        raise SystemExit("the Play screen's large planet has no hex grid to make the nexus style's shield of")
    parts = [{**surface, "role": "shield", "pattern": {"kind": "hex", "across": HEX_ACROSS}, "materials": shield}] + [p for p in large.parts if p["role"] == "ring"]
    tuning = {"materials": {key: dict(NEXUS_SHIELD) for key in shield}}
    return {**planet_file(export, "style", parts, None, NEXUS_SHADE), "tuning": tuning}

def shield_materials(export: Export, surface: dict) -> list[str]:
    """A Play screen planet's shield: its surface's blended materials with no painting (the hex grid)."""
    return [k for k in surface["materials"]
            if export.materials[k]["blend"] != "opaque" and not (export.materials[k]["diffuse"] or {}).get("texture")]


def spread_from(yaw: float, pitch: float) -> list[float]:
    """The point on a body's face (its frame: y away from the eye) a fresnel turned by `yaw` (to
    the right) and `pitch` (upwards, negative) is brightest on: opposite where the turn leans."""
    return [round(-math.cos(pitch) * math.sin(yaw), 4), round(-math.cos(pitch) * math.cos(yaw), 4), round(math.sin(pitch), 4)]


def worn_shield(export: Export, own: str, style: str) -> dict:
    """How a Play screen planet shows the nexus style's shield material (`style`) as its own
    shield (`own`) is in the data: its grid's brightness and size against the style's, its
    fresnel's floor (how far it spreads) and turn (the side it spreads from), its glow the data's."""
    mine, theirs = export.materials[own]["emissive"], export.materials[style]["emissive"]
    fresnel = mine["fresnel"]
    return {"strength": round(mine["brightMult"] / theirs["brightMult"], 4),
            "scale": round(theirs["tiling"][0] / mine["tiling"][0], 4),
            "spread": fresnel["min"], "spreadFrom": spread_from(fresnel["yaw"], fresnel["pitch"]), "glow": 1.0}


def play_base(export: Export, planet: Planet, style_id: str, style: dict) -> dict:
    """A Play screen planet as a base: its surface without its shield (the hex grid) and no ring,
    wearing the style (`style_id`) that brings both, set as its own shield is (and not shaded:
    its painting is)."""
    parts, own = [], None
    for part in planet.parts:
        if part["role"] == "ring":
            continue
        if part["role"] == "surface":
            shield = shield_materials(export, part)
            if len(shield) != 1:
                raise SystemExit(f"{planet.name}: {len(shield)} hex-grid materials, not one")
            own = shield[0]
            part = {**part, "materials": [k for k in part["materials"] if k != own]}
        parts.append(part)
    worn = next(p for p in style["parts"] if p["role"] == "shield")["materials"][0]
    out = planet_file(export, planet.kind, parts, planet.light)
    out["tuning"] = {"style": style_id, "variants": {style_id: {
        "shade": {"strength": 0}, "materials": {worn: worn_shield(export, own, worn)}}}}
    return out


def play_planets(storage: Storage, export: Export, baked: dict) -> dict[str, Planet]:
    """The Play screen's planets, each about its root bone, its body a unit radius (the large and
    medium ones a hemisphere towards -Y, the small one a sphere), as the model poses them without
    a camera to face (their billboards as modelled: facing -Y)."""
    info = play.cutscene(storage)
    m3 = M3(model_file(storage, info["model"]))
    groups = m3.groups()
    played = list(groups[info["animation"]])
    played += [c for name, stcs in groups.items() if stcs and all(c["concurrent"] for c in stcs) for c in stcs]
    tracks = {}
    for collection in played:
        tracks.update(m3.tracks(collection))
    export.m3, export.tracks = m3, tracks
    world = m3.pose(tracks)
    bones = m3.bones()
    names = [b.text("name") for b in bones]
    region_materials = m3.region_materials()
    planets = {}
    for key, root in PLAY_TEMPLATES.items():
        root_index = names.index(root)
        regions = [(i, r) for i, r in enumerate(m3.regions()) if root in chain(bones, names, m3.region_bone(r))]
        # The body: the opaque mesh (the painting); its radius is the unit.
        body = next((i, r) for i, r in regions
                    if any(m3.material(ref)[0]["blendMode"] == 0 for ref in region_materials.get(i, [])))
        into = frame_of(world[root_index], np.eye(3), 1.0)
        unit = radius_of(m3.mesh(body[1], [into @ w for w in world])["positions"])
        into = frame_of(world[root_index], np.eye(3), unit)
        planet = Planet(export, "play", key, storage, baked)
        planet.placed = ([round(float(v), 3) for v in world[root_index][:3, 3]], round(unit, 3))
        for i, region in sorted(regions, key=lambda ir: ir != body):
            materials = [r for ref in region_materials.get(i, []) for r in m3.material(ref)]
            planet.add(m3, region, world, into, materials, f"{key}'s {names[m3.region_bone(region)]}")
        planets[key] = planet
    return planets


def flyby_planets(storage: Storage, export: Export, baked: dict) -> dict[str, Planet]:
    """The flyby's planets, each in its own space as the flyby shows it best (its camera's view
    nearest to straight at it), under the light on then."""
    m3 = M3(model_file(storage, FLYBY_MODEL))
    tracks = {}
    for collection in m3.groups()[FLYBY_ANIMATION]:
        tracks.update(m3.tracks(collection))
    export.m3, export.tracks = m3, tracks
    bones = m3.bones()
    names = [b.text("name") for b in bones]
    region_materials = m3.region_materials()
    camera = names.index(CAMERA_BONE)
    schedule = flyby_light_schedule(storage)
    lights = light_sets(storage)["lights"]
    length = max(int(t.frames[-1]) for t in tracks.values())
    poses = {ms: m3.pose(tracks, ms) for ms in range(0, length + 1, SAMPLE_MS)}

    def bone_radius(bone: int, region) -> float:
        """A body's radius in its bone's own units (the bone at scale 1), from any pose that has it."""
        for world in poses.values():
            scale = np.linalg.norm(world[bone][:3, 0])
            if scale < 1e-4:
                continue
            into = frame_of(world[bone], np.eye(3), 1.0)
            return radius_of(m3.mesh(region, [into @ w for w in world])["positions"]) / scale
        raise SystemExit(f"{names[bone]} is never there in the flyby")

    def best_view(bone: int, radius: float) -> tuple[int, np.ndarray]:
        """When the flyby shows a bone's body best: largest on screen while its centre is well
        within the camera's view (just before the camera passes it; failing that, nearest to
        straight ahead): the time, and the pose then."""
        largest = None
        straightest = None
        for ms, world in poses.items():
            scale = np.linalg.norm(world[bone][:3, 0])
            if scale < 1e-4:
                continue
            cam = world[camera]
            forward = -cam[:3, 2] / np.linalg.norm(cam[:3, 2])
            to = world[bone][:3, 3] - cam[:3, 3]
            distance = np.linalg.norm(to)
            if distance < 1e-6:
                continue
            off = math.acos(max(-1.0, min(1.0, float(to / distance @ forward))))
            size = math.asin(min(1.0, radius * scale / distance))
            if straightest is None or off < straightest[0]:
                straightest = (off, ms)
            if off <= HALF_FOV * WELL_WITHIN and (largest is None or size > largest[0]):
                largest = (size, ms)
        best = largest or straightest
        if best is None:
            raise SystemExit(f"{names[bone]} is never in the flyby's view")
        return best[1], poses[best[1]]

    def frame(bone: int, ms: int, world: list[np.ndarray], unit: float) -> tuple[np.ndarray, np.ndarray]:
        """Into the bone's space turned to face the camera as it is then: the matrix, and the turn
        that takes world directions into that space."""
        cam = world[camera]
        rotation = world[bone][:3, :3] / np.linalg.norm(world[bone][:3, :3], axis=0)
        view = rotation.T @ (world[bone][:3, 3] - cam[:3, 3])
        up = rotation.T @ cam[:3, 1]
        turn = facing(view, up)
        return frame_of(world[bone], turn, unit), turn @ rotation.T

    def framed(bone: int, ms: int, world: list[np.ndarray], body) -> tuple[np.ndarray, np.ndarray]:
        """The frame about a body at a time, in units of its radius (about its own centre: a
        painted sphere's bone sits on its surface, not at its centre): the matrix, and the turn."""
        into, _ = frame(bone, ms, world, 1.0)
        data = m3.mesh(body, [into @ w for w in world])
        positions = np.array(data["positions"])[components(len(data["positions"]), data["indices"])[0]]
        centre = sphere_centre(positions)
        unit = float(np.linalg.norm(positions - centre, axis=1).max())
        into, turn = frame(bone, ms, world, unit)
        into[:3, 3] -= centre / unit
        return into, turn

    planets: dict[str, Planet] = {}
    # The worlds: each master's meshes but its name plates and the painted spheres among them.
    for master, name in ((i, MASTER.match(n)) for i, n in enumerate(names)):
        if not name:
            continue
        regions = [(i, r) for i, r in enumerate(m3.regions())
                   if names[master] in chain(bones, names, m3.region_bone(r))
                   and not NAMEPLATE.match(names[m3.region_bone(r)]) and not PAINTED.match(names[m3.region_bone(r)])]
        plates = [NAMEPLATE.match(n) for n in names if NAMEPLATE.match(n) and names[master] in chain(bones, names, names.index(n))]
        if not plates:
            raise SystemExit(f"{names[master]} has no name plate to name it")
        key = plates[0].group(1).lower()
        body = next((i, r) for i, r in regions
                    if any(m3.material(ref)[0]["blendMode"] == 0 for ref in region_materials.get(i, [])))
        if key in SHOT_TIMES:
            ms = SHOT_TIMES[key] // SAMPLE_MS * SAMPLE_MS
            world = poses[ms]
        else:
            ms, world = best_view(master, bone_radius(master, body[1]))
        into, turn = framed(master, ms, world, body[1])
        planet = Planet(export, "world", key, storage, baked)
        for i, region in sorted(regions, key=lambda ir: ir != body):
            if region["vertices"] > MAX_MESH_VERTICES:
                continue
            materials = [r for ref in region_materials.get(i, []) for r in m3.material(ref)]
            planet.add(m3, region, world, into, materials, f"{key}'s {names[m3.region_bone(region)]}")
        planet.light = light_in(lights, light_at(schedule, ms), turn)
        ui.info(f"{key}: as the flyby shows it at {ms / 1000:.1f} s, under {light_at(schedule, ms)}")
        planets[key] = planet
    # The painted spheres, lit as the flyby does.
    for i, region in enumerate(m3.regions()):
        bone = m3.region_bone(region)
        if not PAINTED.match(names[bone]):
            continue
        materials = [r for ref in region_materials.get(i, []) for r in m3.material(ref)]
        keys = [export.material(r) for r in materials]
        texture = export.materials[keys[0]].get("diffuse", {}).get("texture", "")
        number = PAINTED_TEXTURE.search(texture)
        if not number:
            continue
        key = f"planet-{number.group(1)}"
        if key in planets:
            continue
        ms, world = best_view(bone, bone_radius(bone, region))
        into, turn = framed(bone, ms, world, region)
        ui.info(f"{key}: as the flyby shows it at {ms / 1000:.1f} s, under {light_at(schedule, ms)}")
        planet = Planet(export, "painted", key, storage, baked)
        planet.add(m3, region, world, into, materials, f"{key}'s {names[bone]}")
        planet.light = light_in(lights, light_at(schedule, ms), turn)
        planets[key] = planet
    return planets


def export(storage: Storage, out: Path) -> dict:
    """Export the planets into `out`/planets (see the module's description); the index."""
    # One export's materials and textures serve every planet: the Play screen's model's first, then
    # the flyby's, their keys kept apart by name.
    ex = Export(None, {})  # type: ignore[arg-type]  # the models are set as each is read
    baked: dict[str, tuple[Image.Image, bool]] = {}
    planets = play_planets(storage, ex, baked)
    planets.update(flyby_planets(storage, ex, baked))
    files = {key: planet.file() for key, planet in planets.items()}
    files["style-nexus"] = nexus_style(ex, planets["play-large"])
    # The Play screen's planets as bases, wearing the nexus style as their shields are.
    for key in PLAY_TEMPLATES:
        files[key] = play_base(ex, planets[key], "nexus", files["style-nexus"])
    folder = out / "planets"
    folder.mkdir(parents=True, exist_ok=True)
    textures = texture_files(storage, ex.textures, ex.alpha_read, out, baked, ex.sources, ex.baked_from)
    for key, file in files.items():
        file["textures"] = {t: textures[t] for t in file["textures"]}
        (folder / f"{key}.json").write_text(json.dumps(file, separators=(",", ":")), encoding="utf-8", newline="\n")
    index = {"planets": [{"id": key, "kind": file["kind"], **({"style": file["tuning"]["style"]} if "style" in file.get("tuning", {}) else {})}
                         for key, file in files.items() if file["kind"] != "style"],
             "play": [{"id": key, "position": planet.placed[0], "radius": planet.placed[1]}
                      for key, planet in planets.items() if planet.placed],
             "styles": [{"id": key.removeprefix("style-")} for key, file in files.items() if file["kind"] == "style"]}
    (folder / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8", newline="\n")
    for stale in folder.glob("*.json"):
        if stale.stem not in files and stale.name != "index.json":
            stale.unlink()
    for stale in out.glob("*-*.webp"):  # a baked texture of an earlier export, no longer made
        if stale.stem not in textures and re.search(r"-(diffuse|emissive|alphamasks)$", stale.stem):
            stale.unlink()
    ui.info(f"{len(files)} planets: " + ", ".join(files))
    return index
