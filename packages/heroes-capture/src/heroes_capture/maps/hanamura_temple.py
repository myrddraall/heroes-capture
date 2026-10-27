"""Hanamura Temple: the timers between the gates opening and its first objective (the payloads), cut
short so the objective is in place before any tile is shot. Names from its library's Galaxy code
(CASC: mods/heroesmapmods/battlegroundmapmods/hanamura.stormmod/base.stormdata/LibMHmu.galaxy)."""

from heroes_capture.map.specifics import Specifics

OPENING_TIMERS = [
    "libMHmu_gv_hanamura.lv_mapMechanic_ObjectivePreviewTimer",
    "libMHmu_gv_hanamura.lv_mapMechanic_ObjectivePreviewDelay",
    "libMHmu_gv_hanamuraMM10secondcountdownTimer",
    "libMHmu_gv_elevator.lv_elevatorInitialRaiseTimer",
    "libMHmu_gv_elevator.lv_elevatorRaiseTimer",
]


def extend(specifics: Specifics) -> None:
    specifics.opening_timers += OPENING_TIMERS
