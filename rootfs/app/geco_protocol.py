"""
GECO Protocol Implementation for Hewalex heat pumps and solar controllers.

Protocol structure (reverse-engineered from):
  - https://github.com/aelias-eu/hewalex-geco-protocol
  - https://github.com/mvdklip/Domoticz-Hewalex
  - https://www.elektroda.pl/rtvforum/topic3499254.html

Packet format:
  HEADER (8 bytes):
    [0]    0x69        Start marker
    [1]    dst_hard    Target physical RS485 address
    [2]    src_hard    Sender physical RS485 address
    [3]    0x84        Fixed header byte
    [4]    0x00        Fixed
    [5]    0x00        Fixed
    [6]    payload_len Payload byte count (everything after header)
    [7]    hdr_crc     CRC-8/DVB-S2 over bytes [0..6]

  PAYLOAD (variable length):
    [0]    dst_soft    Target logical address
    [1]    0x00        Reserved
    [2]    src_soft    Sender logical address
    [3]    0x00        Reserved
    [4]    fnc         Function code
    [5..]  data        Function-specific data
    [-2:]  crc16       CRC-16 over payload bytes [0..-2]

Function codes:
  0x40 = Read status registers (request)
  0x50 = Read status registers (response)
  0x60 = Read/write config registers (request)
  0x70 = Read/write config registers (response)
"""

import struct
import logging

logger = logging.getLogger(__name__)

START_BYTE = 0x69
HEADER_FIXED_BYTE = 0x84
HEADER_LEN = 8

FNC_READ_STATUS_REQ = 0x40
FNC_READ_STATUS_RESP = 0x50
FNC_READ_CONFIG_REQ = 0x60
FNC_READ_CONFIG_RESP = 0x70
FNC_WRITE_CONFIG_REQ = 0x60
FNC_WRITE_CONFIG_RESP = 0x70


def crc8_dvb_s2(data: bytes, crc: int = 0) -> int:
    """CRC-8/DVB-S2 used for the packet header."""
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def crc16(data: bytes, crc: int = 0) -> int:
    """CRC-16 used for the payload. Algorithm from hewalex2mqtt.py."""
    msb = (crc >> 8) & 0xFF
    lsb = crc & 0xFF
    for x in data:
        x = x ^ msb
        x ^= (x >> 4)
        msb = (lsb ^ (x >> 3) ^ (x << 4)) & 0xFF
        lsb = (x ^ (x << 5)) & 0xFF
    return (msb << 8) + lsb


def build_header(dst_hard: int, src_hard: int, payload_len: int) -> bytes:
    """Build the 8-byte packet header."""
    header_data = bytes([
        START_BYTE,
        dst_hard,
        src_hard,
        HEADER_FIXED_BYTE,
        0x00,
        0x00,
        payload_len & 0xFF,
    ])
    hdr_crc = crc8_dvb_s2(header_data)
    return header_data + bytes([hdr_crc])


def build_payload(dst_soft: int, src_soft: int, fnc: int, data: bytes) -> bytes:
    """Build a payload with addressing, function code, data, and CRC-16."""
    payload_no_crc = bytes([
        dst_soft, 0x00,
        src_soft, 0x00,
        fnc
    ]) + data
    crc = crc16(payload_no_crc)
    return payload_no_crc + struct.pack('>H', crc)


def build_read_request(dst_hard: int, src_hard: int,
                       dst_soft: int, src_soft: int,
                       fnc: int, reg_start: int, reg_count: int) -> bytes:
    """
    Build a register read request packet.

    For status registers: fnc=0x40, reg_start=100
    For config registers: fnc=0x60, reg_start=300
    """
    # Function-specific data: [0x80, 0x00, reg_count, reg_start, 0x00]
    func_data = bytes([0x80, 0x00, reg_count & 0xFF, reg_start & 0xFF, 0x00])
    payload = build_payload(dst_soft, src_soft, fnc, func_data)
    header = build_header(dst_hard, src_hard, len(payload))
    return header + payload


def build_write_request(dst_hard: int, src_hard: int,
                        dst_soft: int, src_soft: int,
                        reg_start: int, reg_index: int,
                        value: int, total_regs: int = 50) -> bytes:
    """
    Build a config register write request.

    The write uses FNC 0x60 with a different sub-function.
    reg_index is the register offset from reg_start.
    value is the 16-bit value to write.
    """
    # Write single register format:
    # FNC=0x60, sub_fnc=0xA0, 0x00, total_regs, reg_start,
    # then reg_index as 2-byte offset into the block,
    # then 2-byte value
    func_data = bytes([
        0xA0, 0x00,
        total_regs & 0xFF,
        reg_start & 0xFF,
        0x00,
    ])
    # Register offset within the block (which register to write)
    func_data += struct.pack('>H', reg_index)
    # Value to write
    func_data += struct.pack('>H', value & 0xFFFF)

    payload = build_payload(dst_soft, src_soft, FNC_WRITE_CONFIG_REQ, func_data)
    header = build_header(dst_hard, src_hard, len(payload))
    return header + payload


