from __future__ import annotations

import time
from typing import Never, final, override

from chimera.core.exceptions import ChimeraException
from chimera.core.lock import lock
from chimera.instruments.dome import DomeBase
from chimera.instruments.lamp import LampBase
from chimera.interfaces.dome import DomeStatus, InvalidDomePositionException, Style
from chimera.util.coord import Coord

from chimera_robo43_dome.instruments.domedriver import DomeSlewTimeoutError, DomeDriver
from chimera_robo43_dome.util.domecoords import DEG_PER_TAG, DomeLookupTable, az_to_tag

class DomeRobo43(DomeBase, LampBase):
    """LNA 43cm dome - serial controller."""

    __config__ = {
        "device": "/dev/ttyUSB0",
        "model": "LNA ROBO43 DOME",
        "style": Style.Classic,
        "az_resolution": 2,
        "slew_timeout": 120,
        "serial_timeout_s": 10,
        "fake": False,
    }

    def __init__(self):
        DomeBase.__init__(self)
        LampBase.__init__(self)
        self._drv: DomeDriver | None = None
        self._lookup: DomeLookupTable = DomeLookupTable()

    @override
    def __start__(self):
        self._drv = DomeDriver(
            self.log,
            serial_timeout_s=float(self["serial_timeout_s"]),
            move_timeout_s=float(self["slew_timeout"]),
            fake_serial=bool(self["fake"])
        )
        self._drv.open(self["device"])
        super().__start__()

    @override
    def __stop__(self):
        super().__stop__()
        if self._drv is not None:
            self._drv.close()

    # ------------------------------------------------------------------
    # DomeSlew
    # ------------------------------------------------------------------

    @lock
    @override
    def get_az(self):
        assert self._drv is not None, "Driver not initialized"
        return self._drv.get_az()

    @lock
    @override
    def slew_to_az(self, az):
        assert self._drv is not None, "Driver not initialized"
        if float(az) > 360:
            raise InvalidDomePositionException(f"Cannot slew to {az}: outside limits.")

        tel = self.telescope
        # telescope with tracking disabled is assumed to not be in use
        if tel and tel.is_tracking():
            alt, tel_az = tel.get_position_alt_az()
            tag = self._lookup.get_tag_altaz(alt, tel_az)

            tag_resolution = max(1, round(self["az_resolution"] / DEG_PER_TAG))
            if abs(tag - self._drv.get_tag()) <= tag_resolution:
                return
        else:
            if tel and not tel.is_tracking():
                self.log.debug("Telescope is not tracking — ignoring lookup table.")
            elif not tel:
                self.log.error("No telescope proxy — cannot use lookup table.")
            tag = az_to_tag(float(az))

        self.slew_begin(az)
        try:
            self._drv.move_to_tag(tag)
            self.slew_complete(self.get_az(), DomeStatus.OK)
        except DomeSlewTimeoutError as e:
            self.slew_complete(self.get_az(), DomeStatus.ABORTED)
            raise ChimeraException(str(e)) from e

    @override
    def is_slewing(self):
        assert self._drv is not None, "Driver not initialized" 
        return not self._drv.is_idle()

    @override
    def abort_slew(self) -> Never:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # DomeSlit
    # ------------------------------------------------------------------

    @lock
    def open_slit(self):
        assert self._drv is not None, "Driver not initialized"
        self.log.debug("Opening dome slit.")
        if not self._drv.open_slit():
            raise ChimeraException("Failed to open slit — controller did not ACK.")

    @lock
    def close_slit(self):
        assert self._drv is not None, "Driver not initialized"
        self.log.debug("Closing dome slit.")
        if not self._drv.close_slit():
            raise ChimeraException("Failed to close slit — controller did not ACK.")

    @override
    def is_slit_open(self):
        assert self._drv is not None, "Driver not initialized"
        return self._drv.state.slit_open

    # ------------------------------------------------------------------
    # LampBase
    # ------------------------------------------------------------------

    @lock
    @override
    def switch_on(self):
        assert self._drv is not None, "Driver not initialized" 
        return self._drv.lamp_on()

    @lock
    @override
    def switch_off(self):
        assert self._drv is not None, "Driver not initialized" 
        return self._drv.lamp_off()

    @override
    def is_switched_on(self):
        assert self._drv is not None, "Driver not initialized" 
        return self._drv.state.lamp_on

    @override
    def track(self):
        super(DomeBase, self).track()
        # The dome motor needs time to settle after tracking is enabled
        # before the control loop starts issuing moves.
        self.log.debug("Sleeping 15s after tracking enabled to let dome motor settle.")
        time.sleep(15)

    # ------------------------------------------------------------------
    # FITS metadata
    # ------------------------------------------------------------------

    @override
    def get_metadata(self, request) -> list[tuple[str, str, str]]:
        md = self.get_metadata_override(request)
        if md is not None:
            return md
        return [
            ("DOME_MDL", str(self["model"]), "Dome Model"),
            ("DOME_TYP", str(self["style"]), "Dome Type"),
            ("DOME_TRK", str(self["mode"]), "Dome Tracking/Standing"),
            ("DOME_AZ",  str(self.get_az()), "Dome Azimuth"),
            ("DOME_SLT", "Open" if self.is_slit_open() else "Closed", "Dome slit status"),
        ]
