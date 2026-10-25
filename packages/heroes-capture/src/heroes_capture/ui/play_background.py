"""The game's Play screen background, exported for a browser to draw (heroes-replay-stats draws it
with WebGL): its sky, the energy drifting in towards the horizon, the twinkling stars and the
planets; not the floor.

Everything comes from the game's data:
  - the cutscene the Play screen runs (gluebackground.stormcutscene): its camera, the model its
    actor shows, the light active while it holds the Play screen (ScreenPlay.HeroSelect), the animation
    it plays there (Stand);
  - that model (Storm_UI_HeroSelection_Floor's .m3): the sky's meshes placed by the Stand pose, their
    materials' layers (with the Stand values of animated ones, and the steady scrolls of the
    sequences that run alongside every other, GLstand), and the twinkling stars' particle system;
  - the light's HDR exposure and white point.

    <out>/scene.json   camera, tone, meshes, materials, particles, textures, and the sky's overlays
                       (play-scene.ts in the app)
    <out>/<texture>.webp  the textures the layers use: lossless where a channel is a mask, else lossy
"""

import io
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from heroes_capture.common import ui
from heroes_capture.common.casclib import Storage
from heroes_capture.common.game_data import light_sets
from heroes_capture.common.lighting import light_hdr
from heroes_capture.common.m3 import BLEND_MODES, CHANNELS, M3, Track, UNSHADED, VERTEX_ALPHA, VERTEX_COLOR

CUTSCENE = "gluebackground.stormcutscene"
HOLD = "ScreenPlay.HeroSelect Start"  # the bookmark where the cutscene holds the Play screen, looping
# The model's parts that make the sky, by the bone each hangs on: the sky's cylinder (the nebula and
# two layers of stars) and the funnel of energy around the view (HorizonEnergy).
SKY_BONES = ("Sky_Cylinder_Stars", "Cylinder001")
# The sky's parts set apart as overlays the app's sky wears (and may take off, or wear on another
# sky), by the overlay's id: its name, and the bones of the parts it is made of. The sky wears them
# all as the game does, so it is drawn as it was.
SKY_OVERLAYS = {"horizon-energy": ("Horizon energy", ("Cylinder001",))}
# The planets, by the bones their meshes hang on: the big one (OrangePlanet), and the blue one and
# its moon with each one's atmosphere (Dummy001 to Dummy003).
PLANET_BONES = ("OrangePlanet", "Dummy001", "Dummy002", "Dummy003")
# A planet's atmosphere material, named for the planet's size (large, medium, small): the name the
# scene gives each planet's meshes, for the app to tint each planet on its own.
ATMOSPHERE = re.compile(r"AtmospherePlanet_(\w+)")
LAYER_COLOR = 0x400  # a layer that is a colour of its own (its `color`), with no texture
LAYER_WRAP_X, LAYER_WRAP_Y = 0x4, 0x8  # a layer's texture repeats across u, and across v (else clamps)
EMISSIVE_BLENDS = {0: "mod", 1: "mod2x", 2: "add"}
PARTICLE_BONES = ("TwinkleStars",)
WEBP_QUALITY = 90
PARTICLE_VERTEX_ALPHA = 0x200000
MESH_EMISSION = 7  # a particle system's emission area: the mesh regions it names


def cutscene(storage: Storage) -> dict:
    """What the Play screen's cutscene says: {camera: its attributes, model: the actor's model id,
    light: the light active at the hold, animation: what the actor plays there}."""
    hits = [n for n in storage.find(f"*\\cutscenes\\{CUTSCENE}") if "\\base.stormdata\\" in n.lower()]
    if not hits:
        raise SystemExit(f"the game's data has no {CUTSCENE}")
    root = ET.fromstring((storage.read(hits[0]) or b"").decode("utf-8-sig"))
    bookmarks = {b.get("bookmarkName"): int(b.get("start", "0")) for b in root.iter("CCutsceneElementBookmark") if b.get("bookmarkName")}
    hold = bookmarks[HOLD]
    camera = next(root.iter("CCutsceneNodeCamera")).attrib
    actor = next(root.iter("CCutsceneNodeActor"))

    def active(elements, value: str) -> str:
        """The value of the element in effect at the hold: the last to start at or before it."""
        started = [(int(e.get("start", "0")), e.get(value)) for e in elements if int(e.get("start", "0")) <= hold]
        return max(started, key=lambda s: s[0])[1]

    lights = next(n for n in root.iter("CCutsceneNodeActiveLight") if not n.get("filter"))
    return {"camera": camera, "model": actor.get("modelLink"),
            "light": active(lights.iter("CCutsceneElementActiveLight"), "lightID"),
            "animation": active(actor.iter("CCutsceneElementAnim"), "anim")}


