"""The Play screen planets' painting taken apart: an evenly lit surface, and lights that paint it
again, so a planet can be turned (and lit from anywhere) and still look as the game paints it.

The game paints each Play screen planet's light into its surface texture (Storm_UI_HeroSelection_
Floor_OrangePlanet, _BluePlanet) and draws it unshaded: the lit side is a bright patch on the far
side of the sphere (the game's camera sees the dark side and a lit edge), the rest black. Since the
texture's mapping onto its sphere is known (the planet file's), each texel's direction on the sphere
is too, and so:

  - the light: the direction its painting is brightest towards (the one that explains most of the
    texture's brightness as a function of how squarely a point faces it), and how its brightness
    falls off, fitted as the app's directional light and the Wrap and Drop off of the lit
    material under it (how every light on that material falls off: one shape, so one light),
    its colour neutral where the light is half on, so the planet's colours stay in its surface;
  - the surface: the texture with that light divided out where the texel holds detail (not dark,
    not blown out), the rest filled from patches of what is kept (patch_fill), in three charts
    that each have little distortion, so no pole pinches: the band about the equator in the
    texture's own layout, and each pole in a flat view about it, rebuilt there and put back.

A planet's surface material then reads the surface (lit, not unshaded), and its file has a light of
its own that shows it well (BASE_LIGHTS, at the Play screen's exposure). How the Play screen paints
it is its NEXUS variant: its own light off, the light its painting is fitted to (from its own
point of the texture: each planet maps it its own way round) on, and its surface material's Wrap
and Drop off as fitted, and the nexus overlay it wears (its shield) on: off unless it is; the Play
screen's set puts it on. Its SCENE_LIT variant has its own light off, for scenes whose lights light it. Two planets painted
with one texture (the medium and small ones) share one surface. The painted texture itself is
used no more.
"""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, map_coordinates
from scipy.optimize import nnls

from heroes_capture.common import ui
from heroes_capture.common.casclib import Storage
from heroes_capture.common.game_data import cache_dir
from heroes_capture.ui.patch_fill import fill
from heroes_capture.ui.play_background import Export, texture_image

# Bumped whenever a surface would come out otherwise: the cache's key.
GENERATOR = 6
# The light's brightness as a function of how squarely a point faces it: in this many steps.
BINS = 64
# Directions tried for the light, before narrowing down about the best.
DIRECTIONS = 4000
# How a light reaches the app's lit material: ((facing + wrap) / (1 + wrap)) to the power of its
# drop off, between 0 and 1, the material's Wrap and Drop off (every light on it alike). Fitted
# over these (the editor's ranges).
WRAPS = np.linspace(0.0, 1.0, 11)
SOFTNESSES = np.geomspace(0.5, 24.0, 28)
# The light is fitted to the falloff by its relative error (each step weighted by 1 over its
# brightness, floored at this share of the brightest): its dim tail, all the game's camera sees of
# the painting (its lit edge), counts as much as its bright side.
RELATIVE_FLOOR = 0.002
# The side the game's camera sees (the planet's frame: the eye along -y) counts this many times as
# much in the fit as the side away from it, by how squarely it faces the eye: the painting is not
# all one light (its penumbra on that side fades slower and warmer), and that side is what the
# game shows.
SEEN_WEIGHT = 30.0
EYE = np.array([0.0, -1.0, 0.0])
# A texel holds detail where its stored brightness (0 to 1, as stored) is past the first (fully
# from the second on), and none where any channel is past BLOWN (white: its brightness is lost).
DETAIL = (0.05, 0.12)
BLOWN = 0.97
# The surface: its brightest texels (the 99th percentile's brightest channel) at this, the light
# carrying the rest.
SURFACE_TOP = 0.85
# The charts: the band about the equator (degrees from the pole) that is filled in the texture's
# own layout, and the band its source patches come from; each pole's view out to CAP, rebuilt
# inside CAP_KEPT (the band's kept beyond, fading in over CAP_FADE), put back inside CAP_BLEND.
BAND = (40.0, 140.0)
BAND_SOURCE = (35.0, 145.0)
CAP, CAP_KEPT, CAP_FADE, CAP_BLEND = 52.0, 44.0, 4.0, (45.0, 48.0)

