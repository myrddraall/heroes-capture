"""A battleground calibration's review (VERIFICATION.md): what calibrate.py measured, with what can be
settled from the numbers settled (review_data), and the rest as questions on a page
(write_review: index.html, its pictures and answers.json, published as an artifact whose Submit
saves the answers into answers.json). apply_review turns the measurements and the answers into
the map's reviewed calibration, which an elements render is prepared with.

Settled from the numbers: a copy spot that passed with room to spare (CLEAR), a structure type
whose structures all agree on whether they change with the side. Asked: each filmed fall's best
moment (the review's frame, preset to the frame its fall settled at, or Battlefield of
Eternity's wait for that kind), each copy spot that passed narrowly, each structure type whose
structures disagree on changing with the side. Shown: each kind's rubble at home with the scene
hidden and shown, beside its copy's, the darkest first; what still moves while paused, grouped by
what it is, with what the data report says it is made of; a note can go with each."""

import hashlib
import json
import math
from html import escape
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from heroes_capture.categories.battleground.elements import FALL_WAITS, LIGHT_WAIT, STRUCTURE_RADIUS, rubble_radius

# A passing copy spot is settled without asking when it passed by this much: drawn over at least
# CLEAR_DRAWN of its structure's pixels, its brightness within CLEAR_BRIGHTNESS, its colour cast
# within CLEAR_CAST, at most CLEAR_DARK more of it black.
CLEAR_DRAWN, CLEAR_BRIGHTNESS, CLEAR_CAST, CLEAR_DARK = 0.7, 0.15, 15.0, 0.08
# Pictures of what moves while paused shown per group at most.
MOTION_PICTURES = 6
PAGE = Path(__file__).with_name("calibration_review.html")
# A film with fewer pixels than this off the sky's colour, in all its frames, is empty.
EMPTY_PIXELS = 50


def margins(own: dict, copy: dict) -> dict:
    """How a copy compared with its structure: drawn (share of the structure's pixels), brightness
    (ratio), cast (largest channel difference, brightness evened out), dark (more black)."""
    ratio = statistics.fmean(copy["mean"]) / max(statistics.fmean(own["mean"]), 1.0)
    return {"drawn": round(copy["drawn"] / own["drawn"], 3) if own["drawn"] else 1.0, "brightness": round(ratio, 3),
            "cast": round(max(abs(c / ratio - s) for c, s in zip(copy["mean"], own["mean"])), 1),
            "dark": round(copy["dark"] - own["dark"], 3)}


def clear(m: dict) -> bool:
    """Whether a passing copy spot passed with room to spare (CLEAR_*)."""
    return (m["drawn"] >= CLEAR_DRAWN and abs(m["brightness"] - 1) <= CLEAR_BRIGHTNESS and m["cast"] <= CLEAR_CAST
            and m["dark"] <= CLEAR_DARK)


def preset_frame(fall: dict, table_wait: float | None) -> int:
    """The film frame a fall's question starts on: Battlefield of Eternity's wait for the kind,
    else the moment it settled, else the middle."""
    seconds = table_wait if table_wait is not None else fall.get("settle")
    if seconds is None:
        return fall["frames"] // 2
    return max(0, min(fall["frames"] - 1, round(seconds / fall["step"])))


