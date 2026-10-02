"""Frames of the game's window, and quick comparisons between them."""

import time

import mss
import numpy as np


def _mss():
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()  # mss 10 renamed it


class ScreenGrabber:
    """Frames of a screen region as RGB arrays.

    Through DXGI desktop duplication (the dxcam package) where it works: it hands over only
    frames the compositor has finished. mss copies the screen with GDI, which can catch the
    game part way through presenting a frame (a band of garbled strips, then black, or all
    black) though the screen never shows one; those are retaken (see capture.py's whole_frame),
    but a few still got through as all-black tiles. Desktop duplication is the default for
    tiles (HRS_CAPTURE=mss switches); the crash when leaving a match happens with either. After
    losing access (a display transition) dxcam quietly returns its last frame, so the checks
    that watch the screen change use mss (`duplication=False`)."""

    open_grabbers: list["ScreenGrabber"] = []

    def __init__(self, region: dict, duplication: bool = False):
        ScreenGrabber.open_grabbers.append(self)
        self.region = region
        self.camera = None
        self.box = None
        self.sct = None
        if duplication:
            self._open_duplication(region)
        if self.camera is None:
            self.sct = _mss()

    def _open_duplication(self, region: dict) -> None:
        try:
            import dxcam

            for index in range(8):
                try:
                    # numpy conversion: the default one needs OpenCV.
                    camera = dxcam.create(output_idx=index, output_color="RGB", processor_backend="numpy")
                except Exception:
                    break
                if camera is None:
                    break
                r = camera._output.desc.DesktopCoordinates  # where this monitor is on the desktop
                inside = (
                    r.left <= region["left"] and r.top <= region["top"]
                    and region["left"] + region["width"] <= r.right and region["top"] + region["height"] <= r.bottom
                )
                if inside:
                    x, y = region["left"] - r.left, region["top"] - r.top
                    self.camera, self.box = camera, (x, y, x + region["width"], y + region["height"])
                    break
        except Exception as e:  # not installed, or no DXGI output: fall back
            print(f"(desktop duplication unavailable: {e}; using mss)", flush=True)

    @property
    def method(self) -> str:
        return "desktop duplication" if self.camera is not None else "mss"

    def grab(self) -> np.ndarray:
        if self.camera is not None:
            # A copy of its own (it may be saved in the background while the next is taken), and
            # the last frame again when nothing has changed since; None only before the first.
            for _ in range(25):
                frame = self.camera.grab(region=self.box, copy=True, new_frame_only=False)
                if frame is not None:
                    return frame
                time.sleep(0.02)
            return np.zeros((self.region["height"], self.region["width"], 3), np.uint8)
        return np.asarray(self.sct.grab(self.region), dtype=np.uint8)[:, :, 2::-1]  # BGRA -> RGB

    def __enter__(self) -> "ScreenGrabber":
        return self

    def __exit__(self, *exc) -> None:
        if self in ScreenGrabber.open_grabbers:
            ScreenGrabber.open_grabbers.remove(self)
        if self.sct is not None:
            self.sct.close()
        self.release_duplication()

    def release_duplication(self) -> None:
        if self.camera is not None:
            # Let go of desktop duplication: nothing holds on to the game's screen while it
            # leaves the match.
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None

    @staticmethod
    def monitor(number: int) -> dict:
        """An mss monitor's region (capture.py --monitor)."""
        with _mss() as sct:
            return dict(sct.monitors[number])


def looks_black(frame: np.ndarray) -> bool:
    """A failed grab, or a frame caught mid-transition: black or a single flat colour."""
    sample = frame[::8, ::8]
    return int(sample.max()) < 12 or float(sample.std()) < 3.0


def same_view(a: np.ndarray, b: np.ndarray) -> bool:
    """Nothing on screen changed: the camera didn't move."""
    return float(np.abs(a[::8, ::8].astype(np.int16) - b[::8, ::8].astype(np.int16)).mean()) < 0.5


def changed_share(a: np.ndarray, b: np.ndarray) -> float:
    """The share of the screen that differs between two shots: 32-pixel blocks whose average
    brightness differs by more than 25 levels."""

    def blocks(f: np.ndarray) -> np.ndarray:
        h, w = f.shape[0] // 32 * 32, f.shape[1] // 32 * 32
        g = f[:h, :w].astype(np.float32).mean(axis=2)
        return g.reshape(h // 32, 32, w // 32, 32).mean(axis=(1, 3))

    return float((np.abs(blocks(a) - blocks(b)) > 25).mean())


def view_shift(a: np.ndarray, b: np.ndarray) -> float:
    """How far the view moved between two shots, in screen pixels (phase correlation at a
    quarter scale). Animation (an animated sky, flickering lights, labels) changes pixels but
    doesn't move the view; a camera move does."""

    def prepare(f: np.ndarray) -> np.ndarray:
        g = f[::4, ::4].astype(np.float32).mean(axis=2)
        g -= g.mean()
        return g * np.hanning(g.shape[0])[:, None] * np.hanning(g.shape[1])[None, :]

    pa, pb = prepare(a), prepare(b)
    cross = np.fft.rfft2(pa) * np.conj(np.fft.rfft2(pb))
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.irfft2(cross, s=pa.shape)
    y, x = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    dy, dx = (y - h if y > h // 2 else y), (x - w if x > w // 2 else x)
    return float(np.hypot(dx, dy)) * 4


def disagree(a: np.ndarray, b: np.ndarray) -> bool:
    """Two shots of the same view disagree over more than a sliver of the screen: one was caught
    while the game was part way through drawing it (a band of garbled strips, then black), or
    shows a chat box. Labels and animation change well under 1% of the screen (measured); a
    half-drawn frame about a quarter of it."""
    return changed_share(a, b) > 0.02
