from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import final

from chimera_robo43_dome.instruments.controller import ControllerCommands, ControllerSerial, ControllerStatus, is_ack
from chimera_robo43_dome.util.domecoords import PARK_TAG, tag_to_az
from tests.fake_controller import ControllerSerialFake


class DomeSlewTimeoutError(RuntimeError):
    pass


@dataclass
class DriverState:
    current_tag: int = 0
    slit_open: bool = False
    lamp_on: bool = False


@final
class DomeDriver:
    """
    Manages device state, retry/reset logic, and move-completion polling.
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        serial_timeout_s: float = 10.0,
        move_timeout_s: float = 120.0,
        precision_tags: int = 2,
        restart_precision_tags: int = 4,
        max_retries: int = 3,
        fake_serial: bool = False,
    ):
        self.log = logger
        self._move_timeout_s = float(move_timeout_s)
        self._precision = int(precision_tags)
        self._restart_precision = int(restart_precision_tags)
        self._max_retries = int(max_retries)

        self._serial: ControllerSerial | ControllerSerialFake = ControllerSerial(timeout_s=serial_timeout_s, logger=logger) if not fake_serial else ControllerSerialFake()
        self._cmd = ControllerCommands()
        self.state = DriverState()

    def open(self, device: str) -> None:
        self._serial.open(device)
        self.reset(PARK_TAG)
        self.state.current_tag = self.get_tag()
        self.log.info(
            "Dome opened on %s — initial tag=%d (az=%.1f°)",
            device,
            self.state.current_tag,
            tag_to_az(self.state.current_tag),
        )

    def close(self) -> None:
        self._serial.close()

    def get_status(self) -> ControllerStatus:
        return ControllerStatus.from_response(self._serial.send(self._cmd.status()))

    def is_idle(self) -> bool:
        status = self.get_status()
        if not status.initialized:
            return False
        return not status.busy

    def get_tag(self) -> int:
        """Return current tag; triggers re-initialization if controller reports blank."""
        status = self.get_status()
        if not status.initialized:
            self.log.info("Dome uninitialized — resetting to park tag.")
            self.reset(PARK_TAG)
            status = self.get_status()
            self.log.info("Dome initialized.")
        self.state.current_tag = status.tag
        return self.state.current_tag

    def get_az(self) -> float:
        return tag_to_az(self.get_tag())

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self, reset_tag: int = PARK_TAG) -> None:
        """PARAR + RESET, then move to reset_tag and wait for idle."""
        _= self._send_until_ack(self._cmd.stop())
        _= self._send_until_ack(self._cmd.reset())
        _= self._send_until_ack(self._cmd.move(reset_tag))
        self._wait_idle(self._move_timeout_s)

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def move_to_tag(self, tag: int) -> None:
        """
        Move to tag, block until complete.

        Retry sequence on NAK: reset to tag-100 and re-issue the command.
        After the move, verify final position; if too far, reset and retry.
        Raises DomeSlewTimeoutError if all retries are exhausted.
        """
        _= self._send_until_ack(self._cmd.move(tag))

        try:
            self._wait_idle(self._move_timeout_s)
        except DomeSlewTimeoutError:
            self.log.warning("Slew timed out — resetting and retrying.")
            self.reset(self._safe_reset_tag(tag))
            # Re-issue the move so the retry loop checks convergence from a live
            # slew, not from the safety reset tag.
            _= self._send_until_ack(self._cmd.move(tag))
            try:
                self._wait_idle(self._move_timeout_s)
            except DomeSlewTimeoutError:
                self.log.warning("Re-issued move also timed out — entering retry loop.")

        for attempt in range(self._max_retries):
            self.state.current_tag = self.get_tag()
            if abs(self.state.current_tag - tag) <= self._restart_precision:
                self.log.debug(
                    "Dome settled at tag=%d (target=%d)", self.state.current_tag, tag
                )
                return

            self.log.warning(
                "Position error %d tags (attempt %d/%d) — resetting.",
                abs(self.state.current_tag - tag),
                attempt + 1,
                self._max_retries,
            )
            self.reset(self._safe_reset_tag(tag))
            _= self._send_until_ack(self._cmd.move(tag))
            try:
                self._wait_idle(self._move_timeout_s)
            except DomeSlewTimeoutError:
                self.log.warning("Retry %d/%d timed out.", attempt + 1, self._max_retries)

        raise DomeSlewTimeoutError(
            f"Dome failed to reach tag {tag} after {self._max_retries} retries"
        )

    # ------------------------------------------------------------------
    # Slit
    # ------------------------------------------------------------------

    def open_slit(self) -> bool:
        ok = is_ack(self._serial.send(self._cmd.slit_open()))
        if ok:
            self.state.slit_open = True
        return ok

    def close_slit(self) -> bool:
        ok = is_ack(self._serial.send(self._cmd.slit_close()))
        if ok:
            self.state.slit_open = False
        return ok

    # ------------------------------------------------------------------
    # Lamp
    # ------------------------------------------------------------------

    def lamp_on(self) -> bool:
        ok = is_ack(self._serial.send(self._cmd.lamp_on()))
        if ok:
            self.state.lamp_on = True
        return ok

    def lamp_off(self) -> bool:
        ok = is_ack(self._serial.send(self._cmd.lamp_off()))
        if ok:
            self.state.lamp_on = False
        return ok

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send_until_ack(self, cmd: str) -> str:
        """Send cmd; retry up to max_retries if no ACK."""
        resp = ""
        for attempt in range(self._max_retries):
            resp = self._serial.send(cmd)
            if is_ack(resp):
                return resp
            self.log.debug(
                "No ACK from dome (attempt %d/%d) — retrying.", attempt + 1, self._max_retries
            )
            time.sleep(2)
        raise RuntimeError(f"Command {cmd} failed after {self._max_retries} retries — last response: {resp}")

    def _wait_idle(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_idle():
                return
            time.sleep(1)
        raise DomeSlewTimeoutError(f"Dome did not become idle within {timeout_s}s")

    def _safe_reset_tag(self, target_tag: int) -> int:
        """Return a reset tag 100 positions before target, wrapping around."""
        from chimera_robo43_dome.util.domecoords import TAG_MIN, TAG_MAX
        t = target_tag - 100
        if t < TAG_MIN:
            t = TAG_MAX - (TAG_MIN - t)
        return t
