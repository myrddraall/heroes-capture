"""The interface's textures (extract.py's) searched by description.

Each texture's thumbnail goes through the image half of an image-text model (SigLIP 2, by
OpenCLIP, on the CPU: in bfloat16 where the processor has it, twice as fast for the same vectors) once: squared up over mid grey, so a black mask and a white glow both show,
and a long bar isn't cropped. The vectors are kept in <out>/index-<model>.npz with each texture's
SHA-1, so after a game update only what it changed is looked at again. A description goes through
the text half; the textures are ranked by how near their picture is to it, with WORD_WEIGHT added
in proportion to how many of its words appear in what the game's data calls the texture (its file
and asset names, the layout frames that draw it, the entries that show it and their English names).

The best are listed and shown on a contact sheet (<out>/searches/<description>.png), numbered as
listed, over a checkerboard where they're transparent.
"""

import contextlib
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from heroes_capture.common import ui
from heroes_capture.common.game_data import slug
from heroes_capture.common.jsonio import read_json
from heroes_capture.ui.extract import CATALOGUE

MODEL, PRETRAINED = "ViT-B-16-SigLIP2-256", "webli"
GREY = (128, 128, 128)
BATCH = 64
SAVE_EVERY = 2000  # textures looked at between saves of the index
WORD_WEIGHT = 0.05
STOP_WORDS = {"a", "an", "and", "the", "of", "with", "for", "in", "on", "to", "or", "that", "like", "some", "ui"}
TILE, COLUMNS, LABEL = 200, 8, 30  # the contact sheet's pictures, per row, and the label under each


class Model:
    """The image-text model, loaded once (its weights downloaded the first time)."""

    def __init__(self):
        try:
            import open_clip
            import torch
        except ImportError:
            raise SystemExit("ui search needs the search extra: uv sync --extra search") from None
        # torch's own thread count, one per core: one per hardware thread ran ten times slower.
        self.torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(MODEL)
        self.bfloat16 = torch.ops.mkldnn._is_mkldnn_bf16_supported()

    def _running(self):
        """The model's mode: no gradients, and bfloat16 where the processor has it."""
        stack = contextlib.ExitStack()
        stack.enter_context(self.torch.inference_mode())
        if self.bfloat16:
            stack.enter_context(self.torch.autocast("cpu", dtype=self.torch.bfloat16))
        return stack

    def pictures(self, images: list[Image.Image]) -> np.ndarray:
        with self._running():
            batch = self.torch.stack([self.preprocess(image) for image in images])
            vectors = self.model.encode_image(batch).float()
        return (vectors / vectors.norm(dim=-1, keepdim=True)).numpy()

    def text(self, description: str) -> np.ndarray:
        with self._running():
            vector = self.model.encode_text(self.tokenizer([description]))[0].float()
        return (vector / vector.norm()).numpy()


