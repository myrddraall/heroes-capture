"""A simulated Windows desktop and Heroes match, for running the capture on Linux.

    python fakegame.py <package dir> <work dir> [capture args...]

Fakes ctypes.windll (process, window and input calls), pydirectinput, mss and subprocess, and a
virtual clock (time.time/time.sleep), then runs heroes_capture.capture.main() on
<work dir>/test-map.json (written by makemanifest.py). The fake game has the map's command box
(capture_script.galaxy): it has the keyboard from the match's start, takes typed text (Unicode or
keys; Enter does nothing while it has the keyboard) and carries out the last complete ";"-ended
command as the text arrives; the chat box, open when the box hasn't the keyboard, only answers
"focus", which gives it back. It draws frames with the status strip the way the script does.
Environment: FAKE_FAULT (focus, wrongmap, silent, crash, menu: the Esc menu open for 6 s, taking
the keys; boxfocus: the box loses the keyboard; broken: the map's script failed to compile, every
interface panel showing) and FAKE_FAULT_AT (virtual seconds),
FAKE_START=map (the map already running), FAKE_BOUNDS, FAKE_HIDDEN (the world hidden until the
map is ready), FAKE_SKY_RATE.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
from menus import broken_interface, game_menu
from PIL import Image

TOOL, WORK = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
ARGS = sys.argv[3:]
W, H = 3440, 1440

# ---------------------------------------------------------------- virtual clock
_now = [1_000_000.0]
_real_sleep = time.sleep
time.time = lambda: _now[0]


def _sleep(s):
    _now[0] += max(0.0, float(s))
    GAME.tick()


time.sleep = _sleep

# ---------------------------------------------------------------- keys
KEYS = {"enter": 28, "space": 57, "backspace": 14, "alt": 56, "esc": 1, "tab": 15, "\\": 43}
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz0123456789.-:,@_"):
    KEYS[ch] = 100 + i
CODE_TO_KEY = {v: k for k, v in KEYS.items()}
pdi = types.ModuleType("pydirectinput")
pdi.KEYBOARD_MAPPING = KEYS
pdi.PAUSE = 0.0
sys.modules["pydirectinput"] = pdi


# ---------------------------------------------------------------- the game
class Game:
    def __init__(self):
        self.state = "menu"  # menu, loading, map, leaving
        self.state_at = _now[0]
        self.chat = None
        self.log = []
        self.bounds = tuple(float(v) for v in os.environ.get("FAKE_BOUNDS", "14,14,50,50").split(","))  # the camera bounds the game applies
        self.cam = (32.0, 32.0)
        self.seq = 0
        self.sky = 1
        self.pending = []  # (time, fn)
        self.map_id = 0
        self.leaving = False
        self.cache = {}
        rng = np.random.default_rng(1)
        small = rng.integers(30, 220, size=(64 * 6, 64 * 6, 3)).astype(np.uint8)
        self.world = np.asarray(Image.fromarray(small).resize((64 * 48, 64 * 48), Image.BILINEAR))  # 48 px per cell
        sky_small = rng.integers(0, 255, size=(64 * 3, 64 * 3, 3)).astype(np.uint8)
        self.sky_world = np.asarray(Image.fromarray(sky_small).resize((64 * 48, 64 * 48), Image.BILINEAR))
        self.clip = 5.0
        self.layer0, self.layer1 = "lightgrey", "mapparallax"
        yy, xx = np.mgrid[0 : 64 * 48 : 8, 0 : 64 * 48 : 8]
        haze = np.clip(0.3 * np.sin(xx / 300.0) * np.cos(yy / 200.0), 0, 0.3).astype(np.float32)
        self.haze_alpha = np.asarray(Image.fromarray((haze * 255).astype(np.uint8)).resize((64 * 48, 64 * 48), Image.BILINEAR), np.float32) / 255
        self.sky_rate = float(os.environ.get("FAKE_SKY_RATE", "0.4"))
        self.menu = self._menu_frame()
        self.box, self.box_focus, self.box_lost = "", True, False  # the map's command box

    def _menu_frame(self):
        frame = np.full((H, W, 3), 60, np.uint8)
        folder = TOOL / "src" / "heroes_capture" / "menu-reference"
        for p in json.loads((folder / "patches.json").read_text())["patches"]:
            t = Image.open(folder / f"{p['name']}.png").convert("RGB")
            pw, ph = int(round(p["w"] * H)), int(round(p["h"] * H))
            x = int(round(p["dx"] * H)) if p["anchor"].endswith("left") else W - int(round(p["dx"] * H)) - pw
            y = int(round(p["dy"] * H)) if p["anchor"].startswith("top") else H - int(round(p["dy"] * H)) - ph
            frame[y : y + ph, x : x + pw] = np.asarray(t.resize((pw, ph), Image.BILINEAR))
        return frame

    def set_state(self, s):
        self.log.append(f"{_now[0] - START:.2f} state {s}")
        self.state, self.state_at = s, _now[0]

    def launch(self, path):
        self.map_id = MAP_ID
        self.box, self.box_focus = "", True  # a new match: the map's box, with the keyboard
        self.set_state("loading")
        self.pending.append((_now[0] + 6.0, lambda: self.set_state("map")))

    def tick(self):
        due = [p for p in self.pending if p[0] <= _now[0]]
        self.pending = [p for p in self.pending if p[0] > _now[0]]
        for _, fn in sorted(due, key=lambda p: p[0]):
            fn()
        if _fault("boxfocus") and not self.box_lost:  # something took the keyboard from the box
            self.box_lost, self.box_focus = True, False

    def box_typed(self, ch):
        """Text into the command box: its last complete command carried out, the box emptied."""
        self.box += ch
        if ";" in self.box:
            text = self.box.rsplit(";", 1)[0].split(";")[-1]
            self.box = ""
            self.command(text)

    def char(self, ch):
        """Unicode text: into the chat box or the command box, never a key to the game."""
        if _menu_open():
            return
        if self.chat is not None:
            self.chat += ch
        elif self.box_focus:
            self.box_typed(ch)

    # match timeline (seconds since the map state began)
    def t(self):
        return _now[0] - self.state_at

    def phase(self):
        if self.leaving:
            return 3
        t = self.t()
        return 0 if t < 3 else 1 if t < 13 else 2

    def key(self, code, up):
        k = CODE_TO_KEY[code]
        if up or _menu_open():
            return  # with the Esc menu open, keys go to it
        if self.chat is None and self.box_focus:
            if k != "enter":  # Enter doesn't open the chat box while the command box has the keyboard
                self.box_typed(" " if k == "space" else k)
            return
        if k == "enter":
            if self.chat is None:
                self.chat = ""
            else:
                text, self.chat = self.chat, None
                if text:
                    self.command(text, chat=True)
        elif self.chat is not None:
            if k == "backspace":
                self.chat = self.chat[:-1]
            else:
                self.chat += " " if k == "space" else k

    def command(self, text, chat=False):
        self.log.append(f"{_now[0] - START:.2f} {'chat' if chat else 'box'} {text!r}")
        if self.state != "map":
            return
        words = text.replace(";", " ").split()
        if not words or (chat and words[0] != "focus"):
            return  # through the chat, the map answers only "focus"
        try:
            seq = int(float(words[-1])) & 255
        except ValueError:
            seq = 0
        cmd = words[0]

        def ack(delay):
            if _fault("silent"):
                return
            self.pending.append((_now[0] + delay, lambda: setattr(self, "seq", seq)))

        if cmd == "tile":
            if len(words) >= 5:
                x, y = float(words[2]), float(words[3])
                l, b, r, t = self.bounds
                self.cam = (min(max(x, l), r), min(max(y, b), t))
                self.sky = 1
                self.clip = 5.0
            ack(0.4)
        elif cmd in ("move", "look"):
            xs = words[2:4] if cmd == "move" else words[1:3]
            self.cam = (float(xs[0]), float(xs[1]))
            ack(0.4)
        elif cmd == "black":
            self.sky = 2
            ack(0.05)
        elif cmd == "sky":
            self.sky = {"lightgrey": 1, "black": 2}.get(words[1], 0)
            if len(words) > 3 and words[2] == "1":
                self.layer1 = words[1]
            else:
                self.layer0 = words[1]
            ack(0.05)
        elif cmd == "unbound":
            self.bounds = (0.0, 0.0, 64.0, 64.0)
            ack(0.05)
        elif cmd == "clip":
            self.clip = float(words[1])
            ack(0.05)
        elif cmd == "hidemap":
            self.clip, self.layer0, self.sky = float(words[1]), "none", 0
            ack(0.05)
        elif cmd in ("clean", "pause", "bgspeed", "refitwait", "hidemap", "note"):
            ack(0.05)
        elif cmd == "focus":
            self.box_focus = True
            ack(0.05)
        elif cmd == "quit":
            self.leaving = True
            self.pending.append((_now[0] + 4.0, lambda: self.set_state("loading")))
            self.pending.append((_now[0] + 12.0, lambda: self.set_state("menu")))

    def bits(self):
        t = self.t()
        cam_x, cam_y = (int(v * 64 + 0.5) for v in self.cam)
        phase = self.phase()
        fields = [(2, 8, self.seq), (10, 10, int(max(0, t - 3))), (20, 15, cam_x), (35, 15, cam_y), (50, 1, 0),
                  (51, 1, int(self.sky == 1)), (52, 1, int(self.sky == 2)), (53, 1, int(t * 4) % 2), (54, 1, int(phase == 2)),
                  (55, 16, self.map_id), (71, 2, phase), (73, 8, min(255, int(max(0, t - 13) // 8))), (81, 1, 0),
                  (82, 4, 5 if phase >= 1 else 0)]
        bits = [0] * 88
        bits[0] = 1
        for start, count, value in fields:
            for i in range(count):
                bits[start + i] = (value >> i) & 1
        bits[86] = sum(bits[2:86]) % 2
        return bits

    def frame(self):
        if self.state == "map" and FAULT == "broken":  # the map's script failed to compile: no strip, every panel showing
            if "broken" not in self.cache:
                self.cache["broken"] = broken_interface(np.full((H, W, 3), 90, np.uint8))
            return self.cache["broken"]
        if self.state == "map" and _menu_open():
            return game_menu(self.match_frame(), "esc")
        return self.match_frame()

    def match_frame(self):
        if self.state == "menu":
            return self.menu
        if self.state == "loading" or (self.state == "map" and self.t() < 2 and not self.leaving):
            return np.full((H, W, 3), 25, np.uint8)
        bits = self.bits()
        key = (self.cam, self.sky, self.clip, self.layer0, self.layer1, tuple(bits))
        if key in self.cache:
            return self.cache[key]
        frame = np.full((H, W, 3), 198 if self.sky == 1 else 0, np.uint8)  # the light sky drawn at about 198
        world = self.world
        cx, cy = self.cam
        if self.clip > 230:  # the map clipped away: the sky shells, moving at sky_rate of the map
            world = self.sky_world
            cx, cy = 32 + (cx - 32) * self.sky_rate, 32 + (cy - 32) * self.sky_rate
        # world at 48 px per cell, camera at the screen's centre, y up
        x0 = int(round(cx * 48 - W / 2))
        y0 = int(round((64 - cy) * 48 - H / 2))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        wx0, wy0 = max(0, x0), max(0, y0)
        wx1, wy1 = min(self.world.shape[1], x0 + W), min(self.world.shape[0], y0 + H)
        if wx1 > wx0 and wy1 > wy0:
            frame[sy0 : sy0 + wy1 - wy0, sx0 : sx0 + wx1 - wx0] = world[wy0:wy1, wx0:wx1]
        if self.clip >= 600:  # only the fixed skybox survives: a purple gradient, or nothing
            frame[:] = 0
            if self.layer0 == "mapsky":
                frame[:] = np.linspace(60, 110, W, dtype=np.float32)[None, :, None] * np.array([1.0, 0.6, 0.9])
        elif self.clip > 230 and self.layer1 != "mapparallax":
            # The keyed copies: background art real or light grey/black, haze kept or not.
            v = self.layer1.replace("parallax", "")
            base = frame.astype(np.float32)
            if v in ("light", "lightbare"):
                base[:] = 198
            elif v == "black":
                base[:] = 0
            if v in ("light", "black"):
                a = np.zeros((H, W), np.float32)
                a[sy0 : sy0 + wy1 - wy0, sx0 : sx0 + wx1 - wx0] = self.haze_alpha[wy0:wy1, wx0:wx1]
                base = base * (1 - a[..., None]) + 200 * a[..., None]
            frame = base.astype(np.uint8)
        if os.environ.get('FAKE_HIDDEN') and self.phase() < 2:
            frame[:] = 0  # an in-game hero selection: the world hidden until the map is ready
        frame[:, :60] = 0  # the backdrop
        for k in range(89):
            logical = k if k < 64 else (None if k == 64 else k - 1)
            v = 0 if logical is None else bits[logical]
            x, y = (k // 64) * 30, (k % 64) * 18
            frame[y : y + 18, x : x + 30] = 255 if v else 0
        if len(self.cache) > 64:
            self.cache.clear()
        self.cache[key] = frame
        return frame


START = _now[0]
GAME = Game()
if os.environ.get("FAKE_START") == "map":
    GAME.state, GAME.state_at = "map", _now[0] - 30
if os.environ.get("FAKE_START") == "leaving":  # an earlier run's match still ending
    GAME.state, GAME.state_at, GAME.leaving = "map", _now[0] - 30, True
    GAME.pending.append((_now[0] + 4.0, lambda: GAME.set_state("loading")))
    GAME.pending.append((_now[0] + 12.0, lambda: (setattr(GAME, "leaving", False), GAME.set_state("menu"))))


# ---------------------------------------------------------------- ctypes.windll
class Fn:
    def __init__(self, f):
        self.f = f

    def __call__(self, *a):
        return self.f(*a)


def _obj(x):
    return getattr(x, "_obj", x)


def _send_input(n, events, size):
    events = _obj(events)
    seq = [events] if not hasattr(events, "__len__") else list(events)
    for e in seq[:n]:
        if e.ki.dwFlags & 0x0004:  # Unicode text
            if not e.ki.dwFlags & 0x0002:
                GAME.char(chr(e.ki.wScan))
        else:
            GAME.key(e.ki.wScan, bool(e.ki.dwFlags & 0x0002))
    return n


def _client_rect(hwnd, rect):
    r = _obj(rect)
    r.left, r.top, r.right, r.bottom = 0, 0, W, H
    return 1


def _set(attr, value):
    def f(hwnd, p):
        setattr(_obj(p), attr, value)
        return 1
    return f


FAULT = os.environ.get("FAKE_FAULT", "")
FAULT_AT = float(os.environ.get("FAKE_FAULT_AT", "1e9"))


def _fault(name):
    return FAULT == name and _now[0] - START >= FAULT_AT


def _menu_open():
    return FAULT == "menu" and FAULT_AT <= _now[0] - START < FAULT_AT + 6


def _crashed():
    return _fault("crash")


FOREGROUND = [100]  # the window in front: the game (100), or another after an alt-tab


def _to_front(*a):
    FOREGROUND[0] = 100
    return 1


def _foreground():
    t = _now[0] - START
    return 200 if FAULT == "focus" and FAULT_AT <= t < FAULT_AT + 3 else FOREGROUND[0]


def _enum_processes(pids, size, used):
    alive = [999] + ([] if _crashed() else [4242])
    for i, p in enumerate(alive):
        pids[i] = p
    _obj(used).value = len(alive) * ctypes.sizeof(ctypes.c_ulong)
    return 1


def _image_name(handle, flags, buf, size):
    if handle == 4242 and _crashed():
        return 0
    buf.value = "D:/Games/Heroes of the Storm/Versions/HeroesOfTheStorm_x64.exe" if handle == 4242 else "C:/Windows/explorer.exe"
    return 1


class User32:
    SendInput = Fn(_send_input)
    GetForegroundWindow = Fn(_foreground)
    GetWindowThreadProcessId = Fn(lambda hwnd, p: setattr(_obj(p), "value", 4242 if hwnd == 100 else 999) or 1)
    GetClientRect = Fn(_client_rect)
    ClientToScreen = Fn(lambda hwnd, p: 1)
    IsWindowVisible = Fn(lambda hwnd: 1)
    EnumWindows = Fn(lambda visit, lp: visit(100, None))
    ShowWindow = Fn(lambda *a: 1)
    SwitchToThisWindow = Fn(_to_front)
    SetForegroundWindow = Fn(_to_front)
    SetCursorPos = Fn(lambda x, y: 1)
    SetProcessDPIAware = Fn(lambda: 1)


class Kernel32:
    OpenProcess = Fn(lambda access, inherit, pid: pid)
    CloseHandle = Fn(lambda h: 1)
    QueryFullProcessImageNameW = Fn(_image_name)
    K32EnumProcesses = Fn(_enum_processes)


class Shcore:
    SetProcessDpiAwareness = Fn(lambda v: 0)


ctypes.windll = types.SimpleNamespace(user32=User32(), kernel32=Kernel32(), shcore=Shcore())
ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE

# ---------------------------------------------------------------- mss
mss = types.ModuleType("mss")


class MSS:
    monitors = [{"left": 0, "top": 0, "width": W, "height": H}] * 2

    def grab(self, region):
        GAME.tick()
        f = GAME.frame()
        return np.dstack([f[:, :, ::-1], np.full(f.shape[:2], 255, np.uint8)])

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


mss.MSS = MSS
sys.modules["mss"] = mss
sys.modules["dxcam"] = None  # not installed: the grabber falls back to mss


# ---------------------------------------------------------------- subprocess
class FakePopen:
    def __init__(self, argv, *a, **k):
        GAME.log.append(f"{_now[0] - START:.2f} popen {Path(argv[0]).name}")
        if "HeroesSwitcher" in str(argv[0]):
            GAME.launch(argv[1])


subprocess.Popen = FakePopen
def _call(argv, *a, **k):
    raise RuntimeError(f"relaunch attempted: {' '.join(Path(x).name if '/' in x else x for x in argv[1:])} (HRS_RECOVERIES={k['env'].get('HRS_RECOVERIES')})")


subprocess.call = _call

# ---------------------------------------------------------------- run
os.environ["HRS_CAPTURE"] = "mss"
import tempfile  # noqa: E402

tempfile.gettempdir = lambda: str(WORK / "tmp")
(WORK / "tmp").mkdir(parents=True, exist_ok=True)
game_dir = WORK / "game"
(game_dir / "Support64").mkdir(parents=True, exist_ok=True)
(game_dir / "Support64" / "HeroesSwitcher_x64.exe").write_text("")
manifest = json.loads((WORK / "test-map.json").read_text())
MAP_ID = manifest["status"]["mapId"]
GAME.map_id = MAP_ID if os.environ.get("FAKE_START") == "map" else 0
if FAULT == "wrongmap":
    MAP_ID += 1
sys.path.insert(0, str(TOOL / "src"))
os.chdir(WORK)
argv = [str(WORK / "test-map.json"), "--game", str(game_dir), *ARGS]
from heroes_capture import capture  # noqa: E402

try:
    try:
        capture.main(argv)
    except capture.Recoverable as e:
        capture.recover(e, argv)
    outcome = "finished"
except SystemExit as e:
    outcome = f"exit {e.code}"
except Exception as e:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    outcome = f"error {type(e).__name__}: {e}"
(WORK / "game-log.txt").write_text("\n".join(GAME.log) + f"\n{outcome}\n")
print(f"\n[harness] {outcome}; virtual time {_now[0] - START:.1f} s")
