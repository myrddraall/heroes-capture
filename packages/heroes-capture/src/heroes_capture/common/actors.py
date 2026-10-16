"""What a map's structures and doodads are made of in the game's data, for the calibration's report:
each structure's actor tree (the actors its unit's birth creates, the actors those create, the event
macros they run, the models they show and swap to) and what can move in each model (the particle
systems, ribbons and lights its .m3 holds). Pausing and hiding have to reach all of it: a part the
capture's pause doesn't still, or its hiding doesn't hide, is found here by name.

The catalogs load as the game loads them: the core mod, the shared Heroes data, then the map's mods
(each after the mods it names), then the map's own data; a later definition of an entry adds to an
earlier one (and its values win), and an entry inherits from its parent."""

import re
import struct
import xml.etree.ElementTree as ET

import numpy as np

# How deep an actor tree is followed (a structure's: unit, its turret, their explosions).
TREE_DEPTH = 4
# The .m3 tags (stored reversed in the file) that move on their own: particle systems, ribbons,
# lights.
MOVING_PARTS = {"PAR_": "particles", "RIB_": "ribbons", "LITE": "lights"}


def mod_order(documentinfo, map_mods: list[str]) -> list[str]:
    """The mods a map's data loads, dependencies first: the core mod, the shared Heroes data, then
    the map's (each after those it names). `documentinfo(mod)` returns the mods a mod names."""
    order: list[str] = []

    def visit(mod: str) -> None:
        if mod in order:
            return
        order.append(mod)  # (placeholder against cycles: moved to the end once its dependencies are in)
        for dependency in documentinfo(mod):
            visit(dependency.lower())
        order.remove(mod)
        order.append(mod)

    for mod in ["core.stormmod", "heroesdata.stormmod", *map_mods]:
        visit(mod.lower())
    return order


def _documentinfo(storage, mod: str) -> list[str]:
    """The mods a mod names in its DocumentInfo."""
    names = []
    for info in storage.find(f"*\\{mod}\\documentinfo"):
        names += [m.decode() for m in re.findall(rb"([A-Za-z0-9_]+\.stormmod)", storage.read(info) or b"")]
    return names


def catalog_texts(storage, map_mods: list[str], kinds: tuple[bytes, ...] = (b"<CActor", b"<CModel")) -> list[str]:
    """The game data catalogs with actors or models in them (or the entries `kinds` names) that a
    map loads, in load order (the map's own data not included)."""
    files = [n for n in storage.find("*.xml") if "\\base.stormdata\\gamedata\\" in n.lower()]
    texts = []
    for mod in mod_order(lambda m: _documentinfo(storage, m), map_mods):
        for name in files:
            if f"\\{mod}\\base.stormdata\\" in name.lower():
                data = storage.read(name) or b""
                if any(kind in data for kind in kinds):
                    texts.append(data.decode("utf-8", errors="replace"))
    return texts


def unit_names(storage, map_mods: list[str], own_strings: str, unit_types: list[str]) -> dict[str, str]:
    """Each unit type's name as the game shows it (GameStrings' Unit/Name/<type>, English: Dragon
    Shire's TownMoonwellL3 is a "Healing Fountain"), the mods a map loads in load order and then the
    map's own strings, the later winning."""
    files = [n for n in storage.find("*gamestrings.txt") if "\\enus.stormdata\\localizeddata\\" in n.lower()]
    texts = []
    for mod in mod_order(lambda m: _documentinfo(storage, m), map_mods):
        texts += [(storage.read(n) or b"").decode("utf-8", errors="replace") for n in files if f"\\{mod}\\" in n.lower()]
    names: dict[str, str] = {}
    wanted = set(unit_types)
    for text in [*texts, own_strings]:
        for unit, name in re.findall(r"^Unit/Name/([^=\r\n]+)=([^\r\n]*)", text, re.M):
            if unit in wanted and name.strip():
                names[unit] = name.strip()
    return names


# The players each team's structures belong to (Heroes' Order is player 11, Chaos 12): actor events
# and model aspects name them ("PlayerId 11 11", ModelOwnerHasPlayerId="11").
TEAM_PLAYERS = {"order": 11, "chaos": 12}


def for_player(terms: str, player: int | None) -> bool | None:
    """Whether an actor event's terms hold for `player`: True or False where they name players
    ("PlayerId <first> <last>"), None where they name none."""
    found = re.search(r"\bPlayerId (\d+)(?: (\d+))?", terms)
    if not found:
        return None
    first, last = int(found.group(1)), int(found.group(2) or found.group(1))
    return player is not None and first <= player <= last


