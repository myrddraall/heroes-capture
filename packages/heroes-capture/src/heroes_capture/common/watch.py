"""A probe of what the game shows between the shots (HRS_WATCH=1): from the launch until the tiles
begin, a small copy of the screen every WATCH_EVERY seconds, named by the seconds since the watch
began and the stage running (runlog.stage), with the last message logged beside each in
index.txt. Its own GDI copies (mss) on a thread of its own, apart from the shots' grabber."""

import threading
import time
from pathlib import Path

from PIL import Image

from .runlog import current
from .screen import _mss

WATCH_EVERY = 2.0
# A quarter of the screen across: enough to tell what is drawn.
WATCH_SCALE = 4
# Seconds after which it stops whatever the stage (a run that never reaches the tiles).
WATCH_LIMIT = 900.0


def start_watch(region: dict, folder: Path) -> threading.Event:
    """Starts watching `region` into `folder`; set the returned event to stop it."""
    folder.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def run() -> None:
        started = time.time()
        with _mss() as sct, open(folder / "index.txt", "a", encoding="utf-8") as index:
            while not stop.is_set() and current["stage"] != "tiles" and time.time() - started < WATCH_LIMIT:
                at = time.time() - started
                shot = sct.grab(region)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                image = image.resize((image.width // WATCH_SCALE, image.height // WATCH_SCALE))
                stage = current["stage"].replace(" ", "-").replace("(", "").replace(")", "").replace(",", "") or "none"
                name = f"{at:06.1f}-{stage}.jpg"
                image.save(folder / name, quality=80)
                index.write(f"{name}  {current['message']}\n")
                index.flush()
                stop.wait(max(0.0, started + at + WATCH_EVERY - time.time()))

    threading.Thread(target=run, name="watch", daemon=True).start()
    return stop