def review_data(calibration: dict) -> dict:
    """The review: what was settled and what is asked (see the module's note)."""
    def table_wait(kind: str) -> float | None:
        unit, owner = kind.split("|")
        return next((w for prefix, suffix, o, w in FALL_WAITS if unit.startswith(prefix) and unit.endswith(suffix) and o == owner), None)

    # (A film without columns is an earlier one: its frames in one row.)
    standing = [{"kind": kind, "type": kind, "owner": "", "film": f["film"], "frames": f["frames"], "columns": f.get("columns", f["frames"]), "seconds": f.get("seconds"),
                 "step": f["step"], "preset": max(0, min(f["frames"] - 1, round(LIGHT_WAIT / f["step"])))}
                for kind, f in sorted((calibration.get("standing") or {}).items()) if f.get("film")]
    falls = [{"kind": kind, "type": kind.split("|")[0], "owner": kind.split("|")[1], "film": f["film"], "frames": f["frames"], "columns": f.get("columns", f["frames"]),
              "step": f["step"], "settle": f.get("settle"), "table": table_wait(kind), "preset": preset_frame(f, table_wait(kind))}
             for kind, f in sorted((calibration.get("falls") or {}).items()) if f.get("film")]
    spots, settled_spots = [], 0
    for t in calibration.get("tries") or []:
        if t.get("failed") is not None or not t.get("copy"):
            continue
        m = margins(t["own"], t["copy"])
        if clear(m):
            settled_spots += 1
        else:
            spots.append({"id": str(t["id"]), "type": t["type"], "x": t["x"], "y": t["y"], "margins": m,
                          "standing": f"structure-{t['id']}.png", "copy": t.get("standing"), "rubble": t.get("rubble")})
    by_type = defaultdict(list)
    for sid, s in (calibration.get("structures") or {}).items():
        by_type[s["type"]].append((sid, s))
    colour = [{"type": kind, "changes": [sid for sid, s in members if s["changes"]], "unchanged": [sid for sid, s in members if not s["changes"]],
               "pictures": [s["standing"] for _, s in members][:MOTION_PICTURES]}
              for kind, members in sorted(by_type.items()) if len({s["changes"] for _, s in members}) > 1]
    report = calibration.get("report") or {}
    groups = defaultdict(list)
    for patch in calibration.get("motion") or []:
        what = patch["what"]
        groups[f"{what['kind']}:{what.get('type', '')}"].append(patch)
    motion = []
    for key, patches in sorted(groups.items(), key=lambda g: -sum(p["pixels"] for p in g[1])):
        kind, kind_type = key.split(":", 1)
        parts = (report.get("doodads") or {}).get(kind_type) if kind == "doodad" else None
        models = []
        if kind == "structure":
            models = [{"model": m, **(report.get("models") or {}).get(m, {})} for m in _tree_models((report.get("structures") or {}).get(kind_type, []))]
        motion.append({"key": key, "kind": kind, "type": kind_type, "count": len(patches), "parts": parts,
                       "models": [m for m in models if m.get("particles") or m.get("ribbons") or m.get("lights")],
                       "pictures": [p["picture"] for p in sorted(patches, key=lambda p: -p["pixels"]) if p.get("picture")][:MOTION_PICTURES],
                       "cells": [[p["x"], p["y"]] for p in patches]})
    structures = calibration.get("structures") or {}
    moving_structures = [{"id": sid, "type": s["type"], "moves": s["moves"], "picture": s.get("moving")}
                         for sid, s in structures.items() if s.get("moving")]
    homes = [{"kind": kind, "type": kind.split("|")[0], "owner": kind.split("|")[1], "hidden": h.get("hidden"), "shown": h.get("shown"),
              "copy": h.get("copy"), "ratio": h.get("ratio"), "copyRatio": h.get("copyRatio"), "wait": h.get("wait")}
             for kind, h in sorted((calibration.get("rubbleAtHome") or {}).items(), key=lambda kh: kh[1].get("ratio") or 1.0)]
    copied = {str(t["id"]) for t in calibration.get("tries") or []}
    return {
        "map": calibration["map"], "tool": calibration.get("tool"), "version": calibration.get("version"),
        "settled": {"spots": settled_spots, "noSpot": sorted(copied - set(calibration.get("copySpots") or {})),
                    "failedSpots": sum(1 for t in calibration.get("tries") or [] if t.get("failed")),
                    "changing": sum(1 for s in structures.values() if s["changes"]), "structures": len(structures),
                    "colourTypes": len(by_type) - len(colour)},
        "falls": falls, "standing": standing, "spots": spots, "colour": colour, "motion": motion, "movingStructures": moving_structures[:MOTION_PICTURES * 3],
        "homes": homes,
    }


def _tree_models(tree: list[dict]) -> list[str]:
    return list(dict.fromkeys(m for n in tree for m in n["models"] + _tree_models(n["children"])))


def pictures_of(review: dict) -> list[str]:
    """Every picture the review shows."""
    names = [f["film"] for f in review["falls"] + review["standing"]]
    names += [p for s in review["spots"] for p in (s["standing"], s["copy"], s["rubble"]) if p]
    names += [p for c in review["colour"] for p in c["pictures"]]
    return list(dict.fromkeys(names))


def empty_film(strip: Path) -> bool:
    """Whether a film shows nothing but the sky (a copy that fell out of its frame): no frame has
    EMPTY_PIXELS pixels off the sky's colour."""
    import numpy as np

    a = np.asarray(Image.open(strip).convert("RGB")).astype(np.int16)
    sky = np.median(a.reshape(-1, 3), axis=0)
    return int((np.abs(a - sky).max(axis=2) > 20).sum()) < EMPTY_PIXELS


# A copy whose rubble covers less than this share of its circle left none (calibrate.RUBBLE_DRAWN_LEAST).
RUBBLE_DRAWN_LEAST = 0.001
# A rubble circle reaches this many times the furthest debris measured for its structure type (either
# team, any picture): each fall lands differently (Dragon Shire's forts' and towers' came out up to
# 15% past one fall's reach plus a cell).
RUBBLE_ROOM = 1.3
# A pixel this far from the sky's colour (its largest channel) is debris.
DEBRIS_DIFF = 30
# An earlier calibration's pixels a cell on the screen (before it recorded its own: the capture's).
PX_PER_CELL = 48.0