class Catalog:
    """The actors and models of a map's data (catalog texts in load order)."""

    def __init__(self, texts: list[str]):
        self.actors: dict[str, dict] = {}
        self.models: dict[str, dict] = {}
        for text in texts:
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                continue
            for el in root:
                entry_id = el.get("id")
                if not entry_id:
                    continue
                if el.tag.startswith("CActor"):
                    entry = self.actors.setdefault(entry_id, {"class": el.tag, "parent": None, "unit": None, "elements": []})
                    entry["parent"] = el.get("parent") or entry["parent"]
                    entry["unit"] = el.get("unitName") or entry["unit"]
                    entry["elements"] += list(el)
                elif el.tag == "CModel":
                    entry = self.models.setdefault(entry_id, {"parent": None, "file": None, "scale": None})
                    entry["parent"] = el.get("parent") or entry["parent"]
                    model = el.find("Model")
                    if model is not None and model.get("value"):
                        entry["file"] = model.get("value")
                    # Its largest size (ScaleMax: the game picks each copy's between ScaleMin and it),
                    # written as X, Y and Z or as one value "x,y,z" (Tomb of the Spider Queen's fort).
                    scale = el.find("ScaleMax")
                    if scale is not None:
                        axes = [scale.get(a) for a in "XYZ" if scale.get(a)] or (scale.get("value") or "").split(",")
                        entry["scale"] = max((float(v) for v in axes if v.strip()), default=1.0)
        # Each unit type's actors created by its birth: its own actor (unitName: the birth event is
        # its template's, "UnitBirth.##unitName##"), and any whose own events create it on
        # "UnitBirth.<type>".
        self.born: dict[str, list[str]] = {}
        for actor_id, entry in self.actors.items():
            if entry["unit"]:
                self.born.setdefault(entry["unit"], []).append(actor_id)
            for el in entry["elements"]:
                if el.tag == "On" and el.get("Send", "").strip() == "Create":
                    first = el.get("Terms", "").split(";")[0].strip()
                    if first.startswith("UnitBirth.") and actor_id not in self.born.get(first[len("UnitBirth."):], []):
                        self.born.setdefault(first[len("UnitBirth."):], []).append(actor_id)

    def _chain(self, table: dict, entry_id: str) -> list[dict]:
        """An entry and its parents, nearest first."""
        chain, seen = [], set()
        while entry_id in table and entry_id not in seen and len(chain) < 20:
            seen.add(entry_id)
            chain.append(table[entry_id])
            entry_id = table[entry_id]["parent"]
        return chain

    def events(self, actor_id: str) -> list[tuple[str, str]]:
        """An actor's events (Terms, Send): its own, its parents' and its event macros', with the
        templates' ##id## and ##unitName## filled in."""
        unit = self.actors[actor_id]["unit"] or actor_id
        return [(t.replace("##id##", actor_id).replace("##unitName##", unit), s.replace("##id##", actor_id).replace("##unitName##", unit))
                for t, s in self._events(actor_id)]

    def _events(self, actor_id: str) -> list[tuple[str, str]]:
        found, removed = [], set()
        for entry in self._chain(self.actors, actor_id):
            for el in entry["elements"]:
                if el.tag == "On":
                    found.append((el.get("Terms", ""), el.get("Send", "")))
                elif el.tag == "Remove":  # a later catalog's (a map's mod) taking an event out
                    removed.add((el.get("Terms", ""), el.get("Send", "")))
                elif el.tag == "Macros" and el.get("value") in self.actors:
                    found += [(e.get("Terms", ""), e.get("Send", "")) for e in self.actors[el.get("value")]["elements"] if e.tag == "On"]
        return [e for e in found if e not in removed]

    def actor_model(self, actor_id: str) -> str | None:
        """The model an actor shows (its own or its nearest parent's Model)."""
        for entry in self._chain(self.actors, actor_id):
            for el in reversed(entry["elements"]):
                if el.tag == "Model" and el.get("value"):
                    return el.get("value")
        return None

    def model_file(self, model_id: str) -> str | None:
        """A model's .m3 (its own or its nearest parent's)."""
        for entry in self._chain(self.models, model_id):
            if entry["file"]:
                return entry["file"]
        return None

    def model_scale(self, model_id: str) -> float:
        """A model's largest size (its own or its nearest parent's ScaleMax; 1 without one)."""
        for entry in self._chain(self.models, model_id):
            if entry["scale"] is not None:
                return entry["scale"]
        return 1.0

    def node(self, actor_id: str, how: str, depth: int, seen: set) -> dict:
        """An actor with what it shows and what it creates (TREE_DEPTH deep)."""
        seen.add(actor_id)
        entry = self.actors[actor_id]
        events = self.events(actor_id)
        models = [m for m in [self.actor_model(actor_id)] + [s.split()[1] for _, s in events if s.startswith("ModelSwap ") and len(s.split()) > 1] if m]
        children = []
        if depth < TREE_DEPTH:
            for terms, send in events:
                words = send.split()
                if len(words) >= 2 and words[0] == "Create" and words[1] in self.actors and words[1] not in seen:
                    children.append(self.node(words[1], terms.split(";")[0].strip(), depth + 1, seen))
        return {"actor": actor_id, "class": entry["class"].removeprefix("CActor"), "on": how,
                "models": list(dict.fromkeys(models)), "children": [c for c in children if shows(c)]}

    def death_models(self, actor_id: str, player: int | None = None) -> list[str]:
        """The models an actor's structure leaves when it dies: its normal death's (DeathArray
        Normal, its own or its nearest parent's) and its death variations' (DeathCustoms: a wall's,
        a gate's), with the templates' ##unitName## filled in; "Invisible" left out. For `player`,
        the variation its death is customized to for that player when it is ("UnitDeathCustomize;
        PlayerId" "DeathCustomize <name>": Battlefield of Eternity's Heaven and Hell deaths)."""
        unit = self.actors[actor_id]["unit"] or actor_id
        if player is not None:
            names = [send.split()[1] for terms, send in self.events(actor_id)
                     if "UnitDeathCustomize" in terms and for_player(terms, player) is True and send.startswith("DeathCustomize ")]
            for entry in self._chain(self.actors, actor_id) if names else ():
                for el in entry["elements"]:
                    link = el.get("ModelLink")
                    if el.tag == "DeathCustoms" and el.get("Name") in names and link and link.replace("##unitName##", unit) in self.models:
                        return [link.replace("##unitName##", unit)]
        normal, customs = None, []
        for entry in self._chain(self.actors, actor_id):
            for el in entry["elements"]:
                link = el.get("ModelLink") or (el.find("ModelLink").get("value") if el.find("ModelLink") is not None else None)
                if not link:
                    continue
                link = link.replace("##unitName##", unit)
                if el.tag == "DeathArray" and el.get("index", "Normal") == "Normal" and normal is None:
                    normal = link
                elif el.tag == "DeathCustoms" and link != "Invisible" and link not in customs:
                    customs.append(link)
        return [m for m in dict.fromkeys(([normal] if normal else []) + customs) if m in self.models]

    def finished_model(self, actor_id: str, player: int | None = None) -> str | None:
        """The model an actor shows once built: the one it swaps to when its build animation ends
        on its birth model ("AnimDone; AnimName Build" ... "ModelSwap <model>": a level 3 tower,
        a gate, a keep), else its own. For `player` (TEAM_PLAYERS), what the actor picks for that
        player first, as Battlefield of Eternity's Heaven and Hell structures do: such a swap whose
        terms name the player, its model aspect for the player ("ModelOwnerHasPlayerId": a wall, a
        moonwell), its model customized for the player as it is made ("AssetPreInit; PlayerId"
        "ModelLinkCustomize <model>": a fort)."""
        events = self.events(actor_id)

        def built(mine, names=("AnimName Build", "AnimName Birth")) -> str | None:
            for terms, send in events:
                if mine(terms) and "AnimDone" in terms and (names is None or any(n in terms for n in names)) and send.startswith("ModelSwap "):
                    return send.split()[1]
            return None

        if player is not None:
            # (Any animation's end, for the player: a core swaps to its finished model when its
            # "CoreStart" ends.)
            if found := built(lambda terms: for_player(terms, player) is True, None):
                return found
            for entry in self._chain(self.actors, actor_id):
                for el in entry["elements"]:
                    for aspect in el.iter("Aspects") if el.tag == "ModelAspectSets" else ():
                        if aspect.get("ModelOwnerHasPlayerId") == str(player) and aspect.get("Model"):
                            return aspect.get("Model")
            for terms, send in events:
                if "AssetPreInit" in terms and for_player(terms, player) is True and send.startswith("ModelLinkCustomize "):
                    return send.split()[1]
        if found := built(lambda terms: for_player(terms, player) is not False):
            return found
        model = self.actor_model(actor_id)
        # A template's model, "##unitName##" (a fort's, a moonwell's): the model named for the unit.
        unit = self.actors[actor_id]["unit"] or actor_id
        return model.replace("##unitName##", unit).replace("##id##", actor_id) if model else None

    def tree(self, unit_type: str) -> list[dict]:
        """The actors a unit type's birth creates, each with its tree; only the actors that show a
        model, or create one that does (no sounds, shadows, empty templates)."""
        seen: set = set()
        return [n for n in (self.node(a, f"UnitBirth.{unit_type}", 0, seen) for a in self.born.get(unit_type, []) if a not in seen) if shows(n)]


