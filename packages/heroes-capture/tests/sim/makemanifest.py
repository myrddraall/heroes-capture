import json, math, os, sys
from pathlib import Path
work = Path(sys.argv[1]); mode = sys.argv[2] if len(sys.argv) > 2 else "matte"
W, H = (int(v) for v in os.environ.get("FAKE_SCREEN", "1920x1080").split("x"))  # the fake game's screen
b = {"left": 10, "bottom": 10, "right": 54, "top": 54}
vw, vh, keep = W / 48, H / 48, 0.4
def spread(span, step):
    n = max(1, math.ceil(span / step) + 1); return n, (span / (n - 1) if n > 1 else step)
cols, sx = spread(b["right"] - b["left"], vw * keep); rows, sy = spread(b["top"] - b["bottom"], vh * keep)
tiles = [{"index": r * cols + c, "row": r, "col": c, "x": b["left"] + c * sx, "y": b["top"] - r * sy} for r in range(rows) for c in range(cols)]
(work / "test-map.stormmap").write_text("map")
m = {"map": "Test Map", "id": "test-map", "category": "battleground", "stormmap": str(work / "test-map.stormmap"), "structures": "keep",
     "screen": {"w": W, "h": H}, "pxPerCell": 48, "fov": 8.0, "keep": keep, "distance": 214,
     "mapSize": {"width": 64, "height": 64}, "cameraBounds": b, "areas": None, "unbound": False, "cropMargin": 12, "area": b,
     "step": {"x": sx, "y": sy}, "cols": cols, "rows": rows, "tiles": tiles, "keepIntro": False,
     "sky": {"mode": mode, "start": "lightgrey" if mode == "matte" else "black", "colours": ["black", "lightgrey"], "mapSky": {"fixed": "TestSky", "parallax": "TestParallax"}, "keys": True},
     "status": {"mapId": 1234},
     # FAKE_TEAM: doodads in a team's colour (fakegame: discs, Order's blue, red once our player is on Chaos).
     "teamColoured": [[20.0, 40.0], [44.0, 20.0]] if os.environ.get("FAKE_TEAM") else []}
(work / "test-map.json").write_text(json.dumps(m, indent=2))
