"""Model files (.m3: the "MD34" format the game shares with StarCraft II), read as far as the tool
needs them: meshes placed by a sequence's pose, materials and their layers, particle systems,
and animation tracks.

The file is a tag index (each entry: a tag, reversed, where its records start, how many, and their
version) and the records it points at; a record refers to others by index entry. The records'
layouts below are at the versions the game's models use, from the StarCraft II modding community's
description of the format (m3addon's structures.xml), given here as field offsets; a model with any
other version is refused rather than misread.
"""

import math
import struct
from dataclasses import dataclass

import numpy as np

# Field offsets per record type and version: (offset, kind). Kinds: u8, u16, i16, u32, i32, f32, ref
# (count, index entry), and animated values (header with the animation id, then the initial value).
LAYOUTS: dict[tuple[str, int], dict[str, tuple[int, str]]] = {
    ("MODL", 29): {"sequences": (16, "ref"), "stc": (28, "ref"), "stg": (40, "ref"), "bones": (80, "ref"),
                   "vFlags": (96, "u32"), "vertices": (100, "ref"), "divisions": (112, "ref"), "boneLookup": (124, "ref"),
                   "materialReferences": (300, "ref"), "standardMaterials": (312, "ref"), "compositeMaterials": (336, "ref"),
                   "particles": (444, "ref"), "billboards": (816, "ref"), "inverseBoneRest": (648, "ref")},
    # Version 28 (Battlefield of Eternity's Hell keep's death): one reference fewer before the
    # particles (attachmentVolumes), the rest the same (structures.xml).
    ("MODL", 28): {"sequences": (16, "ref"), "stc": (28, "ref"), "stg": (40, "ref"), "bones": (80, "ref"),
                   "vFlags": (96, "u32"), "vertices": (100, "ref"), "divisions": (112, "ref"), "boneLookup": (124, "ref"),
                   "materialReferences": (300, "ref"), "standardMaterials": (312, "ref"), "compositeMaterials": (336, "ref"),
                   "particles": (432, "ref"), "billboards": (804, "ref"), "inverseBoneRest": (636, "ref")},
    ("DIV_", 2): {"faces": (0, "ref"), "regions": (12, "ref"), "batches": (24, "ref")},
    ("REGN", 5): {"firstVertex": (8, "u32"), "vertices": (12, "u32"), "firstFace": (16, "u32"), "faces": (20, "u32"),
                  "firstBoneLookup": (26, "u16"), "boneLookups": (28, "u16"), "uvwMult": (40, "f32"), "uvwOffset": (44, "f32")},
    ("BAT_", 1): {"region": (4, "u16"), "materialReference": (10, "u16"), "bone": (12, "i16")},
    ("MATM", 0): {"type": (0, "u32"), "index": (4, "u32")},
    ("BONE", 1): {"name": (4, "ref"), "parent": (20, "i16"), "location": (24, "anim3"), "rotation": (60, "animq"), "scale": (104, "anim3"),
                  "batching": (140, "animu")},
    ("BBSC", 0): {"bone": (12, "u16"), "type": (14, "u8")},
    ("CMP_", 2): {"name": (0, "ref"), "sections": (16, "ref")},
    ("CMS_", 0): {"materialReference": (0, "u32"), "alphaFactor": (4, "animf")},
    ("STC_", 4): {"name": (0, "ref"), "concurrent": (12, "u16"), "priority": (14, "u16"), "animIds": (20, "ref"), "animRefs": (32, "ref")},
    ("STG_", 0): {"name": (0, "ref"), "stcs": (12, "ref")},
    ("SEQS", 2): {"name": (8, "ref"), "start": (20, "u32"), "end": (24, "u32"), "flags": (32, "u32")},
    ("MAT_", 19): {"name": (0, "ref"), "flags": (16, "u32"), "blendMode": (20, "u32"), "priority": (24, "i32"),
                   "specularity": (32, "f32"), "cutoutThreshold": (40, "u8"), "specMult": (44, "f32"), "emisMult": (48, "f32"),
                   "diffuseLayer": (52, "ref"), "decalLayer": (64, "ref"), "specularLayer": (76, "ref"),
                   "emissiveLayer": (100, "ref"), "emissive2Layer": (112, "ref"),
                   "alphaMaskLayer": (148, "ref"), "alphaMask2Layer": (160, "ref"),
                   "decalBlend": (272, "u32"), "emissiveBlend": (276, "u32"), "emissive2Blend": (280, "u32")},
    ("LAYR", 26): {"image": (4, "ref"), "flags": (36, "u32"), "uvSource": (40, "u32"), "channels": (44, "u32"),
                   "brightMult": (48, "animf"), "midtoneOffset": (68, "animf"), "uvOffset": (188, "anim2"), "uvAngle": (216, "anim3"),
                   "uvTiling": (252, "anim2"), "brightness": (320, "animf"), "color": (16, "animc"),
                   "fresnelType": (416, "u32"), "fresnelExponent": (420, "f32"), "fresnelMin": (424, "f32"),
                   "fresnelMaxOffset": (428, "f32"), "fresnelYaw": (456, "f32"), "fresnelPitch": (460, "f32")},
    ("PAR_", 24): {"bone": (0, "u32"), "materialReference": (4, "u32"), "speed1": (12, "animf"), "speed2": (32, "animf"),
                   "lifespan1": (132, "animf"), "lifespan2": (152, "animf"), "sizeMiddle": (188, "f32"), "colorMiddle": (192, "f32"),
                   "alphaMiddle": (196, "f32"), "sizeHold": (204, "f32"), "colorHold": (208, "f32"), "alphaHold": (212, "f32"),
                   "sizes1": (220, "anim3"), "start1": (292, "animc"), "middle1": (312, "animc"), "end1": (332, "animc"),
                   "max": (400, "u32"), "rate": (404, "animf"), "areaType": (424, "u32"), "emissionRegions": (540, "ref"),
                   "sizes2": (560, "anim3"), "start2": (640, "animc"), "middle2": (660, "animc"), "end2": (680, "animc"),
                   "particleType": (772, "u32"), "flags": (1232, "u32")},
}
# Animated values: the struct of the initial value after the 8-byte header (animation id at +4).
ANIMATED = {"animf": "<f", "anim2": "<2f", "anim3": "<3f", "animq": "<4f", "animc": "<4B", "animu": "<I"}
# A sequence data record (SD2V, SD3V, ...): its frames at +0, its keys at +20; the keys' tag gives their struct.
KEYS = {"VEC2": "<2f", "VEC3": "<3f", "QUAT": "<4f", "REAL": "<f", "I16_": "<h", "COL ": "<4B", "U32_": "<I", "FLAG": "<I"}
# The one vertex format read: position, bone weights and lookups, normal, colour (BGRA), two UVs, tangent.
VERTEX_FORMAT, VERTEX_SIZE = 0x1860261, 40
# Material flags and layer channel settings.
UNSHADED, VERTEX_ALPHA, VERTEX_COLOR = 0x10, 0x2, 0x1
BLEND_MODES = {0: "opaque", 1: "alpha", 2: "add", 3: "alphaAdd", 4: "mod", 5: "mod2x"}
CHANNELS = {0: "rgb", 1: "rgba", 2: "a", 3: "r", 4: "g", 5: "b"}
STANDARD_MATERIAL, COMPOSITE_MATERIAL = 1, 3
# A material's decal and emissive layers blend in by one of these (m3studio's mat_layer_blend: 0 mod,
# 1 mod2x, 2 add, 3 blend); these two are team colour ("Team Color Emissive Add", "... Diffuse Add").
TEAM_COLOUR_BLENDS = (4, 5)
# A layer flag: the layer is a solid colour, drawn without an image.
COLOUR_LAYER = 0x400