def shows(node: dict) -> bool:
    """Whether an actor shows a model, or creates one that does."""
    return bool(node["models"]) or any(shows(c) for c in node["children"])


def m3_parts(m3: bytes) -> dict[str, int]:
    """How many particle systems, ribbons and lights a model file holds (its tag index: the tag,
    reversed, then offset, count and version per entry)."""
    counts = dict.fromkeys(MOVING_PARTS.values(), 0)
    if len(m3) < 12:
        return counts
    offset, entries = struct.unpack_from("<II", m3, 4)
    for i in range(entries):
        if offset + i * 16 + 16 > len(m3):
            break
        tag, _, repetitions, _ = struct.unpack_from("<4sIII", m3, offset + i * 16)
        name = MOVING_PARTS.get(tag[::-1].decode("latin1"))
        if name:
            counts[name] += repetitions
    return counts


# A sequence record's size by its version (the tag index's), and its flag for "plays once".
SEQUENCE_SIZE, NOT_LOOPING = {1: 96, 2: 92}, 1


def m3_sequences(m3: bytes) -> list[dict]:
    """A model file's animation sequences: name, length in seconds and whether it loops."""
    if len(m3) < 12:
        return []
    offset, entries = struct.unpack_from("<II", m3, 4)
    index = [struct.unpack_from("<4sIII", m3, offset + i * 16) for i in range(entries) if offset + i * 16 + 16 <= len(m3)]
    found = []
    for tag, at, count, version in index:
        if tag[::-1] != b"SEQS":
            continue
        size = SEQUENCE_SIZE.get(version, 92)
        for k in range(count):
            base = at + k * size
            if base + 36 > len(m3):
                break
            name_count, name_index, _ = struct.unpack_from("<III", m3, base + 8)
            start, end = struct.unpack_from("<II", m3, base + 20)
            flags = struct.unpack_from("<I", m3, base + 32)[0]
            name = ""
            if name_index < len(index):
                _, name_at, _, _ = index[name_index]
                name = m3[name_at:name_at + name_count].split(b"\0")[0].decode("latin1")
            found.append({"name": name, "seconds": round((end - start) / 1000, 3), "loops": not flags & NOT_LOOPING})
    return found


