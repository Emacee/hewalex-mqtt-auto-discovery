import random
import struct

import pytest

import pcwu
from hewalex_geco.devices import PCWU as RefPCWU

REF = RefPCWU(1, 1, 2, 2)
REF_BY_NAME = {reg["name"]: (addr, reg) for addr, reg in REF.registers.items() if isinstance(reg["name"], str)}
DERIVED = {"OperatingState", "ControllerOn"}


def random_image(seed):
    rnd = random.Random(seed)
    return bytes(rnd.randrange(256) for _ in range(pcwu.REG_END - pcwu.STATUS_START))


def reference_values(blob):
    """Decode a full 120..537 image with the reference library, block by block like it polls."""
    values = {}
    for start, count in (pcwu.STATUS_BLOCK, (302, 226), (528, 10)):
        chunk = blob[start - pcwu.STATUS_START:start - pcwu.STATUS_START + count]
        values.update(REF.parseRegisters(chunk, start, count))
    return values


def test_every_register_point_has_the_reference_address_and_type():
    for point in pcwu.POINTS:
        if point.key in DERIVED:
            continue
        if point.kind == "bit":
            mask = REF.registers[point.address]
            assert mask["type"] == "mask" and mask["name"][point.bit] == point.key
            continue
        address, reg = REF_BY_NAME[point.key]
        assert point.address == address, point.key
        expected = {"te10": "temp", "word": "u16", "bool": "bool", "tprg": "tprg"}[reg["type"]]
        assert point.kind == expected, point.key


@pytest.mark.parametrize("seed", range(25))
def test_decoding_matches_reference_on_random_register_images(seed):
    blob = random_image(seed)
    image = pcwu.RegisterImage()
    image.update(pcwu.STATUS_START, blob)
    ref = reference_values(blob)
    for point in pcwu.POINTS:
        if point.key in DERIVED:
            continue
        ours = pcwu.decode(point, image)
        expected = ref[point.key]
        if point.kind == "tprg":
            mask = sum(1 << hour for hour, on in expected.items() if on)
            assert ours == pcwu.format_schedule(mask), point.key
        elif point.options:
            assert ours == point.options.get(expected, str(expected)), point.key
        else:
            assert ours == expected, point.key


def test_status_values_from_known_bytes():
    image = pcwu.RegisterImage()
    words = {128: 85, 130: 452, 142: (-123) & 0xFFFF, 194: 2, 196: 0x0805, 198: 250, 202: 0, 308: 480,
             304: 1, 330: 2, 334: (-70) & 0xFFFF}
    for address, value in words.items():
        image.update(address, struct.pack("<H", value))
    get = lambda key: pcwu.decode(pcwu.POINTS_BY_KEY[key], image)  # noqa: E731
    assert get("T1") == 8.5
    assert get("T2") == 45.2
    assert get("T8") == -12.3
    assert get("FanON") is True and get("WaterPumpON") is True and get("CompressorON") is True
    assert get("HeaterEON") is False
    assert get("EV1") == 250
    assert get("TapWaterTemp") == 48.0
    assert get("HeatPumpEnabled") is True
    assert get("FanOperationMode") == "Day/Night"
    assert get("DefrostingStartTemp") == -7.0
    assert get("OperatingState") == "Heating"
    assert get("ControllerOn") is True
    assert get("T3") is None  # never read


def test_schedule_example_from_protocol_document():
    # "0x3FFFC0 ... the pump is turned off at 0..5, then on until it is turned off again at 22 and 23"
    assert pcwu.format_schedule(0x3FFFC0) == "06:00-22:00"
    assert pcwu.format_schedule(0) == "off"
    assert pcwu.format_schedule(0xFFFFFF) == "00:00-24:00"
    assert pcwu.format_schedule(0b1000000000000000000000011) == "00:00-02:00"
    assert pcwu.format_schedule(0x800001) == "00:00-01:00, 23:00-24:00"


@pytest.mark.parametrize("controller,flags,ws,expected", [
    (1, 0x0805, 0, "Off (controller off)"),
    (2, 0, 2, "Waiting: disabled"),
    (2, 0, 1, "Waiting: external stop"),
    (2, 0, 32, "Waiting: restart delay"),
    (2, 0, 64, "Waiting: ambient too cold"),
    (2, 0, 4, "Error: low pressure"),
    (2, 0, 8, "Error: high pressure"),
    (2, 0, 1024, "Error: overheating"),
    (2, 0x0805, 0, "Heating"),
    (2, 0x0001, 16, "Defrosting"),
    (2, 0x0001, 512, "Compressor preheating"),
    (2, 0x1000, 0, "Electric heater"),
    (2, 0, 0, "Idle"),
])
def test_operating_state(controller, flags, ws, expected):
    image = pcwu.RegisterImage()
    for address, value in ((194, controller), (196, flags), (202, ws)):
        image.update(address, struct.pack("<H", value))
    assert pcwu.operating_state(image) == expected
    assert expected in pcwu.STATE_OPTIONS