@dataclass(frozen=True)
class Animated:
    """An animatable value: its animation id (what a sequence's tracks name) and its initial value."""
    id: int
    value: tuple


@dataclass(frozen=True)
class Track:
    """An animation track: times in milliseconds and the value at each."""
    frames: tuple[int, ...]
    keys: tuple[tuple, ...]

    def at(self, ms: float) -> tuple:
        """The value at a time, linearly between keys (a quaternion's too: enough between close keys)."""
        if ms <= self.frames[0] or len(self.frames) == 1:
            return self.keys[0]
        for i in range(1, len(self.frames)):
            if ms <= self.frames[i]:
                f = (ms - self.frames[i - 1]) / max(1, self.frames[i] - self.frames[i - 1])
                return tuple(a + (b - a) * f for a, b in zip(self.keys[i - 1], self.keys[i]))
        return self.keys[-1]


class Record:
    """One record of a tag, its fields read by name."""

    def __init__(self, m3: "M3", tag: str, version: int, at: int):
        self.m3, self.tag, self.version, self.at = m3, tag, version, at
        self.layout = LAYOUTS[(tag, version)]

    def __getitem__(self, name: str):
        offset, kind = self.layout[name]
        at = self.at + offset
        if kind == "ref":
            return struct.unpack_from("<II", self.m3.data, at)  # (count, index entry)
        if kind in ANIMATED:
            animation = struct.unpack_from("<I", self.m3.data, at + 4)[0]
            return Animated(animation, struct.unpack_from(ANIMATED[kind], self.m3.data, at + 8))
        return struct.unpack_from({"u8": "<B", "u16": "<H", "i16": "<h", "u32": "<I", "i32": "<i", "f32": "<f"}[kind], self.m3.data, at)[0]

    def text(self, name: str) -> str:
        return self.m3.text(self[name])

    def records(self, name: str) -> list["Record"]:
        return self.m3.records(self[name])


