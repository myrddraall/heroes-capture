"""Step 1 of a new map (`map survey`): what the map's files and the game's data say about it, read
from its calibration's preparation (no game needed), and so what its calibration will measure and
what the data answers instead. Kept as survey.json beside the map's packs, and shown as a short
report, its odd cases as questions for the review.

What it reads (common/actors.py report, made as the map is prepared for its calibration): each
structure's name in the game, its finished model (whether it draws team colour standing, whether it
animates: a global loop, particles) and its death models (whether its remains draw team colour,
after its death or only while it plays, and how long the death plays), how far the models reach
on screen (which structures can overlap), the copies' candidate spots, the doodads that move while
paused, the water and the sky."""


from heroes_capture.categories.battleground.elements import (
    COPY_EXCLUDED,
    GENERIC_DEATH,
    NEIGHBOUR_REACH,
    death_models,
    goes_with_a_fort,
    may_overlap,
    rubble_team_colour,
    stand_model,
    stand_kind,
    standing_team_colour,
)


def _death_models(u: dict, report: dict) -> list[dict]:
    models = report.get("models") or {}
    return [models[m] for m in death_models(u, report) if m != GENERIC_DEATH and m in models]


def _remains(u: dict, report: dict) -> bool | str | None:
    """Whether its remains draw team colour: True, False, "while falling" (only while its death
    plays: Dragon Shire's Healing Fountains), None (the data can't tell)."""
    found = rubble_team_colour(u, report)
    if found:
        colours = [m.get("teamColour") or {} for m in _death_models(u, report)]
        if not any(c.get("Dead") for c in colours) and any(c.get("Death") for c in colours):
            return "while falling"
    return found


def _fall_seconds(u: dict, report: dict) -> float | None:
    """How long its death plays (its death models' Death sequence; the longest), None without one."""
    seconds = [s["seconds"] for m in _death_models(u, report) for s in m.get("sequences") or [] if s["name"] == "Death"]
    return max(seconds) if seconds else None


def _animates(u: dict, report: dict) -> bool:
    """Whether its finished model moves standing (a global loop or another sequence than Stand,
    or particles): its moment is picked from a film (the calibration's standing stage)."""
    model = (report.get("models") or {}).get(stand_model(u, report)) or {}
    return bool(model.get("particles")) or any(s["name"] != "Stand" for s in model.get("sequences") or [])


TEAMS = {"order": "blue", "chaos": "red"}


def survey(manifest: dict) -> dict:
    """The map's survey (see the module's note), from its calibration's prepared manifest."""
    elements = manifest.get("elements") or {}
    report = elements.get("report") or {}
    structures = elements.get("structures") or []
    distance = manifest.get("distance") or 0.0
    kinds: dict[str, dict] = {}
    for u in structures:
        name, team = u.get("name") or u["type"], u.get("owner")
        kind = kinds.setdefault(name, {"name": name, "count": 0, "types": [], "standing": {}, "remains": {}, "fall": set(),
                                       "animates": False, "looks": set()})
        kind["count"] += 1
        if u["type"] not in kind["types"]:
            kind["types"].append(u["type"])
        kind["standing"].setdefault(team, set()).add(standing_team_colour(u, report))
        kind["remains"].setdefault(team, set()).add(_remains(u, report))
        if (seconds := _fall_seconds(u, report)) is not None:
            kind["fall"].add(round(seconds, 1))
        kind["animates"] |= _animates(u, report)
        kind["looks"].add(stand_kind(u, report))

    def by_team(values: dict) -> dict:
        """A value for each team (None where its types disagree or the data can't tell)."""
        return {team: next(iter(v)) if len(v) == 1 else None for team, v in values.items()}

    overlaps = sum(may_overlap(a, b, report, distance, NEIGHBOUR_REACH) for i, a in enumerate(structures) for b in structures[i + 1:])
    candidates = elements.get("copyCandidates") or {}
    moving = sorted(t for t, d in (report.get("doodads") or {}).items() if d.get("particles") or d.get("ribbons") or d.get("lights"))
    lone = [u for u in structures if u.get("town") is None and not u.get("core")]
    found = {
        "map": manifest.get("map"),
        "cells": [(manifest.get("mapSize") or {}).get("width"), (manifest.get("mapSize") or {}).get("height")],
        "outside": "sky" if (manifest.get("sky") or {}).get("mode") == "matte" else "black terrain",
        "skyModels": {k: v for k, v in ((manifest.get("sky") or {}).get("mapSky") or {}).items() if v},
        "waterCells": sum(x1 - x0 for _, x0, x1 in manifest.get("water") or []),
        "tiles": len(manifest.get("tiles") or []),
        "structures": len(structures), "towns": len(elements.get("towns") or []), "camps": len(elements.get("camps") or []),
        "outposts": len(elements.get("outposts") or []),
        "kinds": [{**k, "standing": by_team(k["standing"]), "remains": by_team(k["remains"]), "fall": sorted(k["fall"]),
                   "looks": sorted(k["looks"]), "byTeam": any("|" in look for look in k["looks"])} for k in kinds.values()],
        "overlaps": overlaps,
        "copies": {"structures": len(candidates), "spots": sum(len(v) for v in candidates.values()),
                   "excluded": sorted({u.get("name") or u["type"] for u in structures if u["type"] in COPY_EXCLUDED}),
                   "withFort": sum(goes_with_a_fort(u, structures) for u in structures)},
        "teamColouredDoodads": len(manifest.get("teamColoured") or []),
        "movingDoodads": moving,
        "lone": [{"name": u.get("name") or u["type"], "x": u["x"], "y": u["y"]} for u in lone],
    }
    found["calibration"] = plan(found, structures, candidates)
    found["questions"] = questions(found)
    return found


