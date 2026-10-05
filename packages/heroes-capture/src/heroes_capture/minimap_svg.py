"""The map's custom minimap (CustomMiniMap.dds, PACK.md: Images) redrawn as an SVG made of the
shapes it is drawn with, so it stays sharp at any size and its parts can be told apart:

    #outline       one path down the middle of the map's light outline, round the whole map;
                   at each nexus the circle's own arc, running on into the cut-ins where the
                   circle meets the body, and from each cut-in's tip one curve out to the border
    #out-stroke    the black: the outline stroked wide behind the main shape, so the half outside
                   it shows, and (#cut-ins) the black in each cut-in, narrowing to its tip
    #body          the main shape: the outline filled with the body's colour and stroked with the
                   light line
    #fog           the soft lighter areas, one blurred shape running on under the parts
    g#camps        the darker spots (camps), each a shape
    g#parts        the light parts, each a shape in its own colour
    g#nexus-<n>    each nexus's swirl

Classes, for restyling (a CSS rule beats the colours written in): out-stroke (stroke, and fill for
the cut-ins), bg (the main shape's fill), inner-stroke (its stroke), fog, terrain (the light
parts), camps, nexus (the swirls); all fills but those two strokes.

The outline's strokes don't scale (vector-effect: non-scaling-stroke, on the path itself, as the
property isn't inherited through <use>): drawn at any size, the black edge and the light line keep
the widths they have at the picture's own size, in screen pixels. That holds where the SVG is part
of the page; drawn as an <img>, a browser scales the whole picture, strokes and all.

Everything is measured from the picture: the widths and colours of the edge's bands, the nexus
circles, where each cut-in's light ends, the fog's level and softness. The SVG's units are the
picture's pixels (the viewBox is its size).
"""

import math

import numpy as np
from PIL import Image
from scipy import ndimage

UP = 4  # supersampling for the distance fields


class MinimapError(ValueError):
    """The picture isn't a custom minimap this can redraw (no edge bands to measure)."""


# ------------------------------------------------------------------------------------------------
# Geometry
# ------------------------------------------------------------------------------------------------


def cross(u, v):
    """The 2D cross product (z of the 3D one), elementwise."""
    u, v = np.asarray(u), np.asarray(v)
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


_EDGES_OF = {  # which cell edges a contour crosses, by corner case, oriented (inside on the left)
    1: [("l", "b")], 2: [("b", "r")], 3: [("l", "r")], 4: [("r", "t")], 6: [("b", "t")], 7: [("l", "t")],
    8: [("t", "l")], 9: [("t", "b")], 11: [("t", "r")], 12: [("r", "l")], 13: [("r", "b")], 14: [("b", "l")],
}