def parse_packet(data: bytes) -> dict | None:
    """
    Parse a GECO protocol packet.

    Returns a dict with header info, function code, and register data,
    or None if the packet is invalid.
    """
    if len(data) < HEADER_LEN + 7:  # minimum: header + minimal payload
        logger.debug("Packet too short: %d bytes", len(data))
        return None

    if data[0] != START_BYTE:
        logger.debug("Invalid start byte: 0x%02X", data[0])
        return None

    # Parse header
    dst_hard = data[1]
    src_hard = data[2]
    payload_len = data[6]
    hdr_crc_expected = data[7]

    # Verify header CRC
    hdr_crc_calc = crc8_dvb_s2(data[0:7])
    if hdr_crc_calc != hdr_crc_expected:
        logger.debug("Header CRC mismatch: calc=0x%02X expected=0x%02X",
                      hdr_crc_calc, hdr_crc_expected)
        return None

    # Extract payload
    if len(data) < HEADER_LEN + payload_len:
        logger.debug("Incomplete payload: have %d, need %d",
                      len(data) - HEADER_LEN, payload_len)
        return None

    payload = data[HEADER_LEN:HEADER_LEN + payload_len]

    # Verify payload CRC
    if len(payload) < 7:
        logger.debug("Payload too short for CRC check")
        return None

    payload_data = payload[:-2]
    payload_crc_expected = struct.unpack('>H', payload[-2:])[0]
    payload_crc_calc = crc16(payload_data)

    if payload_crc_calc != payload_crc_expected:
        logger.debug("Payload CRC mismatch: calc=0x%04X expected=0x%04X",
                      payload_crc_calc, payload_crc_expected)
        return None

    # Parse payload fields
    dst_soft = payload[0]
    src_soft = payload[2]
    fnc = payload[4]

    result = {
        'dst_hard': dst_hard,
        'src_hard': src_hard,
        'dst_soft': dst_soft,
        'src_soft': src_soft,
        'fnc': fnc,
        'raw_payload': payload_data,
        'total_len': HEADER_LEN + payload_len,
    }

    # Parse function-specific data
    if fnc in (FNC_READ_STATUS_RESP, FNC_READ_CONFIG_RESP):
        # Response format: [addr(4)] [fnc(1)] [sub(1)] [0x00] [reg_count] [reg_start] [data...]
        if len(payload_data) >= 9:
            sub_fnc = payload_data[5]
            reg_count = payload_data[7]
            reg_start = payload_data[8]
            reg_data = payload_data[9:]

            result['sub_fnc'] = sub_fnc
            result['reg_start'] = reg_start
            result['reg_count'] = reg_count
            result['reg_data'] = reg_data

            logger.debug("Response: FNC=0x%02X sub=0x%02X start=%d count=%d data_len=%d",
                          fnc, sub_fnc, reg_start, reg_count, len(reg_data))

    elif fnc in (FNC_READ_STATUS_REQ, FNC_READ_CONFIG_REQ):
        if len(payload_data) >= 9:
            sub_fnc = payload_data[5]
            reg_count = payload_data[7]
            reg_start = payload_data[8]
            result['sub_fnc'] = sub_fnc
            result['reg_start'] = reg_start
            result['reg_count'] = reg_count

    return result


def find_packets(buffer: bytes) -> list[tuple[dict, int]]:
    """
    Find all valid GECO packets in a byte buffer.

    Returns list of (parsed_packet, end_position) tuples.
    Useful for eavesdropping mode where multiple packets may arrive.
    """
    packets = []
    pos = 0

    while pos < len(buffer):
        # Find next start byte
        idx = buffer.find(bytes([START_BYTE]), pos)
        if idx == -1:
            break

        remaining = buffer[idx:]
        if len(remaining) < HEADER_LEN:
            break

        # Check if we have enough data for the full packet
        payload_len = remaining[6]
        total_len = HEADER_LEN + payload_len

        if len(remaining) < total_len:
            break

        packet_data = remaining[:total_len]
        parsed = parse_packet(packet_data)

        if parsed is not None:
            packets.append((parsed, idx + total_len))
            pos = idx + total_len
        else:
            pos = idx + 1

    return packets


def extract_registers(reg_data: bytes, count: int) -> list[int]:
    """
    Extract 16-bit register values from raw register data.

    Each register is 2 bytes, big-endian unsigned.
    Returns list of unsigned 16-bit integers.
    """
    registers = []
    for i in range(min(count, len(reg_data) // 2)):
        val = struct.unpack('>H', reg_data[i*2:i*2+2])[0]
        registers.append(val)
    return registers


def signed16(value: int) -> int:
    """Convert unsigned 16-bit to signed (two's complement)."""
    if value >= 0x8000:
        return value - 0x10000
    return value
