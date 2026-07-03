from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from chimera_robo43_dome.instruments.domedriver import DomeDriver, DomeSlewTimeoutError
from chimera_robo43_dome.util.domecoords import PARK_TAG
from tests.fake_controller import ControllerSerialFake


def make_driver(fake: ControllerSerialFake, move_timeout_s: float = 5.0) -> DomeDriver:
    drv = DomeDriver(logging.getLogger("test"), move_timeout_s=move_timeout_s)
    drv._serial = fake
    fake.open("fake://")
    return drv


class TestWithFake:
    def test_open_initialises_tag(self):
        fake = ControllerSerialFake(initial_tag=PARK_TAG)
        drv = make_driver(fake)
        drv.open("fake://")
        assert drv.state.current_tag == PARK_TAG

    def test_move_to_tag_succeeds(self):
        fake = ControllerSerialFake()
        drv = make_driver(fake)
        drv.open("fake://")
        drv.move_to_tag(870)
        assert drv.state.current_tag == 870

    def test_move_to_tag_updates_az(self):
        from chimera_robo43_dome.util.domecoords import tag_to_az
        fake = ControllerSerialFake()
        drv = make_driver(fake)
        drv.open("fake://")
        drv.move_to_tag(870)
        assert drv.get_az() == tag_to_az(870)

    def test_open_slit_updates_state(self):
        fake = ControllerSerialFake()
        drv = make_driver(fake)
        drv.open("fake://")
        result = drv.open_slit()
        assert result is True
        assert drv.state.slit_open is True

    def test_close_slit_updates_state(self):
        fake = ControllerSerialFake()
        drv = make_driver(fake)
        drv.open("fake://")
        drv.open_slit()
        result = drv.close_slit()
        assert result is True
        assert drv.state.slit_open is False

    def test_lamp_on_off(self):
        fake = ControllerSerialFake()
        drv = make_driver(fake)
        drv.open("fake://")
        assert drv.lamp_on() is True
        assert drv.state.lamp_on is True
        assert drv.lamp_off() is True
        assert drv.state.lamp_on is False

    def test_open_slit_returns_false_on_nak(self):
        fake = ControllerSerialFake(nak_commands={"ABRIR"})
        drv = make_driver(fake)
        drv.open("fake://")
        assert drv.open_slit() is False
        assert drv.state.slit_open is False

    def test_close_slit_returns_false_on_nak(self):
        fake = ControllerSerialFake(nak_commands={"FECHAR"})
        drv = make_driver(fake)
        drv.open("fake://")
        drv.state.slit_open = True  # pretend it was open
        assert drv.close_slit() is False
        assert drv.state.slit_open is True  # state not mutated on NAK

    def test_move_to_tag_fails_after_max_retries(self):
        # settle_polls=9999: dome never becomes idle within any timeout.
        # Bypass open() to avoid the startup reset timing out; set state directly.
        fake = ControllerSerialFake(settle_polls=9999)
        fake.open("fake://")
        drv = DomeDriver(logging.getLogger("test"), move_timeout_s=0.05)
        drv._serial = fake
        drv.state.current_tag = PARK_TAG
        with pytest.raises(DomeSlewTimeoutError):
            drv.move_to_tag(870)


# ---------------------------------------------------------------------------
# Unit tests — unittest.mock (targeted error paths)
# ---------------------------------------------------------------------------

class TestWithMock:
    def _make_mock_driver(self, move_timeout_s=0.05) -> DomeDriver:
        drv = DomeDriver(logging.getLogger("test"), move_timeout_s=move_timeout_s)
        drv._serial = MagicMock()
        return drv

    def _status_response(self, tag: int, busy: bool) -> str:
        return f"ACK     {tag:03d}     {'1' if busy else '0'}"

    def test_send_until_ack_raises_after_all_retries(self):
        drv = self._make_mock_driver()
        drv._serial.send.return_value = "NAK 00"
        with pytest.raises(RuntimeError, match="failed after"):
            drv._send_until_ack("MEADE PROG PARAR")

    def test_open_slit_returns_false_on_nak(self):
        drv = self._make_mock_driver()
        drv._serial.send.return_value = "NAK 00"
        assert drv.open_slit() is False

    def test_close_slit_returns_false_on_nak(self):
        drv = self._make_mock_driver()
        drv._serial.send.return_value = "NAK 00"
        assert drv.close_slit() is False

    def test_move_to_tag_timeout_reissues_move(self):
        """After an initial slew timeout + reset, MOVER must be sent again
        before the retry loop so the loop checks a live slew position."""
        target = 870
        drv = self._make_mock_driver(move_timeout_s=0.05)

        # STATUS always reports busy (forces timeout), except PARAR/RESET
        # which we handle via side_effect on send.
        idle_resp = self._status_response(target, busy=False)
        busy_resp = self._status_response(PARK_TAG, busy=True)

        # With move_timeout_s=0.05s and sleep(1) inside _wait_idle, each
        # _wait_idle call consumes exactly one STATUS response before timing out.
        drv._serial.send.side_effect = [
            "ACK 00",  # first MOVER=870
            busy_resp, # STATUS → busy → _wait_idle times out after sleep(1)
            "ACK 00",  # PARAR (inside reset)
            "ACK 00",  # RESET (inside reset)
            "ACK 00",  # MOVER=reset_tag (inside reset)
            idle_resp, # STATUS → _wait_idle for reset completes
            "ACK 00",  # re-issued MOVER=870 (the fix being tested)
            idle_resp, # STATUS → _wait_idle after re-issue completes
            idle_resp, # STATUS → get_tag() in retry loop → settled at target
        ]

        drv.move_to_tag(target)

        move_calls = [c for c in drv._serial.send.call_args_list
                      if "MOVER" in str(c)]
        # Should have: initial MOVER + MOVER to reset_tag + re-issued MOVER = 3
        assert len(move_calls) >= 3, (
            f"Expected at least 3 MOVER calls, got {len(move_calls)}: {move_calls}"
        )
