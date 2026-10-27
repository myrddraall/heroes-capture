"""The interface's textures as pictures, with their catalogue (catalogue.py):

    <out>/catalogue.json   {build, textures: {file name: {path, png, width, height, format, alpha, sha1, ...}}}
    <out>/png/<name>.png   each texture at its full size, its transparency kept
    <out>/thumbs/<name>.png  the same at most THUMB pixels a side (what search.py looks at)

A texture whose bytes haven't changed since the last extraction (its SHA-1) isn't decoded again,
so a run after a game update only writes what the update changed; the catalogue is saved every
SAVE_EVERY textures, so a stopped run carries on from there.
"""

import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from heroes_capture.common import ui
from heroes_capture.common.casclib import Storage
from heroes_capture.common.jsonio import read_json, write_json
from heroes_capture.ui.catalogue import catalogue

THUMB = 256
SAVE_EVERY = 500
CATALOGUE = "catalogue.json"


def texture_format(data: bytes) -> str:
    """A DDS's pixel format: its four-character code (DXT1, DXT5, DX10, ...), or RGBA/RGB when it's
    uncompressed."""
    four_cc = data[84:88]
    if four_cc.strip(b"\x00"):
        return four_cc.decode("ascii", errors="replace")
    return "RGBA" if int.from_bytes(data[104:108], "little") else "RGB"  # the alpha mask


def write_pictures(data: bytes, png: Path, thumb: Path) -> dict:
    """Decode a texture and write it and its thumbnail: {width, height, alpha}; {error} when the
    decoder can't read it."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as e:  # noqa: BLE001 - a format the decoder doesn't know is recorded, not fatal
        return {"error": f"{type(e).__name__}: {e}"}
    image = image.convert("RGBA")
    alpha = image.getchannel("A").getextrema()[0] < 255
    if not alpha:
        image = image.convert("RGB")
    image.save(png, "PNG")
    small = image.copy()
    small.thumbnail((THUMB, THUMB), Image.Resampling.LANCZOS)
    small.save(thumb, "PNG")
    return {"width": image.width, "height": image.height, "alpha": alpha}


def extract(storage: Storage, out: Path) -> dict:
    """Extract the interface's textures into `out` (see the module's description); the catalogue."""
    ui.info("reading which textures the interface uses and what the game's data says about them ...")
    entries = catalogue(storage)
    (out / "png").mkdir(parents=True, exist_ok=True)
    (out / "thumbs").mkdir(exist_ok=True)
    before = read_json(out / CATALOGUE, {}).get("textures", {})
    found = {"build": storage.build(), "textures": {}}
    names = sorted(entries)
    reused = 0
    with ThreadPoolExecutor(os.cpu_count()) as pool, ui.bar(len(names), "textures") as advance:
        # CascLib reads on this thread, one file at a time (it isn't safe across threads); the
        # decoding and PNG writing, the slow part, on the others.
        pending = []
        for n, name in enumerate(names, 1):
            entry = entries[name]
            data = storage.read(entry["path"]) or b""
            stem = name.rsplit(".", 1)[0]
            png, thumb = out / "png" / f"{stem}.png", out / "thumbs" / f"{stem}.png"
            sha1 = hashlib.sha1(data).hexdigest()
            entry.update(png=f"png/{stem}.png", thumb=f"thumbs/{stem}.png", sha1=sha1, size=len(data),
                         format=texture_format(data) if data[:4] == b"DDS " else "?")
            old = before.get(name)
            if old and old.get("sha1") == sha1 and "error" not in old and png.exists() and thumb.exists():
                entry.update({k: old[k] for k in ("width", "height", "alpha")})
                found["textures"][name] = entry
                reused += 1
                advance()
            else:
                future = pool.submit(write_pictures, data, png, thumb)
                future.add_done_callback(lambda _: advance())
                pending.append((name, entry, future))
            if n % SAVE_EVERY == 0 or n == len(names):
                for done_name, done_entry, future in pending:
                    done_entry.update(future.result())
                    found["textures"][done_name] = done_entry
                pending = []
                found["textures"] = dict(sorted(found["textures"].items()))
                write_json(out / CATALOGUE, found, indent=1)
                ui.info(f"{n}/{len(names)} textures ({reused} unchanged)")
    kept = {e[key] for e in found["textures"].values() for key in ("png", "thumb")}
    for folder in ("png", "thumbs"):
        for picture in (out / folder).glob("*.png"):
            if f"{folder}/{picture.name}" not in kept:
                picture.unlink()  # a texture the game no longer has, or no longer uses in its interface
    failed = [n for n, e in found["textures"].items() if "error" in e]
    for name in failed:
        ui.warn(f"{name}: {found['textures'][name]['error']}")
    return found