def contours(field: np.ndarray, level: float) -> list[np.ndarray]:
    """Closed iso-contours of `field` at `level` (marching squares, linear interpolation), as
    arrays of (x, y) in pixel units with pixel (i, j)'s centre at (j + 0.5, i + 0.5). The field is
    padded with values below the level, so every contour closes."""
    f = np.pad(field.astype(float), 1, constant_values=level - 1e6)
    # A value on (or a hair from) the level would put crossings from different edges on the same
    # pixel centre, and the loops would join up wrongly there: nudge such values under it.
    f[np.abs(f - level) < 1e-3] = level - 1e-3
    above = f > level

    def point(y0, x0, y1, x1):
        # Each edge interpolated the same way from both cells that share it, so the two ends are
        # the same floats and the segments link up (from opposite ends they can differ).
        if (y1, x1) < (y0, x0):
            y0, x0, y1, x1 = y1, x1, y0, x0
        v0, v1 = f[y0, x0], f[y1, x1]
        t = (level - v0) / (v1 - v0)
        return (x0 + t * (x1 - x0) - 0.5, y0 + t * (y1 - y0) - 0.5)  # back to unpadded coordinates

    tl, tr, br, bl = above[:-1, :-1], above[:-1, 1:], above[1:, 1:], above[1:, :-1]
    case = tl * 8 + tr * 4 + br * 2 + bl * 1
    nxt = {}
    for y, x in zip(*np.nonzero((case != 0) & (case != 15))):
        c = case[y, x]
        edge = {"t": (y, x, y, x + 1), "r": (y, x + 1, y + 1, x + 1), "b": (y + 1, x + 1, y + 1, x), "l": (y + 1, x, y, x)}
        if c in (5, 10):  # a saddle: the cell's middle decides which corners join
            centre = (f[y, x] + f[y, x + 1] + f[y + 1, x] + f[y + 1, x + 1]) / 4 > level
            if c == 5:
                pairs = [("l", "t"), ("r", "b")] if centre else [("l", "b"), ("r", "t")]
            else:
                pairs = [("t", "r"), ("b", "l")] if centre else [("t", "l"), ("b", "r")]
        else:
            pairs = _EDGES_OF[c]
        for a, b in pairs:
            nxt[point(*edge[a])] = point(*edge[b])
    loops = []
    while nxt:
        start, cur = next(iter(nxt.items()))
        loop = [start]
        del nxt[start]
        while cur != start and cur in nxt:
            loop.append(cur)
            cur = nxt.pop(cur)
        if len(loop) >= 3:
            if cur != start:
                raise MinimapError("a contour didn't close")
            loops.append(np.array(loop))
    return loops


