from __future__ import annotations
from typing import final
from chimera_robo43_dome.util.domecoords import PARK_TAG


@final
class ControllerSerialFake:
    """
    In-memory simulation of the dome serial controller.

    Drop-in replacement for ControllerSerial — assign to driver._serial in tests.

    Parameters
    ----------
    initial_tag:
        Starting tag position (default: PARK_TAG).
    settle_polls:
        Number of STATUS polls after a MOVER command before the dome reports
        idle and updates its tag. 1 means the dome is idle on the very first
        poll after the command (default). Use a large value (e.g. 9999) to
        simulate a dome that never finishes moving, for timeout tests.
    nak_commands:
        Set of command substrings that return NAK instead of ACK.
        Example: {"ABRIR"} makes every slit-open command fail.
    """

    def __init__(
        self,
        initial_tag: int = PARK_TAG,
        settle_polls: int = 1,
        nak_commands: set[str] | None = None,
    ):
        self._tag = initial_tag
        self._target_tag = initial_tag
        self._busy = False
        self._polls_remaining = 0
        self._settle_polls = settle_polls
        self._nak_commands: set[str] = nak_commands or set()
        self._is_open = False

    def open(self, device: str) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def send(self, cmd: str) -> str:
        if any(kw in cmd for kw in self._nak_commands):
            return "NAK 00"

        if "STATUS" in cmd:
            return self._handle_status()
        if "MOVER" in cmd:
            return self._handle_move(cmd)
        if "PARAR" in cmd or "RESET" in cmd:
            self._busy = False
            return "ACK 00"
        return "ACK 00"


    def _handle_status(self) -> str:
        if self._busy:
            self._polls_remaining -= 1
            if self._polls_remaining <= 0:
                self._busy = False
                self._tag = self._target_tag
        busy_flag = "1" if self._busy else "0"
        # Format matches ControllerStatus.from_response:
        #   raw[8:11] = tag (3 digits), raw[16] = busy flag
        return f"ACK     {self._tag:03d}     {busy_flag}"

    def _handle_move(self, cmd: str) -> str:
        self._target_tag = int(cmd.split("=")[1].strip())
        self._busy = True
        self._polls_remaining = self._settle_polls
        return "ACK 00"