def debris_reach(picture: np.ndarray, centre: tuple[float, float] | None) -> tuple[float, float]:
    """How far (pixels) a picture's debris reaches from the structure's place (`centre`; None: the
    middle), over the sky (its border's median colour, the scene hidden); and how far the picture
    itself reaches from there (to its nearest edge)."""
    h, w = picture.shape[:2]
    cx, cy = centre if centre else (w / 2, h / 2)
    rgb = picture[..., :3].astype(np.int16)
    sky = np.median(np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]), axis=0)
    ys, xs = np.nonzero(np.abs(rgb - sky).max(axis=2) > DEBRIS_DIFF)
    reach = float(np.hypot(xs - cx, ys - cy).max()) if len(xs) else 0.0
    return reach, float(min(cx, cy, w - 1 - cx, h - 1 - cy))


def rubble_radii(run: Path, calibration: dict, answers: dict) -> tuple[dict[str, float], list[str]]:
    """Each kind's rubble circle (cells), measured: its debris at the moment picked in the review,
    in its fall film; a kind without one (a keep, a fort: never copied), in its rubble shot at home
    (at its wait); the furthest for its structure type, RUBBLE_ROOM times. And the kinds whose
    debris reached the edge of what was shot (their circle may be short: an earlier calibration
    shot only the old circle; filmed wider now)."""
    ppc = calibration.get("pxPerCell") or PX_PER_CELL
    reach, edged = {}, []

    def keep(kind: str, reach_px: float, limit_px: float, cells_per_px: float) -> None:
        reach[kind] = reach_px * cells_per_px
        if reach_px >= 0.97 * limit_px:
            edged.append(kind)

    for kind, f in (calibration.get("falls") or {}).items():
        picked = (answers.get("falls") or {}).get(kind)
        source = run / "calibration" / str(f.get("film"))
        if picked is None or not f.get("film") or not source.exists():
            continue
        strip = np.asarray(Image.open(source).convert("RGB"))
        columns = f.get("columns", f["frames"])
        size = strip.shape[0] // math.ceil(f["frames"] / columns)
        k = min(int(picked), f["frames"] - 1)
        frame = strip[k // columns * size:(k // columns + 1) * size, k % columns * size:(k % columns + 1) * size]
        if f.get("cellsPerPixel"):
            cells = f["cellsPerPixel"]
        else:  # an earlier film: cut to the kind's old circle (circle_box), then saved `size` square
            old = rubble_radius({"type": kind.split("|")[0], "radius": STRUCTURE_RADIUS})
            cells = min((calibration.get("screen") or {}).get("h", 1e9), 2 * old * ppc + 3) / size / ppc
        found, limit = debris_reach(frame, f.get("centre"))
        if found:
            keep(kind, found, limit, cells)
    for kind, h in (calibration.get("rubbleAtHome") or {}).items():
        source = run / "calibration" / str(h.get("hidden"))
        if kind in reach or not h.get("hidden") or not source.exists():
            continue
        found, limit = debris_reach(np.asarray(Image.open(source).convert("RGB")), h.get("centre"))
        if found:
            keep(kind, found, limit, h.get("cellsPerPixel") or 1 / ppc)
    furthest: dict[str, float] = {}
    for kind, cells in reach.items():
        furthest[kind.split("|")[0]] = max(furthest.get(kind.split("|")[0], 0.0), cells)
    return {kind: round(furthest[kind.split("|")[0]] * RUBBLE_ROOM, 2) for kind in reach}, sorted(edged)


def empty_kinds(run: Path, review: dict) -> tuple[list[str], list[str]]:
    """The kinds whose fall film, and whose standing film, shows nothing (empty_film): the review
    doesn't ask about them."""
    def empty(f: dict) -> bool:
        return (run / "calibration" / f["film"]).exists() and empty_film(run / "calibration" / f["film"])
    return [f["kind"] for f in review["falls"] if empty(f)], [f["kind"] for f in review["standing"] if empty(f)]


def write_review(run: Path, out: Path) -> Path:
    """The review page for a calibration run's folder (its calibration.json and calibration/
    pictures) in `out`: index.html, img/ (PNGs as WebP) and an empty answers.json. Returns the
    page."""
    calibration = json.loads((run / "calibration.json").read_text(encoding="utf-8"))
    review = review_data(calibration)
    # A film that shows nothing isn't asked about: its kind keeps Battlefield of Eternity's wait.
    review["emptyFalls"], review["emptyStanding"] = empty_kinds(run, review)
    review["falls"] = [f for f in review["falls"] if f["kind"] not in review["emptyFalls"]]
    review["standing"] = [f for f in review["standing"] if f["kind"] not in review["emptyStanding"]]
    images = out / "img"
    images.mkdir(parents=True, exist_ok=True)
    renamed = {}
    for name in pictures_of(review):
        source = run / "calibration" / name
        if not source.exists():
            continue
        if source.suffix == ".png":
            target = Path(name).with_suffix(".webp").name
            Image.open(source).convert("RGB").save(images / target, quality=82, method=4)
        else:
            target = name
            shutil.copyfile(source, images / target)
        # Its address carries its content's hash: a page published again over the same names never
        # shows a picture a browser kept from before.
        renamed[name] = f"img/{target}?v={hashlib.sha1((images / target).read_bytes()).hexdigest()[:10]}"
    review["images"] = renamed
    # Each film's frame size as saved (its picture's rows of `columns`): the page never shows a
    # frame bigger than that.
    for film in review["falls"] + review["standing"]:
        source = run / "calibration" / film["film"]
        if source.exists():
            film["size"] = Image.open(source).size[1] // math.ceil(film["frames"] / max(1, film["columns"]))
    data = json.dumps(review, separators=(",", ":")).replace("</", "<\\/")
    page = out / "index.html"
    html = PAGE.read_text(encoding="utf-8").replace("/*REVIEW*/null", data)
    html = html.replace("<title>Calibration Review</title>", f"<title>{escape(review['map'])} Calibration</title>", 1)
    page.write_text(html, encoding="utf-8", newline="\n")
    if not (out / "answers.json").exists():
        (out / "answers.json").write_text('{"submitted":false}\n', encoding="utf-8", newline="\n")
    return page


def apply_review(calibration: dict, answers: dict, empty: tuple[list[str], list[str]] = ([], [])) -> dict:
    """The map's reviewed calibration: the copy spots that passed, less those the review rejected;
    whether each structure changes with the side (a disputed type as the review answered); each
    filmed kind's fall wait as picked; the review's notes. A kind whose film was empty (`empty`:
    empty_kinds) wasn't asked about: an answer for it (left from an earlier review) is ignored, and
    it keeps Battlefield of Eternity's wait."""
    review = review_data(calibration)
    review["falls"] = [f for f in review["falls"] if f["kind"] not in empty[0]]
    review["standing"] = [f for f in review["standing"] if f["kind"] not in empty[1]]
    rejected = {sid for sid, verdict in (answers.get("spots") or {}).items() if verdict == "reject"}
    # (A copy that fell to nothing: its spot passed in an earlier calibration, which didn't check.)
    empty = {str(t["id"]) for t in calibration.get("tries") or [] if t.get("failed") is None
             and t.get("rubbleLook") and t["rubbleLook"]["drawn"] < RUBBLE_DRAWN_LEAST}
    spots = {sid: spot for sid, spot in (calibration.get("copySpots") or {}).items() if sid not in rejected | empty}
    disputed = {c["type"]: answers.get("colour", {}).get(c["type"]) for c in review["colour"]}
    structures = {sid: {"type": s["type"], "changes": disputed[s["type"]] if disputed.get(s["type"]) is not None else s["changes"]}
                  for sid, s in (calibration.get("structures") or {}).items()}
    falls = {f["kind"]: f for f in review["falls"]}
    waits = {}
    for kind, frame in (answers.get("falls") or {}).items():
        if kind in falls and frame is not None:
            waits[kind] = round(min(int(frame), falls[kind]["frames"] - 1) * falls[kind]["step"], 2)
    standing = {f["kind"]: f for f in review["standing"]}
    stand_waits = {kind: round(min(int(frame), standing[kind]["frames"] - 1) * standing[kind]["step"], 2)
                   for kind, frame in (answers.get("standing") or {}).items() if kind in standing and frame is not None}
    # Each kind's model as filmed (by type, and team where the teams' differ), over the types'.
    stand_models = {**((calibration.get("report") or {}).get("standModels") or {}),
                    **{kind: film["model"] for kind, film in (calibration.get("standing") or {}).items() if film.get("model")}}
    return {"standWaits": stand_waits, "standModels": stand_models, "version": calibration["version"], "stages": calibration.get("stages") or {}, "map": calibration["map"], "tool": calibration.get("tool"),
            "cameraBounds": calibration.get("cameraBounds"), "copySpots": spots, "structures": structures, "fallWaits": waits,
            "reviewed": {"at": answers.get("at"), "notes": answers.get("notes", ""), "motion": answers.get("motion") or {},
                         "rejectedSpots": sorted(rejected), "unanswered": sorted(set(falls) - set(waits))}}