def camera(attrs: dict) -> dict:
    """The cutscene camera's eye and direction, as its node sets them (keyed variations for a hero's
    spotlight aside: with these, the planets of a screenshot of the Play screen land within 0.5% of
    the screen of where this camera puts them): it looks at its position from `distance` away,
    turned by `yaw` (degrees; -180 looks along +y, the model's front), tilted by `pitch` (negative
    down), the eye raised by `heightOffset`."""
    target = [float(v) for v in attrs["position"].split(",")]
    yaw, pitch = math.radians(float(attrs["yaw"])), math.radians(float(attrs["pitch"]))
    forward = [-math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch)]
    distance, height = float(attrs["distance"]), float(attrs["heightOffset"])
    eye = [target[i] - forward[i] * distance for i in range(3)]
    eye[2] += height
    return {"eye": [round(v, 4) for v in eye], "forward": [round(v, 6) for v in forward], "up": [0, 0, 1],
            "fovY": float(attrs["fov"]), "near": float(attrs["nearClip"]), "far": float(attrs["farClip"])}


def model_file(storage: Storage, model_id: str) -> bytes:
    """A CModel's .m3 file, by its id: its Model field, in the game's GameData, or the first of its
    parents' (a doodad's parent Doodad says Assets\\Doodads\\##id##\\##id##.m3), ##id## being the
    model's own id."""
    catalogs = [(storage.read(n) or b"").decode("utf-8-sig", errors="replace") for n in storage.find("*\\gamedata\\*modeldata.xml")]

    def entry(id_: str):
        for text in catalogs:
            found = re.search(rf'<CModel\b([^>]*)\bid="{re.escape(id_)}"([^>]*?)(/>|>(.*?)</CModel>)', text, re.S)
            if found:
                return found
        return None

    seen: set[str] = set()
    current: str | None = model_id
    while current and current not in seen:
        seen.add(current)
        found = entry(current)
        if not found:
            break
        value = re.search(r'<Model value="([^"]+)"', found.group(4) or "")
        if value:
            path = value.group(1).replace("##id##", model_id)
            hits = storage.find("*\\" + path.lower().replace("/", "\\"))
            if hits:
                return storage.read(hits[0]) or b""
            break
        parent = re.search(r'\bparent="([^"]+)"', (found.group(1) or "") + (found.group(2) or ""))
        current = parent.group(1) if parent else None
    raise SystemExit(f"the game's data has no model file for {model_id}")