LUMA = np.array([0.2126, 0.7152, 0.0722])
# A Play screen planet's own light, shown on its own (well lit, all of it to see): a key from the
# upper left, in front, a cool fill from the right and a little ambient; each from where it comes
# (the planet's frame: x right, y away from the eye, z up), at the Play screen's exposure.
BASE_AMBIENT = [0.05, 0.05, 0.06]
BASE_LIGHTS = [{"from": [-0.55, -0.45, 0.7], "color": [1.5, 1.42, 1.3]},
               {"from": [0.85, -0.35, -0.4], "color": [0.25, 0.3, 0.4]}]
# Its variants, by id and name: lit by the scene alone (its own lights off), and lit as the Play
# screen paints it (its own lights off, the light its painting is fitted to on, its surface
# material falling off as fitted): the Play screen's set puts it on, with the nexus overlay.
SCENE_LIT, SCENE_LIT_NAME = "scene-lighting", "Scene lighting"
NEXUS, NEXUS_NAME = "nexus", "Nexus"
PLAY_LIGHT_ID, PLAY_LIGHT_NAME = "added-play-screen", "Play screen light"


class Mapping:
    """A sphere mapping (a planet file's `mapping`, kind sphere) through a layer's transform (its
    offset, tiling and angle): the texture coordinates of a direction on the sphere, and back, as
    the app's body shader has them."""

    def __init__(self, mapping: dict, layer: dict):
        if mapping["kind"] != "sphere":
            raise ValueError(f"a {mapping['kind']} mapping, not a sphere's")
        self.pole = np.array(mapping["pole"], float)
        self.pole /= np.linalg.norm(self.pole)
        meridian = np.array(mapping["meridian"], float)
        meridian -= self.pole * (meridian @ self.pole)
        self.meridian = meridian / np.linalg.norm(meridian)
        self.side = np.cross(self.pole, self.meridian)
        self.v = np.array(mapping["v"], float)
        self.polar = np.linspace(0, math.pi, len(self.v))
        self.u0, self.flip = mapping["u0"], mapping["flip"]
        self.offset = np.array(layer.get("offset", [0, 0]), float)
        self.tiling = np.array(layer.get("tiling", [1, 1]), float)
        self.angle = math.radians(layer.get("angle", 0.0))
        self.key = json.dumps([mapping, {k: layer.get(k) for k in ("offset", "tiling", "angle")}], sort_keys=True)

    def _to_texture(self, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x, y = (u - 0.5 - self.offset[0]) * self.tiling[0], (v - 0.5 - self.offset[1]) * self.tiling[1]
        c, s = math.cos(self.angle), math.sin(self.angle)
        return c * x - s * y + 0.5, s * x + c * y + 0.5

    def _from_texture(self, s_: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c, s = math.cos(self.angle), math.sin(self.angle)
        x, y = s_ - 0.5, t - 0.5
        x, y = c * x + s * y, -s * x + c * y
        return x / self.tiling[0] + 0.5 + self.offset[0], y / self.tiling[1] + 0.5 + self.offset[1]

    def direction_at(self, s_: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The direction on the sphere of texture coordinates, its angle from the pole, and
        whether the mapping shows it at all (past the mapping's last v it does not)."""
        u, v = self._from_texture(np.asarray(s_, float), np.asarray(t, float))
        polar = np.interp(v, self.v, self.polar)
        az = 2 * math.pi * ((-(u - self.u0) if self.flip else (u - self.u0)) % 1.0)
        n = (np.cos(polar)[..., None] * self.pole
             + np.sin(polar)[..., None] * (np.cos(az)[..., None] * self.meridian + np.sin(az)[..., None] * self.side))
        return n, polar, (v >= self.v[0]) & (v <= self.v[-1])

    def directions(self, height: int, width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Each texel's direction (see `direction_at`)."""
        return self.direction_at(*np.meshgrid((np.arange(width) + 0.5) / width, (np.arange(height) + 0.5) / height))

    def coordinates(self, n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The texture coordinates of directions on the sphere."""
        polar = np.arccos(np.clip(n @ self.pole, -1, 1))
        az = np.arctan2(n @ self.side, n @ self.meridian) / (2 * math.pi)
        u = (self.u0 + (-az if self.flip else az)) % 1.0
        s_, t = self._to_texture(u, np.interp(polar, self.polar, self.v))
        return s_ % 1.0, t


def _sample(image: np.ndarray, s_: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Bilinear, round the sides, held at the top and bottom."""
    x = s_ * image.shape[1] - 0.5
    y = np.clip(t * image.shape[0] - 0.5, 0, image.shape[0] - 1)
    return np.stack([map_coordinates(image[..., c], [y, x], order=1, mode="grid-wrap") for c in range(image.shape[2])], -1)


# --- The light ------------------------------------------------------------------------------


def light_direction(brightness: np.ndarray, n: np.ndarray, weight: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """The direction towards the light a texture is painted under: the one that explains most of
    its brightness as a function of how squarely each point faces it (least spread left about
    that function), from DIRECTIONS spread round the sphere, then narrowed down about the best."""
    step = max(1, brightness.shape[1] // 512)
    ns, ys, ws = n[::step, ::step].reshape(-1, 3), brightness[::step, ::step].ravel(), weight[::step, ::step].ravel()

    def spread(light: np.ndarray) -> float:
        b = np.clip(((ns @ light + 1) / 2 * BINS).astype(int), 0, BINS - 1)
        mean = np.bincount(b, ws * ys, BINS) / np.maximum(np.bincount(b, ws, BINS), 1e-9)
        return float(np.sum(ws * (ys - mean[b]) ** 2))

    k = np.arange(DIRECTIONS) + 0.5
    polar, turn = np.arccos(1 - 2 * k / DIRECTIONS), math.pi * (1 + 5 ** 0.5) * k
    tried = np.stack([np.cos(turn) * np.sin(polar), np.sin(turn) * np.sin(polar), np.cos(polar)], -1)
    best = tried[int(np.argmin([spread(d) for d in tried]))]
    for radius in (0.05, 0.02, 0.008):
        near = best + rng.normal(0, radius, (300, 3))
        near = np.vstack([best, near / np.linalg.norm(near, axis=1, keepdims=True)])
        best = near[int(np.argmin([spread(d) for d in near]))]
    facing = ns @ best
    if np.average(ys, weights=ws * (facing > 0) + 1e-12) < np.average(ys, weights=ws * (facing < 0) + 1e-12):
        best = -best  # towards the bright side
    return best


def falloff(target: np.ndarray, facing: np.ndarray, weight: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The mean of `target` (per channel) by how squarely each point faces the light, in BINS
    steps: the steps' facings, means and weights."""
    b = np.clip(((facing + 1) / 2 * BINS).astype(int), 0, BINS - 1).ravel()
    w = weight.ravel()
    total = np.bincount(b, w, BINS)
    means = np.stack([np.bincount(b, w * target[..., c].ravel(), BINS) for c in range(3)], -1) / np.maximum(total, 1e-12)[:, None]
    return (np.arange(BINS) + 0.5) / BINS * 2 - 1, means, total


def reach(facing: np.ndarray, wrap: float, softness: float) -> np.ndarray:
    """How much of a directional light reaches a point facing it so squarely, on a lit material of
    that Wrap and Drop off (`softness`; the app's)."""
    return np.clip((facing + wrap) / (1 + wrap), 0, 1) ** softness


def fit_light(facings: np.ndarray, means: np.ndarray, weights: np.ndarray) -> dict:
    """The app's directional light (its colour per channel) and the Wrap and Drop off of the lit
    material under it ({color, wrap, softness}) that best give `means` at `facings`, by relative
    error (RELATIVE_FLOOR): every shape tried, its colour fitted by least squares (none negative).
    (One light: lights of one direction on one material, of one shape, add up to one.)"""
    luma = means @ LUMA
    weights = weights / (luma + RELATIVE_FLOOR * max(float(luma.max()), 1e-12))
    root = np.sqrt(weights)
    best: tuple[float, np.ndarray, float, float] | None = None
    for w in WRAPS:
        for s in SOFTNESSES:
            a = (reach(facings, w, s) * root)[:, None]
            colour, error = np.zeros(3), 0.0
            for ch in range(3):
                solved, residual = nnls(a, means[:, ch] * root)
                colour[ch], error = solved[0], error + residual ** 2
            if best is None or error < best[0]:
                best = (error, colour, float(w), float(s))
    _, colour, wrap, softness = best
    return {"color": colour, "wrap": wrap, "softness": softness}


def lit_by(facing: np.ndarray, light: dict) -> np.ndarray:
    """The light a light puts on points facing it so squarely (see `fit_light`)."""
    return reach(facing, light["wrap"], light["softness"])[..., None] * np.asarray(light["color"])


# --- The surface ----------------------------------------------------------------------------


def _detail(stored: np.ndarray, used: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How much each texel can be trusted for its detail (0 to 1: by its stored brightness), and
    where it is blown out."""
    blown = binary_dilation(stored.max(-1) > BLOWN, iterations=max(1, stored.shape[1] // 256))
    trust = np.clip((stored @ LUMA - DETAIL[0]) / (DETAIL[1] - DETAIL[0]), 0, 1) * used * ~blown
    return trust, blown


def _fit_weight(n: np.ndarray, area: np.ndarray) -> np.ndarray:
    """How much each texel counts in fitting the lights: its share of the sphere, the side the
    game's camera sees SEEN_WEIGHT times as much."""
    return area * (1 + SEEN_WEIGHT * np.clip(n @ EYE, 0, 1))


def make_surface(stored: np.ndarray, mapping: Mapping, rng: np.random.Generator) -> tuple[np.ndarray, dict]:
    """The evenly lit surface of a painted texture (`stored`: its RGB, 0 to 1 as stored; all of it
    as stored, as the app lights a texture's colours as they are stored, without decoding them,
    as the game does): the light it is painted under divided out where its texels hold detail,
    the rest filled, the poles rebuilt; and that light, as the texture has it ({at: the texture
    coordinates of the point it comes straight from, and the light and the Wrap and Drop off of the
    material under it: {color, wrap, softness})."""
    height, width = stored.shape[:2]
    n, polar, used = mapping.directions(height, width)
    area = np.sin(polar) * used
    light = light_direction(stored @ LUMA, n, area, rng)
    facing = n @ light
    trust, blown = _detail(stored, used)
    facings, means, weights = falloff(stored, facing, _fit_weight(n, area) * ~blown)
    light_fit = fit_light(facings, means, weights)
    # The light neutral where it is half on: the planet's colours are its surface's.
    lit_steps = lit_by(facings, light_fit)
    luma = lit_steps @ LUMA
    half = (luma > 0.25 * luma.max()) & (luma < 0.75 * luma.max())
    tint = (lit_steps[half] / luma[half][:, None]).mean(0) if half.any() else np.ones(3)
    light_fit["color"] = light_fit["color"] / tint
    lit = lit_by(facing, light_fit)
    surface = stored / np.maximum(lit, 1e-4)
    on = lit @ LUMA / max(float((lit @ LUMA).max()), 1e-9)
    trust = trust * (on > 0.02)
    source = (stored @ LUMA > min(DETAIL[1] + 0.02, float(np.percentile((stored @ LUMA)[used], 85)))) & (on > 0.05) & used & ~blown
    scale = SURFACE_TOP / max(float(np.percentile(surface[trust > 0.99].max(-1), 99)), 1e-6)
    surface *= scale
    light_fit["color"] = light_fit["color"] / scale
    at = mapping.coordinates(light[None])
    painting = {"at": [float(at[0][0]), float(at[1][0])], "color": [float(c) for c in light_fit["color"]],
                "wrap": light_fit["wrap"], "softness": light_fit["softness"]}
    # The band about the equator, filled from patches of the band a little wider (mirrored too).
    rows = polar[:, 0]
    band = np.flatnonzero((rows > math.radians(BAND[0])) & (rows < math.radians(BAND[1])))
    src = np.flatnonzero((rows > math.radians(BAND_SOURCE[0])) & (rows < math.radians(BAND_SOURCE[1])))
    s0, s1 = src[0], src[-1] + 1
    patches = np.concatenate([surface[s0:s1], surface[s0:s1, ::-1], surface[s0:s1][::-1], surface[s0:s1][::-1, ::-1]])
    usable = np.concatenate([source[s0:s1], source[s0:s1, ::-1], source[s0:s1][::-1], source[s0:s1][::-1, ::-1]])
    b0, b1 = band[0], band[-1] + 1
    ui.info(f"filling {width}x{height}: the band")
    filled = surface.copy()
    filled[b0:b1] = fill(surface[b0:b1] * (trust[b0:b1, :, None] > 0), trust[b0:b1], patches, usable, True, rng)
    # Each pole: in a flat view about it (as far from it everywhere as on the sphere), rebuilt
    # inside CAP_KEPT from the same patches, the band kept beyond; put back.
    per_radian = width / (2 * math.pi)
    size = int(2 * math.radians(CAP) * per_radian) // 2 * 2
    cy, cx = np.mgrid[0:size, 0:size] + 0.5
    cx, cy = (cx - size / 2) / per_radian, (cy - size / 2) / per_radian
    out_from, turn = np.hypot(cx, cy), np.arctan2(cy, cx)
    result = filled.copy()
    for sign in (1, -1):
        pole, across = mapping.pole * sign, mapping.side * sign
        ui.info(f"filling {width}x{height}: the {'north' if sign > 0 else 'south'} pole")
        view_n = (np.cos(out_from)[..., None] * pole
                  + np.sin(out_from)[..., None] * (np.cos(turn)[..., None] * mapping.meridian + np.sin(turn)[..., None] * across))
        kept = np.clip((out_from - math.radians(CAP_KEPT)) / math.radians(CAP_FADE), 0, 1) * (out_from < math.radians(CAP))
        view = fill(_sample(filled, *mapping.coordinates(view_n)), kept, patches, usable, False, rng)
        from_pole = np.arccos(np.clip(n @ pole, -1, 1))
        q = n - (n @ pole)[..., None] * pole
        a = np.arctan2(q @ across, q @ mapping.meridian)
        x = from_pole * np.cos(a) * per_radian + size / 2 - 0.5
        y = from_pole * np.sin(a) * per_radian + size / 2 - 0.5
        back = np.stack([map_coordinates(view[..., c], [y, x], order=1, mode="nearest") for c in range(3)], -1)
        w = np.clip((math.radians(CAP_BLEND[1]) - from_pole) / math.radians(CAP_BLEND[1] - CAP_BLEND[0]), 0, 1)[..., None]
        result = result * (1 - w) + back * w
    # Texels past the mapping's last v are never shown: the last shown row carried on.
    last = int(np.max(np.flatnonzero(used.any(1)))) + 1
    result[last:] = result[last - 1]
    return np.clip(result, 0, 1), painting


def planet_lights(painting: dict, mapping: Mapping) -> list[dict]:
    """A painting's light (see `make_surface`) as the planet file's directional lights ({direction:
    the way it travels, color}), for a planet mapping the texture so: from the point of its sphere
    the texture's light comes straight from, in the planet's frame."""
    towards = mapping.direction_at(*painting["at"])[0]
    towards = towards / np.linalg.norm(towards)
    return [{"direction": [round(float(-c), 5) for c in towards], "color": [round(float(c), 5) for c in painting["color"]]}]


def painting_surface(painting: dict) -> dict:
    """How the lit material under a painting's light takes it (see `make_surface`): its Wrap and
    Drop off, each where it isn't the app's own (0, 1)."""
    out: dict = {}
    if painting["wrap"]:
        out["wrap"] = round(painting["wrap"], 4)
    if painting["softness"] != 1:
        out["dropOff"] = round(painting["softness"], 4)
    return out


def _own_lights_off(file: dict) -> dict:
    """A planet's own lights switched off, by id (as the app names them): its ambient, each of its
    directional lights and its shade."""
    ids = ["ambient", *(f"directional-{i}" for i in range(len(file["light"]["directional"]))), "shadow"]
    return {i: {"enabled": False} for i in ids}


def _hex(rgb: list[float]) -> tuple[str, float]:
    """A colour (each channel 0 up) as the app's colour and strength: its brightest channel 1."""
    strength = max(max(rgb), 1e-9)
    return "#" + "".join(f"{round(min(c / strength, 1) * 255):02x}" for c in rgb), round(strength, 5)


def play_variants(file: dict, material_key: str, painting: dict, mapping: Mapping) -> list[dict]:
    """A Play screen planet's variants (see SCENE_LIT, NEXUS): lit by the scene alone, and lit as
    the Play screen paints it (the light its painting is fitted to, from where it comes, and its
    surface material's Wrap and Drop off as fitted), wearing the style it wears there."""
    off = _own_lights_off(file)
    style = file["tuning"]["style"]
    [painted] = planet_lights(painting, mapping)
    colour, strength = _hex(painted["color"])
    light = {"id": PLAY_LIGHT_ID, "kind": "directional", "name": PLAY_LIGHT_NAME, "colour": colour,
             "strength": strength, "from": [-c for c in painted["direction"]], "specularStrength": 0, "down": False}
    surface = painting_surface(painting)
    return [{"id": SCENE_LIT, "name": SCENE_LIT_NAME, "settings": {"lights": off}},
            {"id": NEXUS, "name": NEXUS_NAME, "settings": {"overlaysEnabled": {style: True}, "lights": off, "added": [light],
                                                          **({"materials": {material_key: surface}} if surface else {})}}]


def _cached_surface(stored: np.ndarray, mapping: Mapping, name: str) -> tuple[np.ndarray, dict]:
    """The surface of a painted texture and its light (see `make_surface`), from the cache or made
    (and kept there)."""
    key = hashlib.sha256(stored.tobytes() + str(stored.shape).encode() + mapping.key.encode() + f"{GENERATOR}".encode()).hexdigest()[:24]
    folder: Path = cache_dir() / "play-relight"
    image_file, light_file = folder / f"{key}.png", folder / f"{key}.json"
    if image_file.exists() and light_file.exists():
        return np.asarray(Image.open(image_file).convert("RGB"), dtype=np.float64) / 255, json.loads(light_file.read_text())
    with ui.step(f"Rebuilding {name} evenly lit (once: kept in the cache)"):
        surface, painting = make_surface(stored, mapping, np.random.default_rng(int(key[:8], 16)))
    folder.mkdir(parents=True, exist_ok=True)
    image = _surface_image(surface)
    image.save(image_file)
    light_file.write_text(json.dumps(painting), encoding="utf-8")
    return np.asarray(image, dtype=np.float64) / 255, painting


def _surface_image(surface: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(surface, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")


def surface_material(file: dict, export: Export) -> tuple[str, dict, dict]:
    """A Play screen planet's painted material: its key, the material, and its surface part."""
    surface = next(p for p in file["parts"] if p["role"] == "surface")
    for key in surface["materials"]:
        material = file["materials"][key]
        if material["blend"] == "opaque" and material["unshaded"] and (material["diffuse"] or {}).get("texture"):
            return key, material, surface
    raise SystemExit("a Play screen planet with no painted surface material")


def relight(storage: Storage, export: Export, files: dict[str, dict], baked: dict, exposure: float) -> None:
    """The Play screen planets' files (`files`, by key) with their painting taken apart: each one's
    surface material reading its texture's evenly lit surface (into `baked`, by key), lit, and
    the file's light the lights that paint it again, at the Play screen's `exposure`."""
    paintings: dict[str, dict] = {}
    for name, file in files.items():
        key, material, part = surface_material(file, export)
        texture = material["diffuse"]["texture"]
        stored = np.asarray(texture_image(storage, export.textures[texture]).convert("RGB"), dtype=np.float64) / 255
        mapping = Mapping(part["mapping"], material["diffuse"])
        surface_key = f"{texture}-base"
        if texture not in paintings:
            surface, paintings[texture] = _cached_surface(stored, mapping, name)
            baked[surface_key] = (_surface_image(surface), False)
            export.baked_from[surface_key] = texture
        file["materials"][key] = {**material, "unshaded": False, "diffuse": {**material["diffuse"], "texture": surface_key}}
        # (Its painted texture no longer its: only its surface, evenly lit.)
        file["textures"] = sorted((set(file["textures"]) - {texture}) | {surface_key})
        file["light"] = {"ambient": BASE_AMBIENT, "exposure": exposure, "directional": [
            {"direction": [round(-c / math.hypot(*one["from"]), 5) for c in one["from"]], "color": one["color"]}
            for one in BASE_LIGHTS]}
        file["variants"] = play_variants(file, key, paintings[texture], mapping)
        # (The style it wears off unless its NEXUS variant is on.)
        file["overlaysEnabled"] = {file["tuning"]["style"]: False}
        painted = paintings[texture]
        ui.info(f"{name}: its surface {surface_key}, Wrap {painted['wrap']:.2f}, Drop off {painted['softness']:.2f}")
