"""
Read-only test that runs once when direct mode finds itself on the cable between
the PCWU and its G-426 controller instead of on the PCWU's free RS485 port.

On that cable the PCWU is master: every 500 ms it reads registers 100 and 252
from the controller and writes its status block (120, 104 bytes) to it. The test
waits for the quiet gap after a cycle and then asks:

1. the configured device address for the status block, three times. Bits 13..15
   of register 196 are a message counter only present in the copy the PCWU
   writes to the controller, so a non-zero counter means the controller answered.
2. the configured device address for the settings block, so the values can be
   compared with the controller menu.
3. the PCWU itself, on the address it uses on this cable and on the factory
   address of its free port (255/65535), from an unused address. An answer
   would mean the PCWU also accepts requests on this cable.

Nothing is ever written.
"""

from __future__ import annotations

import logging
import struct
import time

import pcwu
from bus import GecoClient, GecoDeviceError
from geco import FNC_READ_RESPONSE, FNC_WRITE, Frame, FrameParser, GecoError

logger = logging.getLogger("hewalex.cable_test")

SETTINGS_SHOWN = ("HeatPumpEnabled", "TapWaterTemp", "TapWaterHysteresis", "TapWaterSensor", "AmbientMinTemp",
                  "HeaterEEnabled", "FanOperationMode", "WaterPumpOperationMode", "InstallationScheme")
STATUS_COMPARED = ("T1", "T2", "T3", "T6", "T7", "T8", "T9", "T10")


def bus_parties(frames: list[Frame]) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """(PCWU address, controller address) as seen in the PCWU's own status write."""
    for frame in frames:
        if frame.fnc == FNC_WRITE and frame.start == pcwu.STATUS_START:
            return (frame.src_hard, frame.src_soft), (frame.dst_hard, frame.dst_soft)
    for frame in frames:
        return (frame.src_hard, frame.src_soft), (frame.dst_hard, frame.dst_soft)
    return None, None


def wait_for_gap(client: GecoClient, timeout: float = 2.0) -> bool:
    """Return right after the controller answered the PCWU's last read of a cycle (register 252)."""
    parser = FrameParser()
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        for frame in parser.feed(client.transport.read(min(remaining, 0.25))):
            if frame.fnc == FNC_READ_RESPONSE and frame.start == 252:
                return True
    return False


def probe(client: GecoClient, start: int, count: int) -> bytes | str:
    wait_for_gap(client)
    try:
        return client.read(start, count)
    except GecoDeviceError as exc:
        return f"answered with an error ({exc})"
    except GecoError:
        return "no answer"


def _image(start: int, data: bytes) -> pcwu.RegisterImage:
    image = pcwu.RegisterImage()
    image.update(start, data)
    return image


def _fmt(key: str, image: pcwu.RegisterImage) -> str:
    point = pcwu.POINTS_BY_KEY[key]
    value = pcwu.decode(point, image)
    if isinstance(value, bool):
        value = "on" if value else "off"
    return f"{point.name} = {value}{' ' + point.unit if point.unit and value is not None else ''}"


def run(client: GecoClient, broadcast: pcwu.RegisterImage) -> list[str]:
    """Run the test and return the report lines (also logged)."""
    pump, controller = bus_parties(client.foreign_requests)
    dev = (client.dev_hard, client.dev_soft)
    client = GecoClient(client.transport, client.con_hard, client.con_soft, dev[0], dev[1], timeout=0.3, retries=0)
    report = ["RS485 cable test (read only, nothing is written)"]
    if pump:
        report.append(f"  The PCWU ({pump[0]}/{pump[1]}) is master on this line and polls the G-426 controller "
                      f"({controller[0]}/{controller[1]}): this is the controller cable, not the free RS485 port.")

    # 1. who answers on the configured device address?
    counters = []
    first = None
    for _ in range(3):
        result = probe(client, pcwu.STATUS_START, 104)
        if isinstance(result, str):
            report.append(f"  Test 1, status read from {dev[0]}/{dev[1]}: {result}.")
            break
        first = first or result
        counters.append(struct.unpack_from("<H", result, 196 - pcwu.STATUS_START)[0] >> 13)
    controller_answered = any(counters)
    if counters:
        image = _image(pcwu.STATUS_START, first)
        pairs = [(pcwu.decode(pcwu.POINTS_BY_KEY[k], image), pcwu.decode(pcwu.POINTS_BY_KEY[k], broadcast))
                 for k in STATUS_COMPARED]
        if any(b is None for _, b in pairs):
            compared = "not compared (no status frame of the PCWU stored yet)"
        elif all(a is not None and abs(a - b) <= 0.5 for a, b in pairs):
            compared = "match the PCWU's own status frames"
        else:
            compared = "differ from the PCWU's own status frames"
        who = (f"the G-426 controller answered (register 196 carries its message counter: "
               f"{', '.join(map(str, counters))})" if controller_answered else
               "the answer has no controller message counter: probably the PCWU itself answered")
        report.append(f"  Test 1, status read from {dev[0]}/{dev[1]}: {who}; temperatures {compared}.")

    # 2. settings as held by whoever answers on the configured address
    result = probe(client, pcwu.CONFIG_START, pcwu.MAX_READ_BYTES)
    if isinstance(result, str):
        report.append(f"  Test 2, settings read from {dev[0]}/{dev[1]}: {result}.")
    elif controller_answered:
        words = struct.unpack_from("<16H", result)
        report.append(f"  Test 2, settings read from {dev[0]}/{dev[1]}: answered, but the controller keeps its "
                      "settings in its own register layout, not the PCWU's, so they cannot be decoded as PCWU "
                      "settings. First words: "
                      + ", ".join(f"{pcwu.CONFIG_START + 2 * i}={w}" for i, w in enumerate(words)) + ".")
    else:
        image = _image(pcwu.CONFIG_START, result)
        report.append(f"  Test 2, settings read from {dev[0]}/{dev[1]}: "
                      + "; ".join(_fmt(k, image) for k in SETTINGS_SHOWN)
                      + ". Compare these with the controller menu.")

    # 3. does the PCWU itself answer on this cable?
    pcwu_answers = False
    if pump:
        used = {pump[0], controller[0], client.con_hard, client.dev_hard}
        own = next(i for i in range(3, 255) if i not in used)
        for label, (hard, soft) in (("the PCWU", pump), ("the free port's factory address", (255, 65535))):
            direct = GecoClient(client.transport, own, own, hard, soft, timeout=0.3, retries=0)
            result = probe(direct, pcwu.STATUS_START, 104)
            if isinstance(result, str):
                report.append(f"  Test 3, status read from {label} ({hard}/{soft}) as {own}/{own}: {result}.")
            else:
                pcwu_answers = True
                report.append(f"  Test 3, status read from {label} ({hard}/{soft}) as {own}/{own}: answered - "
                              "the PCWU does accept requests on this cable.")
        if not pcwu_answers:
            report.append("  Conclusion: on this cable only the controller answers; the PCWU does not accept "
                          "requests here, so its free RS485 port is not bridged onto this cable.")

    report.append("  Writing stays blocked on this cable: the PCWU is master here and polls its controller "
                  "twice a second, so a second master collides with it"
                  + (", and requests to the configured address are answered by the controller, not by the PCWU. "
                     if controller_answered and not pcwu_answers else ". ")
                  + "Connect the adapter to the free RS485 port of the PCWU to change settings. "
                  "Continuing read only.")
    for line in report:
        logger.warning("%s", line)
    return report