class Export:
    """The scene being built: its materials and the textures they use, by key."""

    def __init__(self, m3: M3, tracks: dict[int, Track]):
        self.m3, self.tracks = m3, tracks
        self.materials: dict[str, dict] = {}
        self.textures: dict[str, str] = {}  # key -> the texture's file name
        self.sources: dict[str, str] = {}  # key -> the texture's path in the game data
        self.baked_from: dict[str, str] = {}  # a baked texture's key -> the key of the texture it was baked from
        self.alpha_read: set[str] = set()  # the textures a layer reads the alpha of
        self.keys: dict[tuple[int, int], str] = {}  # material record (its model, its offset in the file) -> its key

    def value(self, animated, at: int = 0) -> tuple:
        """An animated value as the played sequences have it (at the start), else its initial one."""
        track = self.tracks.get(animated.id)
        return track.at(at) if track else animated.value

    def layer(self, ref) -> dict | None:
        records = self.m3.records(ref)
        if not records:
            return None
        layer = records[0]
        if not layer.text("image"):
            if not layer["flags"] & LAYER_COLOR:
                return None
            b, g, r, a = self.value(layer["color"])
            return {"color": [r, g, b, a], "brightMult": round(self.value(layer["brightMult"])[0], 5), **self.fresnel(layer)}
        source = layer.text("image").replace("\\", "/")
        image = source.rsplit("/", 1)[-1]
        key = re.sub(r"[^a-z0-9]+", "-", image.lower().rsplit(".", 1)[0]).strip("-")
        self.textures[key] = image
        self.sources[key] = source
        channel = CHANNELS[layer["channels"]]
        if channel in ("a", "rgba"):
            self.alpha_read.add(key)
        offset = self.value(layer["uvOffset"])
        out = {"texture": key, "channel": channel,
               "brightMult": round(self.value(layer["brightMult"])[0], 5), "midtone": round(self.value(layer["midtoneOffset"])[0], 5),
               "offset": [round(v, 5) for v in offset], "angle": round(self.value(layer["uvAngle"])[2], 5),
               "tiling": [round(v, 5) for v in self.value(layer["uvTiling"])],
               "wrap": [bool(layer["flags"] & LAYER_WRAP_X), bool(layer["flags"] & LAYER_WRAP_Y)]}
        if layer["uvSource"] == 1:
            out["uvSet"] = 2  # the mesh's second texture coordinates
        out.update(self.fresnel(layer))
        track = self.tracks.get(layer["uvOffset"].id)
        if track and track.keys[0] != track.keys[-1]:
            # A steady scroll through the sequence's length, looping (the GLstand ones: 200 s).
            out["scroll"] = {"seconds": (track.frames[-1] - track.frames[0]) / 1000,
                             "by": [round(b - a, 5) for a, b in zip(track.keys[0], track.keys[-1])]}
        return out

    @staticmethod
    def fresnel(layer) -> dict:
        """A layer's fresnel, if it has one (a colour-only layer may: the flyby worlds' rim glow).
        Stronger where the surface turns away from the camera: from `min` facing it to `max`
        edge-on, the view turned by `yaw` and `pitch` (radians); type 2 inverts it."""
        if not layer["fresnelType"]:
            return {}
        return {"fresnel": {"type": layer["fresnelType"], "exponent": round(layer["fresnelExponent"], 5),
                            "min": round(layer["fresnelMin"], 5),
                            "max": round(layer["fresnelMin"] + layer["fresnelMaxOffset"], 5),
                            "yaw": round(layer["fresnelYaw"], 5), "pitch": round(layer["fresnelPitch"], 5)}}

    def material(self, record) -> str:
        if (id(self.m3), record.at) in self.keys:
            return self.keys[(id(self.m3), record.at)]
        name = record.text("name")
        key = base = re.sub(r"[^a-zA-Z0-9]+", "", name[:1].lower() + name[1:])
        # Names can differ only in characters a key drops ("Material #6~DUP", "Material #6~DUP\x04~").
        for n in range(2, 100):
            if key not in self.materials:
                break
            key = f"{base}{n}"
        self.keys[(id(self.m3), record.at)] = key
        if key not in self.materials:
            flags = record["flags"]
            masks = [self.layer(record[k]) for k in ("alphaMaskLayer", "alphaMask2Layer")]
            self.materials[key] = {
                "blend": BLEND_MODES[record["blendMode"]], "unshaded": bool(flags & UNSHADED),
                "vertexAlpha": bool(flags & VERTEX_ALPHA), "vertexColor": bool(flags & VERTEX_COLOR),
                "emissiveMult": round(record["emisMult"], 5), "priority": record["priority"],
                "emissiveBlend": EMISSIVE_BLENDS.get(record["emissiveBlend"], "add"),
                "diffuse": self.layer(record["diffuseLayer"]), "emissive": self.layer(record["emissiveLayer"]),
                "alphaMasks": [m for m in masks if m]}
            if record["blendMode"] == 0 and record["cutoutThreshold"]:
                # Opaque, but cut out where the alpha is below this.
                self.materials[key]["cutout"] = round(record["cutoutThreshold"] / 255, 4)
            specular = None if flags & UNSHADED else self.layer(record["specularLayer"])
            if specular:
                # A lit material's shine: where its specular layer says, the lights' specular colour
                # times its multiplier, as tight as its specularity (the highlight's power).
                self.materials[key].update({"specular": specular, "specularity": round(record["specularity"], 5),
                                            "specularMult": round(record["specMult"], 5)})
        return key

    def layers_of(self, materials: list[str]) -> list[dict]:
        found = []
        for key in materials:
            material = self.materials[key]
            found += [layer for layer in (material["diffuse"], material["emissive"], material.get("specular"), *material["alphaMasks"])
                      if layer]
        return found

    def uses_second_uvs(self, materials: list[str]) -> bool:
        return any(layer.get("uvSet") == 2 for layer in self.layers_of(materials))

    def uses_normals(self, materials: list[str]) -> bool:
        return any("fresnel" in layer for layer in self.layers_of(materials))