# A mesh region record's size by its version, and where its first vertex and vertex count are. (A
# sequence's own bounds are far looser than what the model draws: a tower's reach 6.6 cells out
# where its vertices reach 2.4.)
REGION_SIZE, REGION_VERTICES = {3: 36, 4: 40, 5: 48}, 8


def m3_extent(m3: bytes) -> dict | None:
    """How far a model's mesh reaches across the ground from its origin, whichever way it faces,
    and how high, in cells (its vertices: the regions give how many, the vertex array is the
    byte array that many of them fill, each starting with its position)."""
    if len(m3) < 12:
        return None
    offset, entries = struct.unpack_from("<II", m3, 4)
    index = [struct.unpack_from("<4sIII", m3, offset + i * 16) for i in range(entries) if offset + i * 16 + 16 <= len(m3)]
    count = 0
    for tag, at, records, version in index:
        if tag[::-1] == b"REGN" and version in REGION_SIZE:
            for k in range(records):
                first, many = struct.unpack_from("<II", m3, at + k * REGION_SIZE[version] + REGION_VERTICES)
                count = max(count, first + many)
    arrays = [(at, size) for tag, at, size, _ in index if tag[::-1] == b"U8__" and count and size % count == 0 and 12 <= size // count <= 128]
    if not arrays:
        return None
    at, size = max(arrays, key=lambda a: a[1])
    stride = size // count
    vertices = np.ndarray((count, 3), "<f4", m3, at, (stride, 4))
    return {"reach": round(float(np.hypot(vertices[:, 0], vertices[:, 1]).max()), 3), "height": round(float(vertices[:, 2].max()), 3)}


def model_parts(storage, catalog: Catalog, model_ids: list[str]) -> dict[str, dict]:
    """Each model's file, size (Catalog.model_scale), mesh extent (m3_extent), moving parts
    (m3_parts) and animation sequences (m3_sequences), by model id; a model whose file isn't found
    has none."""
    found = {}
    for model_id in model_ids:
        file = catalog.model_file(model_id)
        parts = dict.fromkeys(MOVING_PARTS.values(), 0)
        # A doodad type without a model entry is found by its name (Assets\Doodads\<type>\<type>.m3).
        pattern = "*\\" + file.lower().replace("/", "\\") if file else f"*\\assets\\doodads\\{model_id.lower()}\\{model_id.lower()}.m3"
        hits = storage.find(pattern)
        if not hits and pattern.endswith(".m3"):
            # A model with variations: its files numbered (Dragon Shire's gates' _00 to _03): the first.
            hits = sorted(storage.find(pattern[:-3] + "_*.m3"))[:1]
        sequences, extent, team = [], None, {}
        if hits:
            file = file or hits[-1]
            data = storage.read(hits[-1]) or b""
            parts = m3_parts(data)
            sequences = m3_sequences(data)
            extent = m3_extent(data)
            team = team_colour(data)
        found[model_id] = {"file": file, "scale": catalog.model_scale(model_id), "extent": extent, "teamColour": team,
                           **parts, "sequences": sequences}
    return found


def team_colour(m3: bytes) -> dict[str, bool]:
    """Whether a model draws team colour (common/m3.py has_team_colour) in each of its sequences
    a structure is shot in: Stand (standing), Death (its remains, shot while it plays) and Dead
    (after it); a sequence the model lacks, or a file the reader can't read, left out. A model with
    no team colour in any of its materials draws none in any of them, those it lacks included
    (Battlefield of Eternity's walls: no Stand sequence, only GLstand)."""
    from heroes_capture.common.m3 import M3

    found = {}
    try:
        model = M3(m3)
        if not model.has_team_colour():
            return {sequence: False for sequence in ("Stand", "Death", "Dead")}
        groups = model.groups()
        for sequence in ("Stand", "Death", "Dead"):
            if sequence in groups:
                found[sequence] = model.has_team_colour(sequence)
        # Standing, a model without a Stand sequence plays its global loop (Battlefield of Eternity's
        # Hell towers: GLstand only).
        if "Stand" not in found and "GLstand" in groups:
            found["Stand"] = model.has_team_colour("GLstand")
    except (ValueError, struct.error, IndexError, KeyError):
        pass
    return found


def tree_models(tree: list[dict]) -> list[str]:
    """Every model an actor tree shows."""
    return list(dict.fromkeys(m for n in tree for m in n["models"] + tree_models(n["children"])))


def report(storage, map_mods: list[str], own_texts: list[str], unit_types: list[str], doodad_types: list[str], own_strings: str = "") -> dict:
    """The calibration's data report: each structure type's actor tree, finished model (the
    standing timing restarts it) and death models, and each team's where the actor picks them by
    player ("teams": {type: {team: {stand, deaths}}}), its name as the game shows it (unit_names;
    `own_strings`: the map's GameStrings), and the moving parts and animations of every model those
    show and of every doodad type placed (a doodad's type is its model)."""
    catalog = Catalog(catalog_texts(storage, map_mods) + own_texts)
    trees = {t: catalog.tree(t) for t in unit_types}
    # Each type's finished model: its unit actor's (the actor named for the unit, else the first its
    # birth creates).
    # Each team's, where the actor picks them by player (Battlefield of Eternity's Heaven and Hell).
    finished, deaths, teams = {}, {}, {}
    for t in unit_types:
        actor = t if t in catalog.actors else next(iter(catalog.born.get(t, [])), None)
        if not actor:
            continue
        if (model := catalog.finished_model(actor)) and not model.startswith("##"):
            finished[t] = model
        deaths[t] = catalog.death_models(actor)
        for team, player in TEAM_PLAYERS.items():
            stand, died = catalog.finished_model(actor, player), catalog.death_models(actor, player)
            if (stand and stand != model and not stand.startswith("##")) or died != deaths[t]:
                teams.setdefault(t, {})[team] = {"stand": stand if stand and not stand.startswith("##") else finished.get(t), "deaths": died}
    team_models = [m for by_team in teams.values() for v in by_team.values() for m in [v["stand"], *v["deaths"]] if m]
    models = model_parts(storage, catalog, list(dict.fromkeys([m for t in trees.values() for m in tree_models(t)] + list(finished.values())
                                                              + [m for d in deaths.values() for m in d] + team_models + doodad_types)))
    return {"structures": trees, "names": unit_names(storage, map_mods, own_strings, unit_types), "standModels": finished,
            "deathModels": deaths, "teams": teams, "models": models, "doodads": {t: models[t] for t in doodad_types}}
