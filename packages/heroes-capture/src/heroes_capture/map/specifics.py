"""What a map's category (categories/) and the map itself (maps/) add to the stages every map goes
through. cli.specifics_for builds one for a map, the category's part first, then the map's own, and
hands it to each stage; a stage calls the hooks at its points and reads the data. This is how the
layers above the capture take part without anything below them importing them."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Specifics:
    # The category's name (categories/<name>); the manifest records it, so each later stage (and a
    # run carried on later) builds the same.
    category: str
    # Map script parts after the map's own and the capture's (map/script.py), in order.
    script_parts: list[Path] = field(default_factory=list)
    # prepare(prep): the map file open (capture/prepare.Preparation); adds its parts' values,
    # manifest fields, archive edits, capture areas.
    prepare: list[Callable] = field(default_factory=list)
    # before_tiles(session, manifest, base): in the match, the start-up done, before the tiles.
    before_tiles: list[Callable] = field(default_factory=list)
    # calibrate(session, manifest, base, stages, previous) -> dict: in a calibration's match
    # (capture/calibration.py), the start-up done: what the named stages measured, for the map's
    # calibration.json (`previous`: the map's calibration so far, for a stage that builds on another).
    calibrate: list[Callable] = field(default_factory=list)
    # The calibration's stages, each with its version (bumped when what it measures changes); and
    # its probes, run only when named (`map calibrate --only`), which a render doesn't need.
    calibration_stages: dict[str, int] = field(default_factory=dict)
    calibration_probes: dict[str, int] = field(default_factory=dict)
    # after_stitch(stitched): the map images written (capture/stitch.Stitched).
    after_stitch: list[Callable] = field(default_factory=list)
    # pack_data(packing) -> dict: entries for the pack's data (capture/pack.Packing; PACK.md).
    pack_data: list[Callable] = field(default_factory=list)
    # The map's timers between the gates and its first objective, cut short (capture/script.py).
    opening_timers: list[str] = field(default_factory=list)
    # Cells added to each edge of where the custom minimap is placed (capture/pack.py).
    minimap_correction: dict[str, float] = field(default_factory=dict)
    # The map's render has been reviewed (maps/validated-maps.json).
    validated: bool = False
