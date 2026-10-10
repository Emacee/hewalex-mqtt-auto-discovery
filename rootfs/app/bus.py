"""
RS485 transport and GECO request/response client.

The PCWU is a slave on its dedicated RS485 port: we send a 0x40 read or 0x60
write and it answers with 0x50 / 0x70. Every response is matched on function,
addresses, start register and byte count, so stale or foreign frames on the
line can never be mistaken for the answer.
"""

from __future__ import annotations

import logging
import time

import serial

from geco import (
    FNC_READ_RESPONSE, FNC_WRITE_RESPONSE, Frame, FrameParser, GecoError,
    read_request, write_request,
)

logger = logging.getLogger("hewalex.bus")

BAUDRATE = 38400


class GecoTimeout(GecoError):
    """No matching response received."""


class GecoDeviceError(GecoError):
    """The device answered with an error frame."""


class Transport:
    """Raw byte pipe to the RS485 bus: TCP (RS485-WiFi/LAN adapter) or a local serial port."""

    def __init__(self, address: str, port: int):
        if address.startswith("/dev/"):
            self.url = address
        elif "://" in address:
            self.url = address
        else:
            self.url = f"socket://{address}:{port}"
        self._ser = None

    def open(self) -> None:
        self.close()
        logger.info("Opening %s", self.url)
        if self.url.startswith("/dev/"):
            self._ser = serial.Serial(self.url, BAUDRATE, bytesize=8, parity="N", stopbits=1, timeout=0.2)
        else:
            self._ser = serial.serial_for_url(self.url, baudrate=BAUDRATE, timeout=0.2)

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001 - closing a broken socket may fail, irrelevant
                pass
            self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def write(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def read(self, timeout: float) -> bytes:
        """Wait up to `timeout` for data, then return everything that has arrived."""
        self._ser.timeout = max(timeout, 0.01)
        data = self._ser.read(1)
        if data:
            self._ser.timeout = 0
            data += self._ser.read(4096)
        return data

    def discard_input(self) -> None:
        """Drop whatever is still buffered (late answers, noise)."""
        self._ser.reset_input_buffer()
        while self.read(0.05):
            pass


class GecoClient:
    def __init__(self, transport: Transport, con_hard: int, con_soft: int, dev_hard: int, dev_soft: int,
                 timeout: float = 2.0, retries: int = 2, max_read: int = 226):
        self.transport = transport
        self.con_hard = con_hard
        self.con_soft = con_soft
        self.dev_hard = dev_hard
        self.dev_soft = dev_soft
        self.timeout = timeout
        self.retries = retries
        self.max_read = max_read

    def _matches(self, frame: Frame, fnc: int, start: int, count: int) -> bool:
        return (frame.src_hard == self.dev_hard and frame.dst_hard == self.con_hard
                and frame.src_soft == self.dev_soft and frame.dst_soft == self.con_soft
                and (frame.is_error or (frame.fnc == fnc and frame.start == start and frame.count == count)))

    def _transact(self, request: Frame, fnc: int) -> Frame:
        last_error: Exception | None = None
        raw = request.encode()
        for attempt in range(1 + self.retries):
            if attempt:
                logger.debug("Retry %d for %s", attempt, request.describe())
                time.sleep(0.2 * attempt)
            self.transport.discard_input()
            logger.debug("TX %s | %s", raw.hex(), request.describe())
            self.transport.write(raw)
            parser = FrameParser()
            deadline = time.monotonic() + self.timeout
            while (remaining := deadline - time.monotonic()) > 0:
                chunk = self.transport.read(min(remaining, 0.5))
                if not chunk:
                    continue
                for frame in parser.feed(chunk):
                    if not self._matches(frame, fnc, request.start, request.count):
                        logger.debug("Ignoring frame %s", frame.describe())
                        continue
                    logger.debug("RX %s", frame.describe())
                    if frame.is_error:
                        raise GecoDeviceError(f"device rejected {request.describe()}: "
                                              f"error code {frame.data.hex() or '-'}")
                    return frame
            last_error = GecoTimeout(f"no response to {request.describe()}"
                                     + (f" ({parser.discarded} invalid bytes received)" if parser.discarded else ""))
        raise last_error

    def read(self, start: int, count: int) -> bytes:
        """Read `count` bytes starting at byte address `start` (split in device-sized chunks)."""
        result = bytearray()
        address = start
        end = start + count
        while address < end:
            n = min(self.max_read, end - address)
            request = read_request(self.dev_hard, self.con_hard, self.dev_soft, self.con_soft, address, n)
            result += self._transact(request, FNC_READ_RESPONSE).data
            address += n
        return bytes(result)

    def write_u16(self, address: int, value: int) -> None:
        """Write one 16-bit register; returns when the device acknowledged it."""
        data = bytes((value & 0xFF, (value >> 8) & 0xFF))
        request = write_request(self.dev_hard, self.con_hard, self.dev_soft, self.con_soft, address, data)
        self._transact(request, FNC_WRITE_RESPONSE)
