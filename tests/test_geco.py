import random

import pytest

from geco import (
    FNC_READ, FNC_READ_RESPONSE, FNC_WRITE, FNC_WRITE_RESPONSE, MARKER_ERROR, Frame, FrameParser,
    GecoFrameError, crc8_dvb_s2, crc16_xmodem, decode_frame, read_request, write_request,
)
from hewalex_geco import crc as ref_crc
from hewalex_geco.devices import PCWU as RefPCWU


def test_crc_standard_check_values():
    assert crc8_dvb_s2(b"123456789") == 0xBC     # CRC-8/DVB-S2 check value
    assert crc16_xmodem(b"123456789") == 0x31C3  # CRC-16/XMODEM check value


def test_crc_against_frames_captured_on_a_real_pcwu():
    # Examples from "Geco Proto.pdf" (krzysztof1111111111), captured on the bus.
    assert crc8_dvb_s2(bytes.fromhex("6902018400000c")) == 0xF6
    # 0x70 acknowledge of the 104 byte status write at 120, display (2) -> pump (1)
    assert crc16_xmodem(bytes.fromhex("01000200708000687800")) == 0x3495


def test_crc_matches_reference_on_random_data():
    rnd = random.Random(1)
    for _ in range(500):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 240)))
        assert crc8_dvb_s2(data) == ref_crc.crc8(data)
        assert crc16_xmodem(data) == ref_crc.crc16(data)


@pytest.mark.parametrize("ids", [(1, 1, 2, 2), (1, 1, 5, 6), (3, 300, 7, 513)])
@pytest.mark.parametrize("start,count", [(120, 182), (302, 226), (528, 10), (308, 2), (100, 20)])
def test_read_request_identical_to_reference(ids, start, count):
    con_hard, con_soft, dev_hard, dev_soft = ids
    ref = RefPCWU(con_hard, con_soft, dev_hard, dev_soft).createReadRegistersMessage(start, count)
    ours = read_request(dev_hard, con_hard, dev_soft, con_soft, start, count).encode()
    assert ours == bytes(ref)


@pytest.mark.parametrize("ids", [(1, 1, 2, 2), (3, 300, 7, 513)])
@pytest.mark.parametrize("address,value", [(304, 0), (304, 1), (308, 480), (334, (-70) & 0xFFFF), (330, 2)])
def test_write_request_identical_to_reference(ids, address, value):
    con_hard, con_soft, dev_hard, dev_soft = ids
    ref = RefPCWU(con_hard, con_soft, dev_hard, dev_soft).createWriteRegisterMessage(address, value)
    ours = write_request(dev_hard, con_hard, dev_soft, con_soft, address,
                         bytes((value & 0xFF, value >> 8))).encode()
    assert ours == bytes(ref)


def test_reference_accepts_our_response_frames():
    """Frames we decode are the frames the reference library accepts (and vice versa)."""
    data = bytes(range(182))
    raw = Frame(1, 2, 1, 2, FNC_READ_RESPONSE, count=len(data), start=120, data=data).encode()
    seen = []
    ref = RefPCWU(1, 1, 2, 2, onMessage=lambda obj, h, sh, m: seen.append(sh))
    ref.processAllMessages(raw)
    assert seen[0]["FNC"] == 0x50 and seen[0]["RegStart"] == 120 and seen[0]["RegLen"] == 182
    assert seen[0]["RestMessage"] == data
    frame = decode_frame(raw)
    assert (frame.fnc, frame.start, frame.count, frame.data) == (FNC_READ_RESPONSE, 120, 182, data)


def test_decode_roundtrip_all_functions():
    for frame in (
        Frame(2, 1, 2, 1, FNC_READ, count=226, start=302),
        Frame(1, 2, 1, 2, FNC_READ_RESPONSE, count=4, start=252, data=b"\x10\x00\x00\x00"),
        Frame(2, 1, 2, 1, FNC_WRITE, count=2, start=304, data=b"\x01\x00"),
        Frame(1, 2, 1, 2, FNC_WRITE_RESPONSE, count=2, start=304),
        Frame(1, 2, 1, 2, FNC_READ_RESPONSE, marker=MARKER_ERROR, data=b"\x04"),
    ):
        assert decode_frame(frame.encode()) == frame


def test_error_frame():
    frame = decode_frame(Frame(1, 2, 1, 2, FNC_READ_RESPONSE, marker=MARKER_ERROR, data=b"\x04").encode())
    assert frame.is_error and frame.data == b"\x04"


def test_decode_rejects_corruption():
    raw = bytearray(Frame(1, 2, 1, 2, FNC_READ_RESPONSE, count=4, start=252, data=b"\x10\0\0\0").encode())
    for index in range(len(raw)):
        broken = bytearray(raw)
        broken[index] ^= 0x01
        with pytest.raises(GecoFrameError):
            decode_frame(bytes(broken))


def test_parser_resynchronises_and_splits():
    frames = [
        Frame(1, 2, 1, 2, FNC_READ_RESPONSE, count=6, start=128, data=b"\x69\x00\x69\x84\x00\x00"),
        Frame(1, 2, 1, 2, FNC_WRITE_RESPONSE, count=2, start=304),
        Frame(2, 1, 2, 1, FNC_WRITE, count=104, start=120, data=bytes([0x69] * 104)),
    ]
    corrupt = bytearray(frames[0].encode())
    corrupt[-1] ^= 0xFF
    stream = b"\x00\x69\x69garbage" + bytes(corrupt) + frames[0].encode() + b"\x69\x02" \
        + frames[1].encode() + frames[2].encode() + b"\x69\x01\x02\x84"
    parser = FrameParser()
    got = []
    rnd = random.Random(7)
    pos = 0
    while pos < len(stream):
        step = rnd.randrange(1, 9)
        got += parser.feed(stream[pos:pos + step])
        pos += step
    assert got == frames
