"""Team colour (PACK.md, Team colour): where a picture is drawn in a team's colour, and how much,
from the same view shot from both sides (our player on Order, then on Chaos: "side").

The game draws everything that has a team as our player's ally (blue) or enemy (red), so from
Order's side Order's structures and team-coloured doodads are blue and Chaos's red, and from
Chaos's side the other way round. Each pixel is taken as its own colour plus an amount of its
team's colour (one team: a structure's owner, a doodad's TeamColor): the difference between the
two sides is that amount times the difference between the two colours, positive for Order's team
(blue to red), negative for Chaos's (red to blue). One direction explains 98% of the change over
Battlefield of Eternity's structures and the same direction its terrain's.

The mask keeps the two amounts, Order's team's and Chaos's, in its red and green channels, in the
picture's own (unpremultiplied) colour: a viewer adds `amount * (chosen colour - TEAM_COLOURS)` to
the picture, which gives the picture itself with the colours as shot, and the other side's within a
few levels with them swapped."""

import numpy as np

# The two colours as the pictures have them at full strength, from Order's side: Order's team as
# the ally's blue, Chaos's as the enemy's red (the median direction of the most team-coloured
# pixels over Battlefield of Eternity's 72 structures, scaled to a full channel).
TEAM_COLOURS = {"order": (101, 159, 255), "chaos": (255, 57, 48)}
# A mask byte of 255 is this many times a team's colour (a faint glow's own colour, unpremultiplied,
# can be more team colour than its alpha alone carries).
MASK_SCALE = 2.0
# A pixel whose premultiplied colour changes by less than this between the sides keeps no team colour.
MASK_NOISE = 12.0
# Pixels this transparent or more have no colour of their own to change.
MASK_ALPHA = 0.02


def team_mask(order: np.ndarray, chaos: np.ndarray) -> np.ndarray | None:
    """The team-colour mask of a picture (RGBA uint8, shot from Order's side) from the same picture
    shot from Chaos's side: uint8 (h, w, 2), Order's team's amount and Chaos's (MASK_SCALE at 255);
    None where no pixel has any."""
    alpha = order[..., 3:4].astype(np.float32) / 255
    alpha_c = chaos[..., 3:4].astype(np.float32) / 255
    po, pc = order[..., :3] * alpha, chaos[..., :3] * alpha_c
    diff = pc - po
    order_colour, chaos_colour = (np.array(TEAM_COLOURS[k], np.float32) for k in ("order", "chaos"))
    toward = chaos_colour - order_colour
    amount = diff @ toward / float(toward @ toward)  # premultiplied: Order's team's positive
    a = alpha[..., 0]
    keep = (np.linalg.norm(diff, axis=2) > MASK_NOISE) & (a > MASK_ALPHA)
    amount = np.where(keep, amount / np.maximum(a, MASK_ALPHA), 0)  # in the picture's own colour
    if not keep.any():
        return None
    mask = np.zeros(order.shape[:2] + (2,), np.uint8)
    mask[..., 0] = np.clip(np.rint(amount / MASK_SCALE * 255), 0, 255)
    mask[..., 1] = np.clip(np.rint(-amount / MASK_SCALE * 255), 0, 255)
    return mask if mask.any() else None


# Where something else moves between the shots (the water, which can't be paused), a change counts
# as team colour only when it points this closely along the teams' colours (the cosine): moving
# foam brightens or darkens, about 0.32 off them; team colour is along them.
ALIGNED = 0.8


def aligned(order: np.ndarray, chaos: np.ndarray) -> np.ndarray:
    """Which pixels changed between the sides along the teams' colours (ALIGNED), premultiplied
    colour (RGBA uint8, the same shape)."""
    po = order[..., :3] * (order[..., 3:4].astype(np.float32) / 255)
    pc = chaos[..., :3] * (chaos[..., 3:4].astype(np.float32) / 255)
    diff = pc - po
    toward = np.array(TEAM_COLOURS["chaos"], np.float32) - np.array(TEAM_COLOURS["order"], np.float32)
    along = diff @ toward / float(np.linalg.norm(toward))
    return np.abs(along) >= ALIGNED * np.maximum(np.linalg.norm(diff, axis=2), 1e-6)


def recoloured(rgba: np.ndarray, mask: np.ndarray, colours: dict) -> np.ndarray:
    """A picture with its teams in other colours ({"order": rgb, "chaos": rgb}), as a viewer draws
    it (PACK.md, Team colour)."""
    out = rgba[..., :3].astype(np.float32)
    for channel, team in enumerate(("order", "chaos")):
        change = np.array(colours[team], np.float32) - np.array(TEAM_COLOURS[team], np.float32)
        out += mask[..., channel:channel + 1].astype(np.float32) / 255 * MASK_SCALE * change
    return np.dstack([np.clip(np.rint(out), 0, 255).astype(np.uint8), rgba[..., 3:4]])
