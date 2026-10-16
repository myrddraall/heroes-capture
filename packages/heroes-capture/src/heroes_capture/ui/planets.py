"""Planets for the Play screen background to put up in sets of its own (heroes-replay-stats
draws a new set, with a pan, as its pages change), exported for a browser to draw, each on its
own so the app loads only the ones a set uses.

Everything comes from the game's data:
  - the Play screen's own three planets (ui/play_background.py's model, the bones PLANET_BONES):
    each as a template in its own space, the big painted hemisphere with its hex grid and its
    atmosphere's disc, the size the app gives it;
  - the planets of the tutorial's flyby (its cutscene FLYBY_CUTSCENE flies its camera past the
    doodad FLYBY_MODEL): three worlds (Azeroth, Korhal, Sanctuary, named by their name plates) with
    their own meshes and layered materials, lit as the flyby lights them, and nine painted spheres
    (their textures on the Play screen's hemisphere, which is smooth where theirs are not), each
    seen from where the flyby shows it best (largest while wholly in its camera's view: the end of
    the approach to it), under the light the cutscene has on then (the cutscene switches lights as
    the camera passes each world, so each is seen under the light of its approach), so lit as the
    tutorial shows it;
  - each flyby planet also wears the Play screen's atmosphere disc and hex grid, which the game
    never puts on them, so they belong in the Play screen's sky.

A planet's file has its meshes in its own space, the body a unit sphere (or hemisphere) at the
origin facing -Y (the app turns it to face its camera, as the game's billboards do), its
materials, its textures (in the scene's directory) and its light (ambient, directional lights
along their directions in that space, and the flyby's exposure, which the app scales by).

    <out>/planets/index.json   the planets: id, kind (play, world, painted); and the Play screen's
                               own set: each template where the game puts it and its size
    <out>/planets/<id>.json    radius (1), meshes, materials, textures, light?; a painted sphere
                               has its template's meshes instead (template, and the body's materials)
"""

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from heroes_capture.common import ui
from heroes_capture.common.casclib import Storage
from heroes_capture.common.game_data import light_sets
from heroes_capture.common.m3 import M3
from heroes_capture.ui import play_background as play
from heroes_capture.ui.play_background import Export, mesh, model_file, texture_files

FLYBY_MODEL = "Storm_Doodad_Nexus_PlanetsFlyby"
FLYBY_CUTSCENE = "tutorial_cutscene_planetflyby.stormcutscene"
FLYBY_ANIMATION = "Stand"
CAMERA_BONE = "SC2Camera001"  # the flyby's camera: it looks along its -Z, +Y up
# Half the flyby camera's field of view, for what is wholly in its view: its own isn't read (the
# model's camera record), and the Play screen's is 43 degrees.
HALF_FOV = math.radians(20)
# A world's bones hang on a master; its name plate names it.
MASTER = re.compile(r"(\w+)_Master$")
NAMEPLATE = re.compile(r"^NamePlate_(\w+?)_\d+$")
# The painted spheres' bones, and their textures' numbering.
PAINTED = re.compile(r"^Planet\d+$")
PAINTED_TEXTURE = re.compile(r"planetsflyby-planet(\d+)$")
# A world's mesh with more vertices than this is a detail too heavy for a background (Korhal's
# space station: 9000 vertices for a speck).
MAX_MESH_VERTICES = 4000
# The Play screen's planets, by their root bones: the large one's hemisphere is every painted
# sphere's body, the large one's atmosphere every flyby planet's.
PLAY_TEMPLATES = {"play-large": "Dummy001", "play-medium": "Dummy002", "play-small": "Dummy003"}
PLAY_BODY_BONE = "OrangePlanet"
SAMPLE_MS = 100


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


def light_in(table: dict, light_id: str, frame: np.ndarray) -> dict:
    """A light as the app lights with it, its directions turned into a planet's frame: {ambient,
    directional: [{direction, color}] (colour times multiplier), exposure}. Missing fields come
    from the light's parents."""
    from heroes_capture.common.lighting import _inherited

    directional = _inherited(table, light_id, "directional") or []
    exposure = _inherited(table, light_id, "hdrExposure")
    if exposure is None:
        raise SystemExit(f"the light {light_id} has no HDR exposure")
    return {"ambient": [round(c, 5) for c in (_inherited(table, light_id, "ambient") or [0, 0, 0])],
            "directional": [{"direction": [round(float(v), 5) for v in frame @ np.array(d["direction"])],
                             "color": [round(c * d["multiplier"], 5) for c in d["color"]]}
                            for d in directional if any(d["direction"])],
            "exposure": exposure}


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


def radius_of(positions: list[float]) -> float:
    p = np.array(positions).reshape(-1, 3)
    return float(np.linalg.norm(p, axis=1).max())


class Planet:
    """A planet's file as it is built: its meshes in its own space, with the materials and textures
    they use (the export's, shared)."""

    def __init__(self, export: Export, kind: str):
        self.export, self.kind = export, kind
        self.meshes: dict[str, dict] = {}
        self.light: dict | None = None
        # A painted sphere: its template's meshes, the body's materials these.
        self.template: tuple[str, list[str]] | None = None
        # Where the game puts it (a template): its position, and its body's radius there.
        self.placed: tuple[list[float], float] | None = None

    def add(self, m3: M3, region, world: list[np.ndarray], into: np.ndarray, name: str, materials: list) -> dict:
        ex = self.export
        keys = [ex.material(r) for r in materials]
        placed = [into @ w for w in world]
        self.meshes[name] = mesh(m3, region, placed, keys, ex.uses_second_uvs(keys), normals=True)
        return self.meshes[name]

    def file(self, templates: dict[str, "Planet"]) -> dict:
        if self.template:
            name, body = self.template
            used = set(body) | {key for m in templates[name].meshes.values() for key in m["materials"]}
        else:
            used = {key for m in self.meshes.values() for key in m["materials"]}
        materials = {key: self.export.materials[key] for key in sorted(used)}
        textures = {layer["texture"] for m in materials.values()
                    for layer in (m["diffuse"], m["emissive"], *m["alphaMasks"]) if layer and "texture" in layer}
        out: dict = {"kind": self.kind, "radius": 1}
        if self.template:
            out["template"], out["body"] = self.template
        else:
            out["meshes"] = self.meshes
        out.update({"materials": materials, "textures": sorted(textures)})
        if self.light:
            out["light"] = self.light
        return out