def mesh(m3: M3, region, world, materials: list[str], second_uvs: bool = False, normals: bool = False) -> dict:
    data = m3.mesh(region, world)
    out = {"positions": [round(float(c), 3) for p in data["positions"] for c in p],
           "uvs": [round(c, 5) for uv in data["uvs"] for c in uv],
           "colors": [c for color in data["colors"] for c in color],
           "indices": list(data["indices"]), "materials": materials}
    if second_uvs:
        out["uvs2"] = [round(c, 5) for uv in data["uvs2"] for c in uv]
    if normals:
        out["normals"] = [round(float(c), 3) for n in data["normals"] for c in n]
    return out


def particles(m3: M3, export: Export, record, emitter: str) -> dict:
    v = lambda field: export.value(record[field])
    rgba = lambda field: [v(field)[k] for k in (2, 1, 0, 3)]  # stored blue, green, red, alpha
    sizes = lambda field: [round(s, 5) for s in v(field)]  # start, middle, end
    return {"emitter": emitter, "material": export.material(m3.material(record["materialReference"])[0]),
            "rate": v("rate")[0], "max": record["max"],
            "lifespan": [v("lifespan1")[0], v("lifespan2")[0]], "speed": [v("speed1")[0], v("speed2")[0]],
            "sizes": [sizes("sizes1"), sizes("sizes2")],
            "colors": [[rgba("start1"), rgba("middle1"), rgba("end1")], [rgba("start2"), rgba("middle2"), rgba("end2")]],
            **{f"{k}{part}": round(record[f"{k}{part}"], 5) for k in ("size", "color", "alpha") for part in ("Middle", "Hold")},
            "vertexAlpha": bool(record["flags"] & PARTICLE_VERTEX_ALPHA)}


def texture_image(storage: Storage, name: str) -> Image.Image:
    """A texture from the game's data, as RGBA."""
    hits = [n for n in storage.find(f"*\\assets\\textures\\{name.lower()}") if re.search(r"\\(base|enus)\.stormassets\\", n, re.I)]
    if not hits:
        raise SystemExit(f"the game's data has no texture {name}")
    image = Image.open(io.BytesIO(storage.read(hits[0]) or b""))
    image.load()
    return image.convert("RGBA")


def texture_files(storage: Storage, names: dict[str, str], alpha_read: set[str], out: Path,
                  baked: dict[str, tuple[Image.Image, bool]] | None = None,
                  sources: dict[str, str] | None = None, baked_from: dict[str, str] | None = None) -> dict:
    """The textures as WebP: lossless where a layer reads the texture's alpha (the stars' mask is in
    it), else without the alpha, lossy at WEBP_QUALITY. `baked` images (key -> image, whether
    lossless) are written beside them. Each says where it came from in the game data (`source`,
    from `sources` by key; a baked one its original's, by `baked_from`, and `baked`)."""
    found = {}
    images = {}
    for key, name in names.items():
        image = texture_image(storage, name)
        images[key] = (image, key in alpha_read and image.getchannel("A").getextrema()[0] < 255)
    images.update(baked or {})
    for key, (image, lossless) in sorted(images.items()):
        file = f"{key}.webp"
        if lossless and image.getchannel("A").getextrema()[0] < 255:
            image.save(out / file, "WEBP", lossless=True, method=6, exact=True)
        elif lossless:
            image.convert("RGB").save(out / file, "WEBP", lossless=True, method=6)
        else:
            image.convert("RGB").save(out / file, "WEBP", quality=WEBP_QUALITY, method=6)
        found[key] = {"file": file, "width": image.width, "height": image.height}
        original = (baked_from or {}).get(key, key)
        if sources and original in sources:
            found[key]["source"] = sources[original]
        if key in (baked or {}):
            found[key]["baked"] = True
    return found


def sky_overlay(label: str, bones: tuple[str, ...], meshes: dict, materials: dict, textures: dict) -> dict:
    """A sky overlay of the sky's parts on `bones`, taken out of the sky's own meshes and materials:
    {label, parts: [{id, shape: {kind: mesh, the mesh as the sky's are (world space: the game's
    camera's)}, materials}], materials, textures} (the textures it reads, the scene's files)."""
    parts = []
    for bone in bones:
        found = meshes.pop(bone)
        names = found.pop("materials")
        parts.append({"id": bone, "shape": {"kind": "mesh", **found}, "materials": names})
    own = {name: materials.pop(name) for part in parts for name in part["materials"] if name in materials}
    read = {layer["texture"] for m in own.values()
            for layer in [m.get("diffuse"), m.get("emissive"), *m.get("alphaMasks", [])]
            if layer and layer.get("texture")}
    return {"label": label, "parts": parts, "materials": own,
            "textures": {key: texture for key, texture in textures.items() if key in read}}