class M3:
    """A model file: its tag index, and its records by reference."""

    def __init__(self, data: bytes):
        if data[:4] != b"43DM":
            raise ValueError("not an m3 model (MD34)")
        self.data = data
        offset, entries = struct.unpack_from("<II", data, 4)
        self.index = [(t[::-1].decode("latin1"), at, count, version)
                      for t, at, count, version in (struct.unpack_from("<4sIII", data, offset + 16 * i) for i in range(entries))]
        self.model = self.records((1, 1))[0] if self.index[1][0] == "MODL" else None
        if self.model is None:
            raise ValueError("an m3 model whose second index entry isn't its MODL")

    def records(self, ref: tuple[int, int]) -> list[Record]:
        count, entry = ref
        if not count:
            return []
        tag, at, _, version = self.index[entry]
        if (tag, version) not in LAYOUTS:
            raise ValueError(f"m3 records {tag} version {version}: a layout this tool doesn't read")
        size = RECORD_SIZES[(tag, version)]
        return [Record(self, tag, version, at + i * size) for i in range(count)]

    def text(self, ref: tuple[int, int]) -> str:
        count, entry = ref
        _, at, _, _ = self.index[entry]
        return self.data[at:at + count].split(b"\0")[0].decode("latin1") if count else ""

    def numbers(self, ref: tuple[int, int], fmt: str) -> list:
        count, entry = ref
        if not count:
            return []
        _, at, _, _ = self.index[entry]
        return list(struct.unpack_from(f"<{count}{fmt}", self.data, at))

    # --- Animation ----------------------------------------------------------------------------

    def collections(self) -> dict[str, Record]:
        """The sequences' transformation collections by name (Stand_full, GLstand_CloudsInwards, ...)."""
        return {c.text("name"): c for c in self.model.records("stc")}

    def groups(self) -> dict[str, list[Record]]:
        """Each sequence's collections, by the sequence's name (STG: Stand -> [Stand_full])."""
        stcs = self.model.records("stc")
        return {g.text("name"): [stcs[i] for i in self.numbers(g["stcs"], "I")] for g in self.model.records("stg")}

    def tracks(self, collection: Record) -> dict[int, Track]:
        """A collection's tracks by animation id. Each track points at one sequence data record
        (high half: which of the collection's sequence data references, low half: which record)."""
        ids = self.numbers(collection["animIds"], "I")
        refs = self.numbers(collection["animRefs"], "I")
        found = {}
        for animation, ref in zip(ids, refs):
            kind, index = ref >> 16, ref & 0xFFFF
            count, entry = struct.unpack_from("<II", self.data, collection.at + 48 + 12 * kind)
            if not count:
                continue
            tag, at, _, version = self.index[entry]
            size = SEQUENCE_DATA_SIZE.get((tag, version))
            if size is None:
                continue  # events and the like: no values to take
            record = at + index * size
            frames = self.numbers(struct.unpack_from("<II", self.data, record), "i")
            key_count, key_entry = struct.unpack_from("<II", self.data, record + 20)
            key_tag, key_at, _, _ = self.index[key_entry]
            fmt = KEYS.get(key_tag)
            if fmt is None or not key_count:
                continue
            width = struct.calcsize(fmt)
            keys = tuple(struct.unpack_from(fmt, self.data, key_at + width * k) for k in range(key_count))
            found[animation] = Track(tuple(frames), keys)
        return found

    # --- Bones and meshes ---------------------------------------------------------------------

    def bones(self) -> list[Record]:
        return self.model.records("bones")

    def pose(self, tracks: dict[int, Track], ms: float = 0, eye=None) -> list[np.ndarray]:
        """Each bone's world matrix in a pose: its rest values where the tracks have none. Given the
        camera's eye, the billboarded bones turn to face it (BILLBOARD_FACING, the shortest turn
        that points the bone's local -Y at the eye), their children with them."""
        bones = self.bones()
        local = []
        for bone in bones:
            loc, rot, scale = (tracks[a.id].at(ms) if a.id in tracks else a.value
                               for a in (bone["location"], bone["rotation"], bone["scale"]))
            local.append(compose(loc, rot, scale))
        facing = {b["bone"] for b in self.model.records("billboards") if b["type"] == BILLBOARD_ALL} if eye is not None else set()
        world: list[np.ndarray] = []
        for i, bone in enumerate(bones):
            parent = bone["parent"]
            matrix = local[i] if parent < 0 else world[parent] @ local[i]  # parents come first
            if i in facing:
                matrix = face(matrix, np.asarray(eye, dtype=float))
            world.append(matrix)
        return world

    def inverse_rest(self) -> list[np.ndarray]:
        """Each bone's inverse rest matrix: from the model's space into the bone's."""
        count, entry = self.model["inverseBoneRest"]
        _, at, _, _ = self.index[entry]
        return [np.array(struct.unpack_from("<16f", self.data, at + 64 * i)).reshape(4, 4).T for i in range(count)]

    def regions(self) -> list[Record]:
        return self.model.records("divisions")[0].records("regions")

    def region_materials(self) -> dict[int, list[int]]:
        """Each mesh region's material references, in drawing order."""
        found: dict[int, list[int]] = {}
        for batch in self.model.records("divisions")[0].records("batches"):
            found.setdefault(batch["region"], []).append(batch["materialReference"])
        return found

    def region_bone(self, region: Record) -> int:
        """The bone a region's first vertex hangs on (the regions here hang on one bone each)."""
        lookup = self.numbers(self.model["boneLookup"], "H")
        _, at, _, _ = self.index[self.model["vertices"][1]]
        vertex = at + region["firstVertex"] * VERTEX_SIZE
        return lookup[region["firstBoneLookup"] + self.data[vertex + 16]]

    def mesh(self, region: Record, world: list[np.ndarray]) -> dict:
        """A region's vertices placed by a pose (world matrices): positions, normals, both UV sets,
        RGBA colours, and its triangles (indices from 0)."""
        if self.model["vFlags"] != VERTEX_FORMAT:
            raise ValueError(f"m3 vertex format {self.model['vFlags']:#x}: not one this tool reads")
        lookup = self.numbers(self.model["boneLookup"], "H")
        inverse = self.inverse_rest()
        _, at, _, _ = self.index[self.model["vertices"][1]]
        uv_scale, uv_offset = region["uvwMult"] / 16 / 2048, region["uvwOffset"]
        positions, normals, uvs, uvs2, colors = [], [], [], [], []
        for k in range(region["firstVertex"], region["firstVertex"] + region["vertices"]):
            v = at + k * VERTEX_SIZE
            weights = self.data[v + 12:v + 16]
            point, normal = np.zeros(4), np.zeros(3)
            stored = np.array([self.data[v + 20 + k] / 255 * 2 - 1 for k in range(3)])
            for w, b in zip(weights, self.data[v + 16:v + 20]):
                if w:
                    bone = lookup[region["firstBoneLookup"] + b]
                    skin = world[bone] @ inverse[bone]
                    point += (w / 255) * (skin @ np.array([*struct.unpack_from("<3f", self.data, v), 1.0]))
                    normal += (w / 255) * (np.linalg.inv(skin[:3, :3]).T @ stored)
            positions.append(point[:3])
            normals.append(normal / (np.linalg.norm(normal) or 1.0))
            u, w = struct.unpack_from("<2h", self.data, v + 28)
            uvs.append((u * uv_scale + uv_offset, w * uv_scale + uv_offset))
            u, w = struct.unpack_from("<2h", self.data, v + 32)
            uvs2.append((u * uv_scale + uv_offset, w * uv_scale + uv_offset))
            b, g, r, a = self.data[v + 24:v + 28]
            colors.append((r, g, b, a))
        faces = self.numbers(self.model.records("divisions")[0]["faces"], "H")
        triangles = faces[region["firstFace"]:region["firstFace"] + region["faces"]]
        return {"positions": positions, "normals": normals, "uvs": uvs, "uvs2": uvs2, "colors": colors, "indices": triangles}

    # --- Materials ----------------------------------------------------------------------------

    def has_team_colour(self, sequence: str | None = None) -> bool:
        """Whether the model draws any team colour (the game tints it by its team): a batch whose
        material blends its decal or either emissive layer as team colour (TEAM_COLOUR_BLENDS), that
        layer drawn (an image, or a solid colour) and lit (its brightness and its multiplier above 0).
        In a sequence (Stand for a standing structure, Dead for its remains), only what that sequence
        shows: a batch whose bone's batching it never turns on doesn't count, nor a layer it never
        lights (Dragon Shire's moonwell's remains: its team-coloured pools dissolve away). Particles
        and lights aren't counted (the game's own team colour flag on a light, LITE 0x20, stays on in
        remains that show none)."""
        tracks: dict[int, Track] = {}
        if sequence is not None:
            groups = self.groups()
            if sequence not in groups:
                raise ValueError(f"m3 model with no sequence {sequence}")
            for collection in groups[sequence]:
                tracks.update(self.tracks(collection))

        def on(value: Animated) -> bool:
            if value.id in tracks:
                return any(key[0] > 0 for key in tracks[value.id].keys)
            return value.value[0] > 0

        bones = self.bones()
        for batch in self.model.records("divisions")[0].records("batches"):
            if 0 <= batch["bone"] < len(bones) and not on(bones[batch["bone"]]["batching"]):
                continue
            try:
                materials = self.material(batch["materialReference"])
            except ValueError:
                continue  # another kind of material (displacement, ...): no team colour layers
            for material in materials:
                for layer, blend in (("decalLayer", "decalBlend"), ("emissiveLayer", "emissiveBlend"), ("emissive2Layer", "emissive2Blend")):
                    if material[blend] not in TEAM_COLOUR_BLENDS or not material[layer][0]:
                        continue
                    drawn = material.records(layer)[0]
                    if (drawn["image"][0] or drawn["flags"] & COLOUR_LAYER) and on(drawn["brightness"]) and on(drawn["brightMult"]):
                        return True
        return False

    def material(self, reference: int) -> list[Record]:
        """A material reference's standard materials: itself, or a composite's sections, in order."""
        kind, index = (lambda r: (r["type"], r["index"]))(self.model.records("materialReferences")[reference])
        if kind == STANDARD_MATERIAL:
            return [self.model.records("standardMaterials")[index]]
        if kind == COMPOSITE_MATERIAL:
            composite = self.model.records("compositeMaterials")[index]
            return [m for s in composite.records("sections") for m in self.material(s["materialReference"])]
        raise ValueError(f"m3 material type {kind}: not one this tool reads")