def simplify(points: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on a closed polygon."""
    far = int(np.argmax(np.hypot(*(points - points[0]).T)))
    first = open_simplify(points[: far + 1], tol)
    second = open_simplify(np.vstack([points[far:], points[:1]]), tol)
    return np.vstack([first[:-1], second[:-1]])


def open_simplify(points: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on an open polyline (its ends kept)."""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    n = np.hypot(*(b - a))
    d = np.abs(cross(b - a, points - a)) / n if n else np.hypot(*(points - a).T)
    i = int(np.argmax(d))
    if d[i] <= tol:
        return np.array([a, b])
    return np.vstack([open_simplify(points[: i + 1], tol)[:-1], open_simplify(points[i:], tol)])


def _fmt(p) -> str:
    return f"{p[0]:.1f} {p[1]:.1f}"


def _curves(points: np.ndarray, closed: bool, corner_deg: float = 40.0) -> list[str]:
    """Path commands through `points` from the first (the current point): cubic Béziers
    (Catmull-Rom) where the line bends gently, straight lines into and out of sharper corners."""
    n = len(points)

    def at(i):
        return points[i % n] if closed else points[min(max(i, 0), n - 1)]

    def turn(i):
        if not closed and (i <= 0 or i >= n - 1):
            return 0.0
        u, v = at(i) - at(i - 1), at(i + 1) - at(i)
        return abs(math.degrees(math.atan2(cross(u, v), np.dot(u, v))))

    corner = [turn(i) >= corner_deg for i in range(n)]
    out = []
    for i in range(n if closed else n - 1):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        c_here, c_next = corner[i], corner[(i + 1) % n]
        if c_here and c_next:
            out.append(f"L{_fmt(p2)}")
            continue
        # Handles no longer than a third of their segment (longer ones overshoot into little
        # loops where points crowd together).
        seg = np.hypot(*(p2 - p1)) / 3

        def handle(t):
            length = np.hypot(*t)
            return t * min(1.0, seg / length) if length else t

        c1 = p1 if c_here else p1 + handle((p2 - p0) / 6)
        c2 = p2 if c_next else p2 - handle((p3 - p1) / 6)
        out.append(f"C{_fmt(c1)} {_fmt(c2)} {_fmt(p2)}")
    return out


def path_d(points: np.ndarray) -> str:
    """A closed path through `points`."""
    return f"M{_fmt(points[0])}" + "".join(_curves(points, closed=True)) + "Z"


def shape_d(loops, tol: float = 0.35, min_area: float = 2.0) -> str:
    """Closed paths through contour loops (simplified), leaving out ones smaller than `min_area`."""
    parts = []
    for loop in loops:
        if abs(signed_area(loop)) < min_area:
            continue
        s = simplify(loop, tol)
        if len(s) >= 3:
            parts.append(path_d(s))
    return " ".join(parts)


def signed_area(loop) -> float:
    x, y = loop[:, 0], loop[:, 1]
    return 0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def contains(loop, pt) -> bool:
    """Whether `pt` is inside the polygon `loop` (even-odd ray cast)."""
    x, y = loop[:, 0], loop[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    crosses = (y > pt[1]) != (y2 > pt[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = x + (pt[1] - y) * (x2 - x) / (y2 - y)
    return bool(np.count_nonzero(crosses & (xs > pt[0])) % 2)


def shapes(loops, min_area: float) -> list[list[np.ndarray]]:
    """Contour loops grouped into shapes: each outer outline with the holes inside it."""
    big = [loop for loop in loops if abs(signed_area(loop)) >= min_area]
    if not big:
        return []
    outer_sign = np.sign(signed_area(max(big, key=lambda loop: abs(signed_area(loop)))))
    groups = [[loop] for loop in big if np.sign(signed_area(loop)) == outer_sign]
    for hole in (loop for loop in big if np.sign(signed_area(loop)) != outer_sign):
        home = [g for g in groups if contains(g[0], hole[0])]
        if home:
            min(home, key=lambda g: abs(signed_area(g[0]))).append(hole)
    return groups


def find_rings(black: np.ndarray, r_min: float, r_max: float, coverage: float) -> list[tuple[float, float, float]]:
    """Circles drawn as black rings (the nexus marks): (cx, cy, r of the ring's middle). A Hough
    vote over the black pixels for each radius, then each strong centre checked: a ring counts
    only if black covers at least `coverage` of it (the side where it meets the body has none,
    and outline arcs that are part of a larger shape cover far less). Refined by a least-squares
    circle through its black pixels."""
    ys, xs = np.nonzero(black)
    px, py = xs + 0.5, ys + 0.5
    h, w = black.shape
    near = ndimage.binary_dilation(black, iterations=1)
    found = []
    for r in np.arange(r_min, r_max + 0.5, 1.0):
        n = max(24, int(round(2 * math.pi * r)))
        ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
        acc = np.zeros((h, w))
        cx = (px[:, None] - r * np.cos(ang)[None, :]).astype(int).ravel()
        cy = (py[:, None] - r * np.sin(ang)[None, :]).astype(int).ravel()
        ok = (cx >= 0) & (cx < w) & (cy >= 0) & (cy < h)
        np.add.at(acc, (cy[ok], cx[ok]), 1)
        acc = ndimage.uniform_filter(acc, 3) * 9
        peaks = (acc == ndimage.maximum_filter(acc, size=int(r))) & (acc > n * 0.8)
        for y, x in zip(*np.nonzero(peaks)):
            sx, sy = x + 0.5 + r * np.cos(ang), y + 0.5 + r * np.sin(ang)
            inside = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
            hit = np.zeros(n, bool)
            hit[inside] = near[sy[inside].astype(int), sx[inside].astype(int)]
            if hit.mean() >= coverage:
                found.append((float(hit.mean()), x + 0.5, y + 0.5, float(r)))
    rings = []
    for _, x, y, r in sorted(found, reverse=True):
        if any(math.hypot(x - rx, y - ry) < max(r, rr) for rx, ry, rr in rings):
            continue
        sel = np.abs(np.hypot(px - x, py - y) - r) < 2
        cx, cy, c = np.linalg.lstsq(np.column_stack([px[sel], py[sel], np.ones(sel.sum())]), px[sel] ** 2 + py[sel] ** 2, rcond=None)[0]
        cx, cy = cx / 2, cy / 2
        rings.append((float(cx), float(cy), float(math.sqrt(c + cx * cx + cy * cy))))
    return rings


def disk(shape, cx: float, cy: float, r: float) -> np.ndarray:
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    return np.hypot(xx + 0.5 - cx, yy + 0.5 - cy) <= r


def hexc(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


# ------------------------------------------------------------------------------------------------
# The minimap
# ------------------------------------------------------------------------------------------------


def to_svg(picture: Image.Image) -> str:
    """The custom minimap `picture` as an SVG (the module's docstring). Raises MinimapError for a
    picture it can't measure."""
    im = np.asarray(picture.convert("RGBA")).astype(float)
    rgb, a = im[..., :3], im[..., 3] / 255
    h, w = a.shape
    lum = rgb.mean(axis=2)
    # Distance from the outer edge, supersampled for smooth contours; supersampled pixel (i, j) is
    # centred on ((j + 0.5) / UP, (i + 0.5) / UP).
    big = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((w * UP, h * UP), Image.BILINEAR)) / 255
    depth_big = ndimage.distance_transform_edt(big >= 0.5) / UP
    depth = depth_big[UP // 2 :: UP, UP // 2 :: UP][:h, :w]

    def band(lo, hi):
        sel = (depth >= lo) & (depth < hi) & (a > 0.9)
        return rgb[sel].mean(axis=0) if sel.any() else np.full(3, np.nan)

    # The bands across the edge: black, then the light line, then the body. Each boundary where
    # the brightness crosses halfway between the bands, interpolated between half-pixel bins.
    profile = []
    for d in np.arange(0.0, 8.0, 0.5):
        c = band(d, d + 0.5)
        if not np.isnan(c).any():
            profile.append((d + 0.25, c.mean()))
    edge_body = band(6, 9)
    if len(profile) < 4 or np.isnan(edge_body).any():
        raise MinimapError("no edge to measure")
    peak = max(profile, key=lambda p: p[1])

    def crossing(level, after, rising):
        for (d0, l0), (d1, l1) in zip(profile, profile[1:]):
            if d0 >= after and ((l0 < level <= l1) if rising else (l0 > level >= l1)):
                return d0 + (level - l0) / (l1 - l0) * (d1 - d0)
        raise MinimapError("no edge bands found")

    black_w = crossing(peak[1] / 2, 0, True)
    light_end = crossing((peak[1] + edge_body.mean()) / 2, peak[0] - 0.01, False)
    inner_w = light_end - black_w
    line_rgb = band(black_w + 0.4, light_end - 0.4)
    centre = black_w + inner_w / 2  # the light line's middle, from the edge

    # The nexus marks: circles whose outline is most of a black ring.
    rings = find_rings((lum < 30) & (a > 0.5), h / 40, h / 10, coverage=0.65)
    # The light line's own pixels: light, and joined to the band along the edge.
    light = (a >= 0.5) & (lum > (edge_body.mean() + line_rgb.mean()) / 2)
    light_lab, _ = ndimage.label(light, structure=np.ones((3, 3)))
    line_px = np.isin(light_lab, np.unique(light_lab[light & (depth <= light_end + 1)]))

    def junction(loop, edge, c, r):
        """How the outline goes round one nexus: for each cut-in, where the outline leaves the
        border (a loop index), the cut-in's tip and the curve between them; and the circle
        (centre, radius, which way round) between the tips. None if the loop doesn't follow it."""
        n = len(loop)
        d = np.hypot(*(loop - c).T)
        near = np.abs(d - (r + black_w / 2 - centre)) < 1.0
        if near.sum() < 20:
            return None
        # The circle the light line runs on, fitted to the trace where it follows it.
        px_, py_ = loop[near, 0], loop[near, 1]
        sol = np.linalg.lstsq(np.column_stack([px_, py_, np.ones(near.sum())]), px_**2 + py_**2, rcond=None)[0]
        c = np.array([sol[0] / 2, sol[1] / 2])
        radius = math.sqrt(sol[2] + c @ c)
        d = np.hypot(*(loop - c).T)
        on_arc = np.abs(d - radius) < 0.5
        if on_arc.all() or not on_arc.any():
            return None
        order = (np.arange(n) + int(np.argmax(~on_arc))) % n  # from off the arc: its run doesn't wrap
        runs, k = [], 0
        while k < n:
            if on_arc[order[k]]:
                j = k
                while j + 1 < n and on_arc[order[j + 1]]:
                    j += 1
                runs.append((k, j))
                k = j + 1
            else:
                k += 1
        k0, k1 = max(runs, key=lambda kj: kj[1] - kj[0])

        def angle(q):
            return math.atan2(q[1] - c[1], q[0] - c[0])

        turned = (angle(loop[order[min(k0 + 5, k1)]]) - angle(loop[order[k0]]) + math.pi) % (2 * math.pi) - math.pi
        sense = 1.0 if turned > 0 else -1.0  # which way round the loop goes
        e_d = np.hypot(*(edge - c).T)
        notches = edge[(np.abs(e_d - (radius + centre)) > 0.8) & (e_d < radius + centre + 3)]
        sides = []
        for end, way in ((k0, -1), (k1, 1)):
            q_end = loop[order[end]]
            # The cut-in's tip: on along the circle past where the trace leaves it, to where its
            # light ends.
            a_end = a_tip = angle(q_end)
            missed = 0
            for k in range(1, 120):
                a_k = a_end + way * sense * k * 0.25 / radius
                q = c + radius * np.array([math.cos(a_k), math.sin(a_k)])
                if line_px[min(max(int(q[1]), 0), h - 1), min(max(int(q[0]), 0), w - 1)]:
                    a_tip, missed = a_k, 0
                else:
                    missed += 1
                    if missed > 4:
                        break
            tip = c + radius * np.array([math.cos(a_tip), math.sin(a_tip)])
            # Where the trace is clear of the notch: the border carries on from there.
            notch = notches[np.argmin(np.hypot(*(notches - q_end).T))] if len(notches) else q_end
            j = end
            while 0 < j < n - 1 and (np.hypot(*(loop[order[j]] - notch)) < centre + 1.5 or abs(d[order[j]] - radius) < 1.5):
                j += way
            border = loop[order[j]]
            ahead = loop[order[min(max(j + 3 * way, 0), n - 1)]]
            onward = (ahead - border) / max(float(np.hypot(*(ahead - border))), 1e-9)  # the border's way on
            sides.append({"j": j, "tip": tip, "a_tip": a_tip, "arc_end": q_end, "way": way, "border": border,
                          "curve": body_curve(tip, border, -onward, c, radius)})
        if sides[0]["j"] >= sides[1]["j"]:
            return None
        return {"order": order, "sides": sides, "c": c, "R": radius, "sense": sense}

    def body_curve(tip, border, back, c, radius):
        """The body's light line from a cut-in's tip to the border: a cubic Bézier fitted to the
        light pixels between them (not the circle's), its handle at the border along `back` so it
        carries straight on into the border. Returns the handles (the tip's, the border's)."""
        chord = border - tip
        length = float(np.hypot(*chord))
        ys, xs = np.nonzero(line_px)
        pts = np.column_stack([xs + 0.5, ys + 0.5])
        t = ((pts - tip) @ chord) / max(length**2, 1e-9)
        off = np.abs(cross(chord, pts - tip)) / max(length, 1e-9)
        keep = (t > 0.05) & (t < 0.95) & (off < 3) & (np.abs(np.hypot(*(pts - c).T) - radius) > 1.2)
        h1, h2 = tip + chord / 3, border + back * length / 3
        if keep.sum() >= 3:
            pts, t = pts[keep], t[keep][:, None]
            b0, b1, b2, b3 = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t**2 * (1 - t), t**3
            rest = pts - b0 * tip - (b2 + b3) * border  # with h2 = border + back * l2
            m = np.zeros((2 * len(pts), 3))  # unknowns: h1's x and y, and l2
            m[0::2, 0] = b1[:, 0]
            m[1::2, 1] = b1[:, 0]
            m[0::2, 2] = b2[:, 0] * back[0]
            m[1::2, 2] = b2[:, 0] * back[1]
            sol = np.linalg.lstsq(m, rest.reshape(-1), rcond=None)[0]
            h1 = np.array(sol[:2])
            h2 = border + back * float(np.clip(sol[2], 0.1 * length, 0.8 * length))
        return h1, h2

    def outline_path(loop, nexuses):
        """The outline's path: the border traced (simplified), and at each nexus a curve in to the
        first cut-in's tip, the circle's arc round to the other's, and a curve back out. Returns
        the path, the mitre limit its tips need, and the cut-ins' black wedges (between the arc and
        the curve, out to where the trace left them), so the black fills them at any size."""
        n = len(loop)
        skip, specials, wedges = np.zeros(n, bool), {}, []
        mitre = 4.0
        for nx in nexuses:
            order, (s0, s1) = nx["order"], nx["sides"]
            skip[order[s0["j"] + 1 : s1["j"] + 1]] = True
            h1a, h2a = s0["curve"]
            h1b, h2b = s1["curve"]
            span = ((s1["a_tip"] - s0["a_tip"]) * nx["sense"]) % (2 * math.pi)
            radius = nx["R"]
            specials[order[s0["j"]]] = (
                f"C{_fmt(h2a)} {_fmt(h1a)} {_fmt(s0['tip'])}"
                f"A{radius:.2f} {radius:.2f} 0 {int(span > math.pi)} {int(nx['sense'] > 0)} {_fmt(s1['tip'])}"
                f"C{_fmt(h1b)} {_fmt(h2b)} {_fmt(s1['border'])}",
                s1["border"],
            )
            for side, (h_tip, h_border) in ((s0, s0["curve"]), (s1, s1["curve"])):
                # From the tip along the curve to the border, across to where the trace left the
                # circle, and back round the circle to the tip.
                sweep = int(side["way"] * nx["sense"] > 0)
                wedges.append(f"M{_fmt(side['tip'])}C{_fmt(h_tip)} {_fmt(h_border)} {_fmt(side['border'])}L{_fmt(side['arc_end'])}"
                              f"A{radius:.2f} {radius:.2f} 0 0 {sweep} {_fmt(side['tip'])}Z")
            for side, handle in ((s0, h1a), (s1, h1b)):  # each tip's angle, between curve and arc
                radial = side["tip"] - nx["c"]
                v = np.array([-radial[1], radial[0]]) / max(float(np.hypot(*radial)), 1e-9)
                u = (handle - side["tip"]) / max(float(np.hypot(*(handle - side["tip"]))), 1e-9)
                tip_angle = math.acos(float(np.clip(abs(u @ v), 0, 1)))
                mitre = max(mitre, min(12.0, 1.05 / max(math.sin(tip_angle / 2), 1e-3)))
        # Start where nothing's replaced, then go round: border stretches, then each nexus.
        free = ~skip
        free[list(specials)] = False
        first = int(np.argmax(free))
        out, run = [f"M{_fmt(loop[first])}"], [loop[first]]
        for k in range(1, n + 1):
            i = (first + k) % n
            if skip[i]:
                continue
            run.append(loop[i])
            if i in specials:
                commands, resume = specials[i]
                out += _curves(open_simplify(np.array(run), 0.3), closed=False)
                out.append(commands)
                run = [resume]
        out += _curves(open_simplify(np.array(run), 0.3), closed=False)
        return "".join(out) + "Z", mitre, " ".join(wedges)

    # The outline: the smooth trace at the light line's depth, the nexuses drawn as above.
    outline_loops = [loop / UP for loop in contours(depth_big - centre, 0)]
    if not outline_loops:
        raise MinimapError("no outline")
    main = max(range(len(outline_loops)), key=lambda i: abs(signed_area(outline_loops[i])))
    loop = outline_loops[main]
    edge = np.vstack([e / UP for e in contours(big, 0.5)])  # the shape's own edge
    nexuses = [nx for cx, cy, r in rings if (nx := junction(loop, edge, np.array([cx, cy]), r))]
    outline_d, tip_mitre, wedges_d = outline_path(loop, nexuses)
    others = [loop for i, loop in enumerate(outline_loops) if i != main and abs(signed_area(loop)) > 20]
    if others:
        outline_d += " " + shape_d(others, tol=0.3)

    # The body's fill: its commonest colour inside the light line (a shade lighter in the band
    # just inside it, which the edge measurement used).
    interior = depth > light_end + 0.75
    if not interior.any():
        raise MinimapError("no body inside the edge")
    colours, counts = np.unique(rgb[interior].astype(np.uint8), axis=0, return_counts=True)
    body_rgb = colours[np.argmax(counts)].astype(float)
    body_lum = body_rgb.mean()

    # Light parts: inside the light line, lighter than halfway between the body and the light
    # colour; each its own shape in its own colour, outlined halfway to it.
    island_mask = interior & (lum > body_lum + 20)
    island_lum = rgb[island_mask].mean() if island_mask.any() else body_lum
    field = np.where(interior | ndimage.binary_dilation(island_mask, iterations=2), lum, body_lum)
    labels, _ = ndimage.label(field > (body_lum + island_lum) / 2)
    # Where the light line runs: its band along the edge, and on into the cut-ins along a nexus
    # circle. Light that touches the line but reaches well beyond it (a lane) is a part.
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    line_zone = depth <= light_end + 1
    for cx, cy, r in rings:
        line_zone |= np.abs(np.hypot(xx - cx, yy - cy) - r) < black_w + inner_w + 2
    lights = []  # (path, colour, middle)
    for k in range(1, labels.max() + 1):
        mine = labels == k
        if (mine & line_px).any() and (mine & ~line_zone).sum() < 8:  # the light line's own pixels
            continue
        own = np.percentile(lum[mine], 75)
        near = ndimage.binary_dilation(mine, iterations=2)
        colour = rgb[mine & (lum >= (body_lum + own) / 2)].mean(axis=0)
        for group in shapes(contours(np.where(near, field, body_lum), (body_lum + own) / 2), min_area=4):
            if d := shape_d(group, tol=0.3, min_area=0):
                lights.append((d, colour, group[0].mean(axis=0)))

    # Fog: one shape for the soft lighter areas, running on under the parts (each part's pixels
    # take the nearest outside it), cut at half the fog's brightness over the body, blurred by what
    # its edges' steepness says. No holes: the camps in it are shapes of their own.
    excess = lum - body_lum
    known = interior & ~ndimage.binary_dilation(island_mask, iterations=2)
    for cx, cy, r in rings:
        known &= ~disk(a.shape, cx, cy, r + 2)
    filled = np.zeros_like(excess)
    if known.any():
        _, (ny, nx_) = ndimage.distance_transform_edt(~known, return_indices=True)
        filled = np.where(interior, excess[ny, nx_], 0)
    plateau = np.percentile(filled[known & (filled > 3)], 75) if (known & (filled > 3)).any() else 0
    fog_d, fog_rgb, sigma = "", body_rgb, 0.0
    if plateau > 3:
        smooth = ndimage.gaussian_filter(filled, 1)
        fog_d = shape_d([g[0] for g in shapes(contours(smooth, plateau / 2), min_area=40)], tol=0.6, min_area=0)
        grad = np.hypot(*np.gradient(smooth))
        rim = np.abs(smooth - plateau / 2) < plateau * 0.1
        steep = np.percentile(grad[rim], 75) if rim.any() else plateau / 10
        sigma = float(np.clip(plateau / (steep * math.sqrt(2 * math.pi)), 1, 20))
        fog_rgb = rgb[known & (filled > plateau * 0.8)].mean(axis=0)

    # Camps: compact blobs clearly darker than the ring of pixels round them (holes in the fog, or
    # darker than the body); not next to the light parts, whose own 1-2 px rims are dark too.
    around = ndimage.median_filter(np.where(interior, lum, body_lum), size=31)
    near_parts = ndimage.binary_dilation(island_mask, iterations=1)
    blobs, _ = ndimage.label(interior & (lum < around - 3) & ~near_parts)
    camps = []
    for k, (sy, sx) in enumerate(ndimage.find_objects(blobs), 1):
        blob = blobs == k
        area = int(blob[sy, sx].sum())
        tall, wide = sy.stop - sy.start, sx.stop - sx.start
        if not 8 <= area <= (h / 20) ** 2 or min(tall, wide) < 4 or area < 0.5 * tall * wide:
            continue
        whole_ring = ndimage.binary_dilation(blob, iterations=3) & ~ndimage.binary_dilation(blob, iterations=1)
        ring = whole_ring & ~near_parts
        if ring.sum() < max(6, whole_ring.sum() / 5) or lum[ring].mean() - lum[blob].mean() < 5:
            continue
        level = (lum[ring].mean() + lum[blob].mean()) / 2
        near = ndimage.binary_dilation(blob, iterations=2)
        for group in shapes(contours(np.where(near, level - lum, -1.0), 0), min_area=4):
            if d := shape_d(group, tol=0.3, min_area=0):
                camps.append((d, rgb[blob].mean(axis=0), group[0].mean(axis=0)))

    def in_ring(part, ring):
        return math.hypot(part[2][0] - ring[0], part[2][1] - ring[1]) < ring[2]

    groups = [("camps", camps), ("parts", [p for p in lights if not any(in_ring(p, ring) for ring in rings)])]
    groups += [(f"nexus-{k}", [p for p in lights if in_ring(p, ring)]) for k, ring in enumerate(rings, 1)]

    blur = f'<filter id="fog-blur" x="-25%" y="-25%" width="150%" height="150%"><feGaussianBlur stdDeviation="{sigma:.1f}"/></filter>'
    # Classes for restyling (CSS beats the colours written here): out-stroke (the black: its
    # stroke and the cut-ins' fill), bg and inner-stroke (the main shape's fill and its light
    # line), fog, terrain (the light parts), camps, nexus (the swirls).
    groups_class = {"camps": "camps", "parts": "terrain"}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" preserveAspectRatio="none">',
           f'<defs>{blur}<path id="outline" vector-effect="non-scaling-stroke" d="{outline_d}"/><clipPath id="inside"><use href="#outline"/></clipPath></defs>',
           # The black: the outline stroked wide under the main shape (its inner half hidden by
           # the fill), and the cut-ins' wedges.
           f'<g id="out-stroke" class="out-stroke" fill="#000" stroke="#000">',
           f'<use href="#outline" fill="none" stroke-width="{inner_w + 2 * black_w:.2f}" stroke-linejoin="round"/>']
    if wedges_d:
        svg.append(f'<path id="cut-ins" d="{wedges_d}" stroke="none"/>')
    svg.append("</g>")
    # The main shape: the body's colour, edged with the light line.
    svg.append(f'<use id="body" class="bg inner-stroke" href="#outline" fill="{hexc(body_rgb)}" stroke="{hexc(line_rgb)}" '
               f'stroke-width="{inner_w:.2f}" stroke-linejoin="miter" stroke-miterlimit="{tip_mitre:.1f}"/>')
    if fog_d:
        svg.append(f'<path id="fog" class="fog" d="{fog_d}" fill="{hexc(fog_rgb)}" filter="url(#fog-blur)" clip-path="url(#inside)"/>')
    for name, parts in groups:
        kind = groups_class.get(name, "nexus")
        svg.append(f'<g id="{name}">')
        svg += [f'<path class="{kind}" d="{d}" fill="{hexc(colour)}" fill-rule="evenodd"/>' for d, colour, _ in parts]
        svg.append("</g>")
    svg.append("</svg>")
    return "\n".join(svg) + "\n"