def export(storage: Storage, out: Path) -> dict:
    """Export the Play screen's sky into `out` (see the module's description); the scene."""
    scene_info = cutscene(storage)
    m3 = M3(model_file(storage, scene_info["model"]))
    groups = m3.groups()
    played = list(groups[scene_info["animation"]])
    # The sequences that run alongside whatever else plays (every collection of theirs concurrent).
    played += [c for name, stcs in groups.items() if stcs and all(c["concurrent"] for c in stcs) for c in stcs]
    tracks: dict[int, Track] = {}
    for collection in played:
        tracks.update(m3.tracks(collection))
    view = camera(scene_info["camera"])
    world = m3.pose(tracks, eye=view["eye"])  # the planets' billboards face this camera
    ex = Export(m3, tracks)
    bone_records = m3.bones()
    bones = [b.text("name") for b in bone_records]
    region_materials = m3.region_materials()
    meshes = {}
    planet_of: dict[str, str] = {}  # mesh -> the planet's root bone
    sizes: dict[str, str] = {}  # a planet's root bone -> its size, as its atmosphere's material names it
    for index, region in enumerate(m3.regions()):
        bone_index = m3.region_bone(region)
        bone = bones[bone_index]
        if bone in SKY_BONES or bone in PLANET_BONES:
            records = [r for ref in region_materials.get(index, []) for r in m3.material(ref)]
            materials = [ex.material(r) for r in records]
            key = bone if bone not in meshes else f"{bone}-{index}"
            meshes[key] = mesh(m3, region, world, materials, ex.uses_second_uvs(materials), ex.uses_normals(materials))
            if bone in PLANET_BONES:
                while bone_records[bone_index]["parent"] >= 0:
                    bone_index = bone_records[bone_index]["parent"]
                planet_of[key] = bones[bone_index]
                for record in records:
                    if size := ATMOSPHERE.fullmatch(record.text("name")):
                        sizes[bones[bone_index]] = size.group(1).lower()
    for key, root in planet_of.items():
        meshes[key]["planet"] = sizes[root]
    systems = []
    for record in m3.model.records("particles"):
        if bones[record["bone"]] not in PARTICLE_BONES:
            continue
        if record["areaType"] != MESH_EMISSION:
            raise SystemExit(f"particles on {bones[record['bone']]}: emission area {record['areaType']}, not a mesh")
        regions = m3.numbers(record["emissionRegions"], "I")
        emitter = f"{bones[record['bone']]}Emitter"
        meshes[emitter] = mesh(m3, m3.regions()[regions[0]], world, [])
        systems.append(particles(m3, ex, record, emitter))
    tone = light_hdr(light_sets(storage)["lights"], scene_info["light"])
    if tone is None:
        raise SystemExit(f"the light {scene_info['light']} has no HDR exposure and white point")
    out.mkdir(parents=True, exist_ok=True)
    textures = texture_files(storage, ex.textures, ex.alpha_read, out, sources=ex.sources)
    materials = dict(ex.materials)
    overlays = {}
    for overlay_id, (label, overlay_bones) in SKY_OVERLAYS.items():
        overlays[overlay_id] = sky_overlay(label, overlay_bones, meshes, materials, textures)
    scene = {"source": {"build": storage.build(), "cutscene": CUTSCENE, "model": scene_info["model"],
                        "animation": scene_info["animation"], "light": scene_info["light"]},
             "camera": view, "tone": tone, "meshes": meshes,
             "materials": materials, "particles": systems, "textures": textures,
             "overlays": overlays, "wears": list(overlays)}
    written = {"scene.json"} | {t["file"] for t in scene["textures"].values()}
    for stale in out.glob("*.webp"):
        if stale.name not in written:
            stale.unlink()
    (out / "scene.json").write_text(json.dumps(scene, separators=(",", ":")), encoding="utf-8", newline="\n")
    ui.info(f"{len(meshes)} meshes, {len(materials)} materials, {len(systems)} particle systems, {len(scene['textures'])} textures,"
            f" {len(overlays)} sky overlays")
    return scene