RECORD_SIZES = {("MODL", 28): 844, ("MODL", 29): 856, ("DIV_", 2): 52, ("REGN", 5): 48, ("BAT_", 1): 14, ("MATM", 0): 8, ("BONE", 1): 160,
                ("BBSC", 0): 48, ("CMP_", 2): 28, ("CMS_", 0): 24, ("STC_", 4): 204, ("STG_", 0): 24, ("SEQS", 2): 92, ("MAT_", 19): 340,
                ("LAYR", 26): 464, ("PAR_", 24): 1496}
SEQUENCE_DATA_SIZE = {(t, 0): 32 for t in ("SD2V", "SD3V", "SD4Q", "SDR3", "SDS6", "SDCC", "SDU3", "SDFG")}


BILLBOARD_ALL = 6  # a billboard that turns on every axis to face the camera ("World All")


def face(matrix: np.ndarray, eye: np.ndarray) -> np.ndarray:
    """A bone's world matrix turned, about its own origin, so that its local -Y points at the eye
    (the shortest such turn)."""
    position = matrix[:3, 3]
    current = -matrix[:3, 1] / np.linalg.norm(matrix[:3, 1])
    wanted = (eye - position) / np.linalg.norm(eye - position)
    axis = np.cross(current, wanted)
    sin, cos = np.linalg.norm(axis), float(current @ wanted)
    if sin < 1e-9:
        return matrix
    k = axis / sin
    cross = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    turn = np.eye(3) + sin * cross + (1 - cos) * (cross @ cross)
    out = matrix.copy()
    out[:3, :3] = turn @ matrix[:3, :3]
    return out


def compose(location, rotation, scale) -> np.ndarray:
    """A bone's local matrix: scale, then rotation (a quaternion, x y z w), then translation."""
    x, y, z, w = rotation
    norm = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    matrix = np.eye(4)
    matrix[:3, :3] = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                               [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                               [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]) * np.array(scale)
    matrix[:3, 3] = location
    return matrix
