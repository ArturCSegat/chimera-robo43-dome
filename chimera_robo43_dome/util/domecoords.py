from __future__ import annotations

import math
import os

import numpy as np
from chimera.util.coord import Coord
from chimera.util.position import Position

import chimera_robo43_dome


TAG_MIN     = 801   # first valid tag (corresponds to az = 270°)
TAG_AZ_ZERO = 846   # tag that corresponds to az = 0°
TAG_MAX     = 982   # last valid tag
DEG_PER_TAG = 2.0   # degrees of rotation per tag step

PARK_TAG = 900  # reference / park position


def az_to_tag(az: float) -> int:
    """Convert azimuth in degrees (0–360) to the nearest controller tag."""
    if az >= 270:
        return int(math.ceil((az - 270) / DEG_PER_TAG + TAG_MIN))
    return int(math.ceil(az / DEG_PER_TAG + TAG_AZ_ZERO))


def tag_to_az(tag: int) -> float:
    """Convert a controller tag to azimuth in degrees."""
    if tag < TAG_AZ_ZERO:
        return 270.0 + (tag - TAG_MIN) * DEG_PER_TAG
    return (tag - TAG_AZ_ZERO) * DEG_PER_TAG

class DomeLookupTable:
    def __init__(self):
        self._table = np.loadtxt(
            f"{os.path.dirname(chimera_robo43_dome.__file__)}/data/dome_model.csv",
            delimiter=",",
        )
        self._coordinates = [
            [Position.from_alt_az(Coord.from_r(v[0]), Coord.from_r(v[1])), v[2]]
            for v in self._table
        ]

    def get_tag_altaz(self, alt: float, az: float) -> int:
        """Return the nearest tag for a given alt/az position (degrees)."""
        position = Position.from_alt_az(Coord.from_r(alt), Coord.from_r(az))
        argmin = np.argmin([v[0].angsep(position) for v in self._coordinates])
        return int(self._coordinates[argmin][1])