def point(key):
    return pcwu.POINTS_BY_KEY[key]


def test_encode_numbers():
    assert pcwu.encode(point("TapWaterTemp"), "48") == 480
    assert pcwu.encode(point("TapWaterTemp"), "48.0") == 480
    assert pcwu.encode(point("TapWaterTemp"), "60") == 600
    assert pcwu.encode(point("AmbientMinTemp"), "-7") == (-70) & 0xFFFF
    assert pcwu.encode(point("DefrostingStartTemp"), "-30") == (-300) & 0xFFFF
    assert pcwu.encode(point("DefrostingInterval"), "45") == 45


@pytest.mark.parametrize("key,payload", [
    ("TapWaterTemp", "61"), ("TapWaterTemp", "9"), ("TapWaterTemp", "47.5"), ("TapWaterTemp", "nan"),
    ("TapWaterTemp", "abc"), ("TapWaterHysteresis", "1"), ("DefrostingMaxTime", "13"),
    ("HeatPumpEnabled", "maybe"), ("FanOperationMode", "Turbo"), ("T1", "20"), ("EV1", "100"),
])
def test_encode_rejects_invalid(key, payload):
    with pytest.raises(ValueError):
        pcwu.encode(point(key), payload)


def test_encode_switches_and_selects():
    assert pcwu.encode(point("HeatPumpEnabled"), "ON") == 1
    assert pcwu.encode(point("HeatPumpEnabled"), "OFF") == 0
    assert pcwu.encode(point("FanOperationMode"), "Min") == 1
    assert pcwu.encode(point("TapWaterSensor"), "T7 (water outlet)") == 2
    assert pcwu.encode(point("CirculationPumpOperationMode"), "Interrupted") == 1


def test_encode_then_decode_roundtrip_for_every_writable_value():
    for p in pcwu.POINTS:
        if not p.writable:
            continue
        if p.platform == "switch":
            payloads = ["ON", "OFF"]
        elif p.platform == "select":
            payloads = list(p.options.values())
        else:
            payloads = [str(p.minimum + i * p.step) for i in range(int((p.maximum - p.minimum) / p.step) + 1)]
        for payload in payloads:
            image = pcwu.RegisterImage()
            image.update(p.address, struct.pack("<H", pcwu.encode(p, payload)))
            value = pcwu.decode(p, image)
            if p.platform == "switch":
                assert value is (payload == "ON")
            elif p.platform == "select":
                assert value == payload
            else:
                assert value == float(payload)


def test_writable_points_are_settings_registers_only():
    for p in pcwu.POINTS:
        if p.writable:
            assert pcwu.CONFIG_START <= p.address < pcwu.REG_END and p.address % 2 == 0
            assert p.address not in range(302, 304), "installation scheme must never be written"
            assert p.address not in range(344, 356), "expansion valve settings must never be written"


def test_eavesdrop_exposes_only_status():
    exposed = pcwu.points_for(expert=True, writable_allowed=False)
    assert exposed and not any(p.writable for p in exposed)
    assert all(p.address is None or p.address < pcwu.CONFIG_START for p in exposed)
    assert pcwu.POINTS_BY_KEY["OperatingState"] in exposed


def test_expert_points_hidden_by_default():
    exposed = pcwu.points_for(expert=False, writable_allowed=True)
    assert not any(p.expert for p in exposed)
    assert pcwu.POINTS_BY_KEY["TapWaterTemp"] in exposed


@pytest.mark.parametrize("flags,reg194,expected_on,expected_state", [
    (None, 2, True, "Heating"),            # free port: register 194 (2 = on)
    (None, 1, False, "Off (controller off)"),
    (0x10, 1, True, "Heating"),            # controller cable: the G-426 itself says it is on
    (0x11, 1, True, "Heating"),            # on, parameter changed
    (0x08, 2, False, "Off (controller off)"),
])
def test_controller_state_prefers_controller_report(flags, reg194, expected_on, expected_state):
    image = pcwu.RegisterImage()
    for address, value in ((194, reg194), (196, 0x0805), (202, 0)):
        image.update(address, struct.pack("<H", value))
    image.controller_flags = flags
    assert pcwu.decode(pcwu.POINTS_BY_KEY["ControllerOn"], image) is expected_on
    assert pcwu.operating_state(image) == expected_state