def play_templates(storage: Storage, export: Export) -> dict[str, Planet]:
    """The Play screen's planets as templates, each about its root bone, its body a unit radius
    (the large and medium ones a hemisphere towards -Y, the small one a sphere), as the model
    poses them without a camera to face (their billboards as modelled: facing -Y)."""
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
    templates = {}
    for key, root in PLAY_TEMPLATES.items():
        root_index = names.index(root)
        regions = [(i, r) for i, r in enumerate(m3.regions()) if root in chain(bones, names, m3.region_bone(r))]
        # The body: the opaque mesh (the painting); its radius is the unit.
        body = next((i, r) for i, r in regions
                    if any(m3.material(ref)[0]["blendMode"] == 0 for ref in region_materials.get(i, [])))
        into = frame_of(world[root_index], np.eye(3), 1.0)
        unit = radius_of(mesh(m3, body[1], [into @ w for w in world], [])["positions"])
        into = frame_of(world[root_index], np.eye(3), unit)
        planet = Planet(export, "play")
        planet.placed = ([round(float(v), 3) for v in world[root_index][:3, 3]], round(unit, 3))
        for i, region in regions:
            name = "body" if (i, region) == body else "atmosphere"
            materials = [r for ref in region_materials.get(i, []) for r in m3.material(ref)]
            planet.add(m3, region, world, into, name, materials)
        templates[key] = planet
    return templates


def flyby_planets(storage: Storage, export: Export, large: Planet) -> dict[str, Planet]:
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
            return radius_of(mesh(m3, region, [into @ w for w in world], [])["positions"]) / scale
        raise SystemExit(f"{names[bone]} is never there in the flyby")

    def best_view(bone: int, radius: float) -> tuple[int, np.ndarray]:
        """When the flyby shows a bone's body best: largest on screen while wholly in the camera's
        view (the end of the approach to it; failing that, nearest to straight ahead): the time,
        and the pose then."""
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
            if off + size <= HALF_FOV and (largest is None or size > largest[0]):
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
        ms, world = best_view(master, bone_radius(master, body[1]))
        into, _ = frame(master, ms, world, 1.0)
        unit = radius_of(mesh(m3, body[1], [into @ w for w in world], [])["positions"])
        into, turn = frame(master, ms, world, unit)
        planet = Planet(export, "world")
        for i, region in regions:
            if region["vertices"] > MAX_MESH_VERTICES:
                continue
            materials = [r for ref in region_materials.get(i, []) for r in m3.material(ref)]
            bone = names[m3.region_bone(region)]
            name = "body" if (i, region) == body else bone.replace("~", "").strip()
            added = planet.add(m3, region, world, into, name, materials)
            if (i, region) == body:
                added["materials"] = added["materials"] + large.meshes["body"]["materials"][1:2]  # the hex grid
        planet.meshes["atmosphere"] = large.meshes["atmosphere"]
        planet.light = light_in(lights, light_at(schedule, ms), turn)
        ui.info(f"{key}: as the flyby shows it at {ms / 1000:.1f} s, under {light_at(schedule, ms)}")
        planets[key] = planet
    # The painted spheres: their textures on the Play screen's hemisphere, lit as the flyby does.
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
        _, turn = frame(bone, ms, world, 1.0)
        ui.info(f"{key}: as the flyby shows it at {ms / 1000:.1f} s, under {light_at(schedule, ms)}")
        planet = Planet(export, "painted")
        planet.template = ("play-large", keys[:1] + large.meshes["body"]["materials"][1:2])  # its painting, the hex grid
        planet.light = light_in(lights, light_at(schedule, ms), turn)
        planets[key] = planet
    return planets


def export(storage: Storage, out: Path) -> dict:
    """Export the planets into `out`/planets (see the module's description); the index."""
    # One export's materials and textures serve every planet: the Play screen's model's first, then
    # the flyby's, their keys kept apart by name.
    ex = Export(None, {})  # type: ignore[arg-type]  # the models are set as each is read
    templates = play_templates(storage, ex)
    planets = dict(templates)
    planets.update(flyby_planets(storage, ex, templates["play-large"]))
    files = {key: planet.file(templates) for key, planet in planets.items()}
    folder = out / "planets"
    folder.mkdir(parents=True, exist_ok=True)
    textures = texture_files(storage, ex.textures, ex.alpha_read, out)
    for key, file in files.items():
        file["textures"] = {t: textures[t] for t in file["textures"]}
        (folder / f"{key}.json").write_text(json.dumps(file, separators=(",", ":")), encoding="utf-8", newline="\n")
    index = {"planets": [{"id": key, "kind": file["kind"]} for key, file in files.items()],
             "play": [{"id": key, "position": planet.placed[0], "radius": planet.placed[1]}
                      for key, planet in templates.items() if planet.placed]}
    (folder / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8", newline="\n")
    for stale in folder.glob("*.json"):
        if stale.stem not in files and stale.name != "index.json":
            stale.unlink()
    ui.info(f"{len(files)} planets: " + ", ".join(files))
    return index
