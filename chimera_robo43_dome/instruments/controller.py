from __future__ import annotations

from logging import Logger
import time
from dataclasses import dataclass
from typing import final

import serial


SERIAL_BAUD    = 9600
SERIAL_TIMEOUT = 10   # seconds


# ---------------------------------------------------------------------------
# Controller state response
# ---------------------------------------------------------------------------

@dataclass
class ControllerStatus:
    tag: int          # current tag; 0 when uninitialized
    busy: bool        
    initialized: bool 
    raw: str          

    @classmethod
    def from_response(cls, raw: str) -> "ControllerStatus":
        raw = raw.replace("\r", "")
        initialized = len(raw) >= 11 and raw[8:11].strip() != ""
        tag = int(raw[8:11]) if initialized else 0
        busy = len(raw) >= 17 and raw[16] == "1"
        return cls(tag=tag, busy=busy, initialized=initialized, raw=raw)


def is_ack(response: str) -> bool:
    return response.strip().startswith("ACK")

class ControllerCommands:
    """ This controller was once used for a Meade telescope, so the commands are prefixed with MEADE. """

    @staticmethod
    def status() -> str:
        return "MEADE PROG STATUS"

    @staticmethod
    def move(tag: int) -> str:
        return f"MEADE DOMO MOVER = {tag:03d}"

    @staticmethod
    def stop() -> str:
        return "MEADE PROG PARAR"

    @staticmethod
    def reset() -> str:
        return "MEADE PROG RESET"

    @staticmethod
    def slit_open() -> str:
        return "MEADE TRAPEIRA ABRIR"

    @staticmethod
    def slit_close() -> str:
        return "MEADE TRAPEIRA FECHAR"

    @staticmethod
    def lamp_on() -> str:
        return "MEADE FLAT_WEAK LIGAR"

    @staticmethod
    def lamp_off() -> str:
        return "MEADE FLAT_WEAK DESLIGAR"


@final
class ControllerSerial:
    def __init__(self, timeout_s: float = SERIAL_TIMEOUT, logger: Logger|None=None):
        self._timeout = float(timeout_s)
        self._port: serial.Serial | None = None
        self._log = logger

    def open(self, device: str) -> None:
        self._port = serial.Serial(device, baudrate=SERIAL_BAUD, timeout=self._timeout)

    def close(self) -> None:
        if self._port and self._port.is_open:
            self._port.close()
        self._port = None

    def is_open(self) -> bool:
        return self._port is not None and self._port.is_open

    def send(self, cmd: str) -> str:
        """Flush, send cmd + CR, read until CR or timeout. Returns response."""
        assert self._port is not None and self._port.is_open, "Serial port not open"
        self._port.reset_output_buffer()
        self._port.reset_input_buffer()
        if self._log:
            self._log.debug("[serial write] %r", cmd)
        if not (self._port.write(f"{cmd}\r".encode())):
            raise RuntimeError("Serial write failed")

        response = ""
        t0 = time.time()
        while "\r" not in response:
            response += self._port.read().decode()
            time.sleep(0.1)
            if time.time() - t0 > self._timeout:
                self._port.reset_input_buffer()
                self._port.reset_output_buffer()
                if self._log:
                    self._log.debug("Serial read timeout — flushed. Partial: %r", response)
                break

        response = response.replace("\r", "")
        if self._log:
            self._log.debug("[serial read ] %r", response)
        return response
