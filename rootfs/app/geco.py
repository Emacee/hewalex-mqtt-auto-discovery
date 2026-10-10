"""
GECO protocol used by Hewalex G-422 / G-426 controllers (PCWU heat pumps, ZPS).

Reverse engineered by krzysztof1111111111 (elektroda.pl), documented in
aelias-eu/hewalex-geco-protocol ("Geco Proto.pdf") and implemented in
mvdklip/Domoticz-Hewalex. Everything below is checked against frames captured
on a real PCWU bus (see tests/test_geco.py).

Frame = 8 byte header + payload

  Header
    [0]    0x69                start byte
    [1]    destination physical (hard) address
    [2]    source physical (hard) address
    [3:6]  0x84 0x00 0x00      constant
    [6]    payload length (bytes after the header, CRC16 included)
    [7]    CRC-8/DVB-S2 (poly 0xD5, init 0) over header bytes [0:7]

  Payload
    [0:2]  destination logical (soft) address, uint16 little-endian
    [2:4]  source logical (soft) address, uint16 little-endian
    [4]    function: 0x40 read, 0x50 read response, 0x60 write, 0x70 write response
    [5:7]  message type marker, uint16 LE: 0x0080 = normal, 0x00C0 = error
    normal (0x0080):
      [7]    number of BYTES read / written
      [8:10] first BYTE address, uint16 little-endian
      [10:]  register bytes (only in 0x50 and 0x60)
    error (0x00C0):
      [7:]   error code byte(s)
    [-2:]  CRC-16/XMODEM (poly 0x1021, init 0) over the payload without the
           CRC, transmitted BIG-endian (high byte first)

Registers are byte addressed. Almost all values are 16-bit little-endian words
at even addresses; temperatures are signed and scaled by 10.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

START_BYTE = 0x69
HEADER_CONST = b"\x84\x00\x00"
HEADER_LEN = 8
MAX_PAYLOAD_LEN = 255

MARKER_OK = 0x0080
MARKER_ERROR = 0x00C0

FNC_READ = 0x40
FNC_READ_RESPONSE = 0x50
FNC_WRITE = 0x60
FNC_WRITE_RESPONSE = 0x70

FNC_NAMES = {
    FNC_READ: "read",
    FNC_READ_RESPONSE: "read-response",
    FNC_WRITE: "write",
    FNC_WRITE_RESPONSE: "write-response",
}


class GecoError(Exception):
    """Base class for protocol errors."""


class GecoFrameError(GecoError):
    """Malformed frame or CRC mismatch."""


def crc8_dvb_s2(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    dst_hard: int
    src_hard: int
    dst_soft: int
    src_soft: int
    fnc: int
    marker: int = MARKER_OK
    count: int | None = None
    start: int | None = None
    data: bytes = b""

    @property
    def is_error(self) -> bool:
        return self.marker != MARKER_OK

    def encode(self) -> bytes:
        payload = struct.pack("<HHBH", self.dst_soft, self.src_soft, self.fnc, self.marker)
        if self.is_error:
            payload += self.data
        else:
            payload += struct.pack("<BH", self.count, self.start) + self.data
        payload += struct.pack(">H", crc16_xmodem(payload))
        if len(payload) > MAX_PAYLOAD_LEN:
            raise GecoFrameError(f"payload too long ({len(payload)} bytes)")
        header = bytes([START_BYTE, self.dst_hard, self.src_hard]) + HEADER_CONST + bytes([len(payload)])
        return header + bytes([crc8_dvb_s2(header)]) + payload

    def describe(self) -> str:
        name = FNC_NAMES.get(self.fnc, f"0x{self.fnc:02X}")
        if self.is_error:
            return (f"{name} ERROR {self.src_hard}/{self.src_soft} -> {self.dst_hard}/{self.dst_soft} "
                    f"marker=0x{self.marker:04X} code={self.data.hex()}")
        return (f"{name} {self.src_hard}/{self.src_soft} -> {self.dst_hard}/{self.dst_soft} "
                f"start={self.start} count={self.count} data={self.data.hex()}")


def read_request(dst_hard: int, src_hard: int, dst_soft: int, src_soft: int,
                 start: int, count: int) -> Frame:
    return Frame(dst_hard, src_hard, dst_soft, src_soft, FNC_READ, MARKER_OK, count, start)


def write_request(dst_hard: int, src_hard: int, dst_soft: int, src_soft: int,
                  start: int, data: bytes) -> Frame:
    return Frame(dst_hard, src_hard, dst_soft, src_soft, FNC_WRITE, MARKER_OK, len(data), start, bytes(data))


def decode_frame(raw: bytes) -> Frame:
    """Decode exactly one complete frame. Raises GecoFrameError when invalid."""
    if len(raw) < HEADER_LEN:
        raise GecoFrameError("frame shorter than header")
    if raw[0] != START_BYTE:
        raise GecoFrameError("missing start byte")
    if raw[3:6] != HEADER_CONST:
        raise GecoFrameError("invalid header constant")
    if crc8_dvb_s2(raw[:7]) != raw[7]:
        raise GecoFrameError("header CRC8 mismatch")
    length = raw[6]
    if len(raw) != HEADER_LEN + length:
        raise GecoFrameError(f"length mismatch: header says {length}, got {len(raw) - HEADER_LEN}")
    payload = raw[HEADER_LEN:]
    if length < 9:
        raise GecoFrameError("payload too short")
    if crc16_xmodem(payload[:-2]) != struct.unpack(">H", payload[-2:])[0]:
        raise GecoFrameError("payload CRC16 mismatch")
    body = payload[:-2]
    dst_soft, src_soft, fnc, marker = struct.unpack_from("<HHBH", body, 0)
    if marker != MARKER_OK:
        return Frame(raw[1], raw[2], dst_soft, src_soft, fnc, marker, data=bytes(body[7:]))
    if len(body) < 10:
        raise GecoFrameError("payload too short for register header")
    count, start = struct.unpack_from("<BH", body, 7)
    data = bytes(body[10:])
    if fnc in (FNC_READ_RESPONSE, FNC_WRITE) and len(data) != count:
        raise GecoFrameError(f"data length {len(data)} does not match count {count}")
    if fnc in (FNC_READ, FNC_WRITE_RESPONSE) and data:
        raise GecoFrameError("unexpected data in request/acknowledge")
    return Frame(raw[1], raw[2], dst_soft, src_soft, fnc, marker, count, start, data)


class FrameParser:
    """
    Incremental frame decoder for a raw RS485 byte stream.

    Feed it whatever bytes arrive; it returns every complete, CRC-valid frame
    and resynchronises on garbage (a 0x69 inside register data is rejected by
    the header constant + CRC8 check, a corrupt payload by the CRC16 check).
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.discarded = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames: list[Frame] = []
        while True:
            idx = self._buf.find(START_BYTE)
            if idx < 0:
                self.discarded += len(self._buf)
                self._buf.clear()
                break
            if idx:
                self.discarded += idx
                del self._buf[:idx]
            if len(self._buf) < HEADER_LEN:
                break
            if self._buf[3:6] != HEADER_CONST or crc8_dvb_s2(self._buf[:7]) != self._buf[7]:
                self._drop_one()
                continue
            total = HEADER_LEN + self._buf[6]
            if len(self._buf) < total:
                break
            try:
                frames.append(decode_frame(bytes(self._buf[:total])))
            except GecoFrameError:
                self._drop_one()
                continue
            del self._buf[:total]
        return frames

    def _drop_one(self) -> None:
        self.discarded += 1
        del self._buf[:1]

    def clear(self) -> None:
        self._buf.clear()