def squared(path: Path) -> Image.Image:
    """A thumbnail over mid grey, in the middle of a square."""
    picture = Image.open(path).convert("RGBA")
    side = max(picture.size)
    square = Image.new("RGBA", (side, side), (*GREY, 255))
    square.alpha_composite(picture, ((side - picture.width) // 2, (side - picture.height) // 2))
    return square.convert("RGB")


def index_path(out: Path) -> Path:
    return out / f"index-{slug(MODEL)}.npz"


def update_index(out: Path, textures: dict[str, dict], model: Model) -> tuple[list[str], np.ndarray]:
    """The textures' names and their pictures' vectors, those new or changed since the index was
    last saved looked at now."""
    path = index_path(out)
    known: dict[tuple[str, str], np.ndarray] = {}
    if path.exists():
        saved = np.load(path)
        known = {(n, s): v for n, s, v in zip(saved["names"], saved["sha1s"], saved["vectors"])}
    wanted = [(n, e["sha1"]) for n, e in textures.items() if "error" not in e]
    missing = [key for key in wanted if key not in known]

    def save() -> None:
        keys = [key for key in wanted if key in known]
        np.savez(path, names=np.array([n for n, _ in keys]), sha1s=np.array([s for _, s in keys]),
                 vectors=np.stack([known[key] for key in keys]).astype(np.float16))

    if missing:
        ui.info(f"looking at {len(missing)} textures' pictures (once: they're kept in {path}) ...")
        with ui.bar(len(missing), "pictures") as advance:
            for start in range(0, len(missing), BATCH):
                keys = missing[start:start + BATCH]
                vectors = model.pictures([squared(out / textures[n]["thumb"]) for n, _ in keys])
                known.update(zip(keys, vectors))
                advance(len(keys))
                if (start // BATCH + 1) % (SAVE_EVERY // BATCH) == 0:
                    save()
        save()
    elif len(known) != len(wanted):
        save()  # textures the game no longer has, dropped
    return [n for n, _ in wanted], np.stack([known[key] for key in wanted]).astype(np.float32)


def words(name: str, entry: dict) -> str:
    """What the game's data calls a texture, as one lower-case text to look for words in."""
    parts = [name, *entry.get("aliases", [])]
    parts += [f"{u['layout']} {u['frame']} {u.get('type', '')}" for u in entry.get("layouts", [])]
    parts += [f"{u['entry']} {u.get('name', '')}" for u in entry.get("data", [])]
    return " ".join(parts).lower()


def query_words(description: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", description.lower()) if len(w) > 2 and w not in STOP_WORDS]


def rank(description: str, names: list[str], vectors: np.ndarray, textures: dict[str, dict], model: Model) -> list[tuple[str, float]]:
    """Every texture with its score for the description, best first."""
    scores = vectors @ model.text(description)
    wanted = query_words(description)
    if wanted:
        for i, name in enumerate(names):
            text = words(name, textures[name])
            scores[i] += WORD_WEIGHT * sum(w in text for w in wanted) / len(wanted)
    order = np.argsort(-scores)
    return [(names[i], float(scores[i])) for i in order]


def checkerboard(size: tuple[int, int], square: int = 8) -> Image.Image:
    board = Image.new("RGB", size, (48, 48, 48))
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], square):
        for x in range((y // square % 2) * square, size[0], 2 * square):
            draw.rectangle((x, y, x + square - 1, y + square - 1), fill=(72, 72, 72))
    return board


def contact_sheet(found: list[tuple[str, float]], textures: dict[str, dict], out: Path, path: Path) -> None:
    """The found textures' thumbnails in a grid, each numbered as listed, with its name and size."""
    rows = -(-len(found) // COLUMNS)
    sheet = Image.new("RGB", (COLUMNS * TILE, rows * (TILE + LABEL)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(11)
    for i, (name, _) in enumerate(found):
        entry = textures[name]
        x, y = (i % COLUMNS) * TILE, (i // COLUMNS) * (TILE + LABEL)
        picture = Image.open(out / entry["thumb"]).convert("RGBA")
        picture.thumbnail((TILE - 8, TILE - 8))
        tile = checkerboard(picture.size).convert("RGBA")
        tile.alpha_composite(picture)
        sheet.paste(tile.convert("RGB"), (x + (TILE - picture.width) // 2, y + 4 + (TILE - 8 - picture.height) // 2))
        stem = name.rsplit(".", 1)[0]
        draw.text((x + 4, y + TILE), f"{i + 1}. {stem[-28:]}", fill=(230, 230, 230), font=font)
        draw.text((x + 4, y + TILE + 14), f"{entry['width']}x{entry['height']}", fill=(150, 150, 150), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def where(entry: dict) -> str:
    """The texture's most telling use: the entry's English name, the frame, or its asset name."""
    for use in entry.get("data", []):
        if use.get("name"):
            return f"{use['name']} ({use['entry']})"
    if entry.get("layouts"):
        use = entry["layouts"][0]
        return f"{use['layout']}: {use['frame']}" + (f" [{use['type']}]" if use.get("type") else "")
    return entry.get("aliases", [""])[0]


def search(out: Path, description: str, count: int) -> tuple[list[tuple[str, float]], dict[str, dict], Path]:
    """The `count` textures that best fit the description, their catalogue, and their contact sheet."""
    catalogue = read_json(out / CATALOGUE, None)
    if catalogue is None:
        raise SystemExit(f"no interface textures in {out}: extract them first (heroes-capture ui extract)")
    textures = catalogue["textures"]
    model = Model()
    names, vectors = update_index(out, textures, model)
    found = rank(description, names, vectors, textures, model)[:count]
    sheet = out / "searches" / f"{slug(description)[:80]}.png"
    contact_sheet(found, textures, out, sheet)
    return found, textures, sheet