def plan(found: dict, structures: list[dict], candidates: dict) -> dict:
    """What its calibration will measure, and what the data answers instead (calibrate.py)."""
    unknown_side = sorted({k["name"] for k in found["kinds"] if None in k["standing"].values()})
    known_side = sorted({k["name"] for k in found["kinds"]} - set(unknown_side))
    falls = {f"{u['type']}|{u.get('owner')}" for u in structures if str(u["id"]) in candidates}  # (each kind's first copy to pass)
    at_home = {f"{u['type']}|{u.get('owner')}" for u in structures if not u.get("core")}
    looks = [look for k in found["kinds"] for look in k["looks"]]
    by_team = sorted(k["name"] for k in found["kinds"] if k["byTeam"])
    return {
        "measures": [
            f"what moves while paused: {found['tiles']} tiles looked at",
            f"side switch timed: {', '.join(unknown_side)}" if unknown_side else "side switch: none to time (the data says for every kind)",
            f"copy spots: {found['copies']['structures']} structures tried on {found['copies']['spots']} candidate spots",
            f"falls filmed: the first copy to pass of each kind on each team ({len(falls)} kinds)",
            f"standing filmed: {len(looks)} looks, one structure each" + (f" (each team's own: {', '.join(by_team)})" if by_team else ""),
            f"rubble at home: {len(at_home)} kinds",
        ],
        "fromData": [
            f"team colour standing: {', '.join(known_side)}" if known_side else None,
            "no water" if not found["waterCells"] else f"water: {found['waterCells']} cells with its shore, left out of the team colour",
            f"{found['overlaps']} pairs of structures can overlap on screen (their models' reach)",
            "fall lengths: " + ", ".join(f"{k['name']} {'/'.join(f'{s:g}' for s in k['fall'])} s" for k in found["kinds"] if k["fall"])
            if any(k["fall"] for k in found["kinds"]) else None,
        ],
    }


def questions(found: dict) -> list[str]:
    """What the data leaves odd, for the review to answer rather than the tool to guess."""
    asked = []
    for u in found["lone"]:
        asked.append(f"{u['name']} ({u['x']:g}, {u['y']:g}): in no town; which town does it belong to?")
    return asked


def _yes(value) -> str:
    return {True: "yes", False: "no", "while falling": "while falling", None: "unknown"}.get(value, str(value))


def _teams(values: dict) -> str:
    """A value the teams share, or each team's ("blue yes, red no")."""
    if len(set(values.values())) == 1:
        return _yes(next(iter(values.values())))
    return ", ".join(f"{TEAMS.get(team, team)} {_yes(values[team])}" for team in ("order", "chaos") if team in values)


def lines(found: dict) -> list[str]:
    """The survey as a short report."""
    out = [f"{found['map']}: {found['cells'][0]}×{found['cells'][1]} cells, outside the map {found['outside']}"
           + (f", sky layers {', '.join(found['skyModels'].values())}" if found["skyModels"] else "")
           + ("" if found["waterCells"] else ", no water"),
           f"{found['structures']} structures in {found['towns']} towns, {found['outposts']} outposts, {found['camps']} camps; "
           f"{found['teamColouredDoodads']} team-coloured doodads",
           "",
           f"{'structure':<18}{'count':>6}  {'team colour':<22}{'remains':<28}{'fall':>10}  looks"]
    for k in sorted(found["kinds"], key=lambda k: -k["count"]):
        fall = "/".join(f"{s:g}" for s in k["fall"]) + " s" if k["fall"] else "–"
        out.append(f"{k['name']:<18}{k['count']:>6}  {_teams(k['standing']):<22}{_teams(k['remains']):<28}{fall:>10}  "
                   + ("one per team" if k["byTeam"] else "one"))
    out += ["", "The calibration measures:"] + [f"  {m}" for m in found["calibration"]["measures"]]
    out += ["The data answers:"] + [f"  {m}" for m in found["calibration"]["fromData"] if m]
    if found["movingDoodads"]:
        out.append(f"Doodads with particles, ribbons or lights: {', '.join(found['movingDoodads'])}")
    if found["questions"]:
        out += ["Questions for the review:"] + [f"  {q}" for q in found["questions"]]
    return out
