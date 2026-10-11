"""
Hewalex PCWU register map (executive module MG-426, controller PG-426).

Register addresses are BYTE addresses. Values are 16-bit little-endian words at
even addresses unless stated otherwise. Source: mvdklip/Domoticz-Hewalex
hewalex_geco/devices/pcwu.py (tested on firmware v0.1n / v0.1o) and the PCWU
user manual for the allowed setting ranges.

Only settings with a known meaning AND a known valid range are writable. The
PCWU silently replaces an out-of-range value by its factory default, so ranges
are enforced here and every write is verified by reading the register back.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

CONTROLLER_STATUS = 252     # register of the G-426 the PCWU reads every cycle (controller cable)
CONTROLLER_ON = 0x10        # ... its first byte: 0x10 display on, 0x08 display off,
                            #     +0x01 parameter changed, +0x04 date/time changed
STATUS_START = 120          # 120..301 status block (read only)
CONFIG_START = 302          # 302..537 settings block (read/write)
REG_END = 538               # first address that cannot be read
MAX_READ_BYTES = 226        # largest block the PCWU returns in one response

# Blocks polled in direct mode: (start, byte count)
STATUS_BLOCK = (STATUS_START, CONFIG_START - STATUS_START)
CONFIG_BLOCK = (CONFIG_START, REG_END - CONFIG_START)

TEMP = "temperature"
CELSIUS = "°C"
MINUTES = "min"
DIAG = "diagnostic"
CONFIG = "config"


@dataclass(frozen=True)
class Point:
    """One value of the PCWU as exposed to Home Assistant."""

    key: str                      # stable id (unique_id / topic suffix)
    name: str                     # entity name (device name is prefixed by HA)
    platform: str                 # sensor, binary_sensor, switch, number, select
    address: int | None = None    # byte address; None for derived values
    kind: str = "u16"             # u16, temp, bool, bit, tprg, derived
    bit: int | None = None        # bit number for kind == "bit"
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    options: dict[int, str] = field(default_factory=dict)
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    category: str | None = None   # None, "config" or "diagnostic"
    enabled: bool = True          # enabled_by_default in HA
    expert: bool = False          # only exposed with expert_mode

    @property
    def writable(self) -> bool:
        return self.platform in ("switch", "number", "select")

    @property
    def size(self) -> int:
        return 4 if self.kind == "tprg" else 2


# Status flags in word 196 (bit 13..15 are a message counter towards the display)
BIT_FAN = 0
BIT_WATER_PUMP = 2
BIT_COMPRESSOR = 11
BIT_HEATER_E = 12

# Bits of WaitingStatus (202)
WS_EXT_OFF = 1
WS_DISABLED = 2
WS_LOW_PRESSURE = 4
WS_HIGH_PRESSURE = 8
WS_DEFROST = 16
WS_RESTART_DELAY = 32
WS_LOW_COP = 64
WS_TANK_FROST = 128
WS_HP_FROST = 256
WS_PREHEAT = 512
WS_OVERHEAT = 1024

STATE_OPTIONS = [
    "Off (controller off)",
    "Waiting: external stop",
    "Waiting: disabled",
    "Waiting: restart delay",
    "Waiting: ambient too cold",
    "Error: low pressure",
    "Error: high pressure",
    "Error: overheating",
    "Heating",
    "Defrosting",
    "Tank frost protection",
    "Frost protection",
    "Compressor preheating",
    "Electric heater",
    "Idle",
]


def _temp(key, name, address, **kw) -> Point:
    kw.setdefault("state_class", "measurement")
    return Point(key, name, "sensor", address, "temp", unit=CELSIUS, device_class=TEMP, **kw)


def _tprg(key, name, address, **kw) -> Point:
    return Point(key, name, "sensor", address, "tprg", icon="mdi:calendar-clock", category=DIAG, **kw)


POINTS: list[Point] = [
    # ── Status block (120..301) ─────────────────────────────────────────
    _temp("T1", "T1 ambient", 128),
    _temp("T2", "T2 tank bottom", 130),
    _temp("T3", "T3 tank top", 132),
    _temp("T4", "T4 solid fuel boiler", 134, enabled=False),
    _temp("T5", "T5", 136, enabled=False),
    _temp("T6", "T6 water inlet", 138),
    _temp("T7", "T7 water outlet", 140),
    _temp("T8", "T8 evaporator", 142),
    _temp("T9", "T9 before compressor", 144),
    _temp("T10", "T10 after compressor", 146),

    Point("ControllerOn", "Controller on", "binary_sensor", 194, "derived",
          device_class="power", category=DIAG),
    Point("FanON", "Fan", "binary_sensor", 196, "bit", bit=BIT_FAN,
          device_class="running", icon="mdi:fan"),
    Point("WaterPumpON", "Water pump", "binary_sensor", 196, "bit", bit=BIT_WATER_PUMP,
          device_class="running", icon="mdi:pump"),
    Point("CompressorON", "Compressor", "binary_sensor", 196, "bit", bit=BIT_COMPRESSOR,
          device_class="running", icon="mdi:heat-pump"),
    Point("HeaterEON", "Electric heater E", "binary_sensor", 196, "bit", bit=BIT_HEATER_E,
          device_class="running", icon="mdi:heating-coil"),

    Point("OperatingState", "Operating state", "sensor", None, "derived",
          device_class="enum", icon="mdi:state-machine"),
    Point("EV1", "Expansion valve position", "sensor", 198, unit="steps",
          state_class="measurement", icon="mdi:valve", category=DIAG),
    Point("WaitingStatus", "Waiting status code", "sensor", 202, icon="mdi:information-outline",
          category=DIAG),
    Point("WaitingTimer", "Restart timer", "sensor", 206, icon="mdi:timer-sand", category=DIAG),
    Point("IsManual", "Controller state code", "sensor", 194, icon="mdi:information-outline",
          category=DIAG, enabled=False),

    # ── Settings: heat pump (302..339) ──────────────────────────────────
    Point("InstallationScheme", "Installation scheme", "sensor", 302, icon="mdi:sitemap",
          category=DIAG),
    Point("HeatPumpEnabled", "Heat pump enabled", "switch", 304, "bool", icon="mdi:heat-pump-outline"),
    Point("TapWaterSensor", "Controlling sensor", "select", 306,
          options={0: "T2 (tank bottom)", 1: "T3 (tank top)", 2: "T7 (water outlet)"},
          icon="mdi:thermometer-check", category=CONFIG),
    Point("TapWaterTemp", "Hot water target", "number", 308, "temp", minimum=10, maximum=60,
          unit=CELSIUS, device_class=TEMP, icon="mdi:water-thermometer"),
    Point("TapWaterHysteresis", "Start hysteresis", "number", 310, "temp", minimum=2, maximum=10,
          unit=CELSIUS, icon="mdi:thermometer-lines", category=CONFIG),
    Point("AmbientMinTemp", "Minimum ambient temperature", "number", 312, "temp", minimum=-10, maximum=10,
          unit=CELSIUS, device_class=TEMP, icon="mdi:thermometer-low", category=CONFIG),
    _tprg("TimeProgramHPM-F", "Schedule heat pump Mon-Fri", 314),
    _tprg("TimeProgramHPSat", "Schedule heat pump Saturday", 318),
    _tprg("TimeProgramHPSun", "Schedule heat pump Sunday", 322),
    Point("AntiFreezingEnabled", "Frost protection", "switch", 326, "bool",
          icon="mdi:snowflake-alert", category=CONFIG),
    Point("WaterPumpOperationMode", "Water pump mode", "select", 328,
          options={0: "Continuous", 1: "Synchronous"}, icon="mdi:pump", category=CONFIG),
    Point("FanOperationMode", "Fan mode", "select", 330,
          options={0: "Max", 1: "Min", 2: "Day/Night"}, icon="mdi:fan", category=CONFIG),
    Point("DefrostingInterval", "Defrost interval", "number", 332, minimum=30, maximum=90,
          unit=MINUTES, icon="mdi:timer-cog-outline", category=CONFIG, expert=True),
    Point("DefrostingStartTemp", "Defrost start temperature", "number", 334, "temp", minimum=-30, maximum=0,
          unit=CELSIUS, device_class=TEMP, icon="mdi:snowflake-thermometer", category=CONFIG, expert=True),
    Point("DefrostingStopTemp", "Defrost stop temperature", "number", 336, "temp", minimum=2, maximum=30,
          unit=CELSIUS, device_class=TEMP, icon="mdi:snowflake-off", category=CONFIG, expert=True),
    Point("DefrostingMaxTime", "Defrost maximum duration", "number", 338, minimum=1, maximum=12,
          unit=MINUTES, icon="mdi:timer-outline", category=CONFIG, expert=True),

    # ── Settings: expansion valve (344..355) – read only on purpose ─────
    Point("EVOperationMode", "Expansion valve mode", "sensor", 344, "u16",
          options={0: "Auto", 1: "Manual"}, device_class="enum", icon="mdi:valve", category=DIAG,
          enabled=False, expert=True),

    # ── Settings: electric heater E (364..385) ──────────────────────────
    Point("HeaterEEnabled", "Electric heater E enabled", "switch", 364, "bool", icon="mdi:heating-coil"),
    Point("HeaterEHPONTemp", "Heater E target (heat pump on)", "number", 366, "temp", minimum=30, maximum=60,
          unit=CELSIUS, device_class=TEMP, icon="mdi:water-thermometer", category=CONFIG),
    Point("HeaterEHPOFFTemp", "Heater E target (heat pump off)", "number", 368, "temp", minimum=30, maximum=60,
          unit=CELSIUS, device_class=TEMP, icon="mdi:water-thermometer", category=CONFIG),
    Point("HeaterEBlocked", "Heater E blocked while heat pump runs", "switch", 370, "bool",
          icon="mdi:lock-outline", category=CONFIG),
    _tprg("HeaterETimeProgramM-F", "Schedule heater E Mon-Fri", 374, enabled=False),
    _tprg("HeaterETimeProgramSat", "Schedule heater E Saturday", 378, enabled=False),
    _tprg("HeaterETimeProgramSun", "Schedule heater E Sunday", 382, enabled=False),

    # ── Settings: heater P (396..417) ───────────────────────────────────
    Point("HeaterPEnabled", "Heater P enabled", "switch", 396, "bool", icon="mdi:heating-coil",
          category=CONFIG, expert=True),
    _temp("HeaterPHPONTemp", "Heater P target (heat pump on)", 398, state_class=None, category=DIAG,
          expert=True),
    _temp("HeaterPHPOFFTemp", "Heater P target (heat pump off)", 400, state_class=None, category=DIAG,
          expert=True),
    Point("HeaterPBlocked", "Heater P blocked while heat pump runs", "switch", 402, "bool",
          icon="mdi:lock-outline", category=CONFIG, expert=True),

    # ── Settings: circulation pump C (428..443) ─────────────────────────
    _temp("CirculationPumpMinTemp", "Circulation pump minimum temperature", 428, state_class=None,
          category=DIAG, expert=True),
    Point("CirculationPumpOperationMode", "Circulation pump mode", "select", 430,
          options={0: "Continuous", 1: "Interrupted"}, icon="mdi:pump", category=CONFIG, expert=True),

    # ── Settings: boiler pump F / solid fuel boiler B (454..461) ────────
    _temp("BoilerPumpMaxTemp", "Boiler pump maximum temperature", 454, state_class=None, category=DIAG,
          expert=True),
    _temp("BoilerPumpMinTemp", "Boiler pump minimum temperature", 456, state_class=None, category=DIAG,
          expert=True),
    Point("BoilerPumpPriority", "Boiler pump priority", "switch", 460, "bool", icon="mdi:priority-high",
          category=CONFIG, expert=True),

    # ── Settings: anti-legionella (498..503) ────────────────────────────
    Point("AntiLegionellaEnabled", "Anti-legionella", "switch", 498, "bool", icon="mdi:bacteria-outline",
          category=CONFIG),
    Point("AntiLegionellaUseHeaterE", "Anti-legionella uses heater E", "switch", 500, "bool",
          icon="mdi:heating-coil", category=CONFIG, expert=True),
    Point("AntiLegionellaUseHeaterP", "Anti-legionella uses heater P", "switch", 502, "bool",
          icon="mdi:heating-coil", category=CONFIG, expert=True),

    # ── Settings: external controller contact (516..525) ────────────────
    Point("ExtControllerHPOFF", "External contact stops heat pump", "switch", 516, "bool",
          icon="mdi:electric-switch", category=CONFIG, expert=True),
    Point("ExtControllerHeaterEOFF", "External contact stops heater E", "switch", 518, "bool",
          icon="mdi:electric-switch", category=CONFIG, expert=True),
    Point("ExtControllerPumpFOFF", "External contact stops pump F", "switch", 522, "bool",
          icon="mdi:electric-switch", category=CONFIG, expert=True),
    Point("ExtControllerHeaterPOFF", "External contact stops heater P", "switch", 524, "bool",
          icon="mdi:electric-switch", category=CONFIG, expert=True),
]

POINTS_BY_KEY = {p.key: p for p in POINTS}


def points_for(expert: bool, writable_allowed: bool) -> list[Point]:
    """Points to expose. In eavesdrop mode only the status block is available."""
    result = []
    for p in POINTS:
        if p.expert and not expert:
            continue
        if not writable_allowed and (p.address is None or p.address >= CONFIG_START) \
                and p.key != "OperatingState":
            continue
        result.append(p)
    return result


# ──────────────────────────────────────────────────────────────────────
# Register image: everything read from the device, addressed by byte
# ──────────────────────────────────────────────────────────────────────

class RegisterImage:
    def __init__(self) -> None:
        self._bytes: dict[int, int] = {}
        # First byte of the G-426's register 252, only seen on the controller cable.
        self.controller_flags: int | None = None

    def controller_on(self) -> bool | None:
        """
        On the controller cable the G-426 tells the PCWU itself whether it is on
        (register 252); otherwise fall back on PCWU register 194 (2 = on, as
        observed by mvdklip on the PCWU's free port).
        """
        if self.controller_flags is not None:
            return bool(self.controller_flags & CONTROLLER_ON)
        value = self.u16(194)
        return None if value is None else value == 2

    def update(self, start: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self._bytes[start + i] = b

    def raw(self, address: int, size: int) -> bytes | None:
        try:
            return bytes(self._bytes[address + i] for i in range(size))
        except KeyError:
            return None

    def u16(self, address: int) -> int | None:
        raw = self.raw(address, 2)
        return None if raw is None else struct.unpack("<H", raw)[0]

    def has(self, address: int) -> bool:
        return address in self._bytes


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def format_schedule(mask: int) -> str:
    """24 bit hour mask (bit 0 = 00:00-01:00) -> '06:00-22:00' style ranges."""
    ranges = []
    hour = 0
    while hour < 24:
        if mask & (1 << hour):
            end = hour
            while end + 1 < 24 and mask & (1 << (end + 1)):
                end += 1
            ranges.append(f"{hour:02d}:00-{end + 1:02d}:00")
            hour = end + 1
        else:
            hour += 1
    return ", ".join(ranges) if ranges else "off"


def operating_state(image: RegisterImage) -> str | None:
    """Combined state like the Domoticz plugin derives it (registers 194, 196, 202)."""
    controller_on = image.controller_on()
    flags = image.u16(196)
    ws = image.u16(202)
    if controller_on is None or flags is None or ws is None:
        return None
    if not controller_on:
        return "Off (controller off)"
    for bit, text in ((WS_EXT_OFF, "Waiting: external stop"), (WS_DISABLED, "Waiting: disabled"),
                      (WS_RESTART_DELAY, "Waiting: restart delay"), (WS_LOW_COP, "Waiting: ambient too cold"),
                      (WS_LOW_PRESSURE, "Error: low pressure"), (WS_HIGH_PRESSURE, "Error: high pressure"),
                      (WS_OVERHEAT, "Error: overheating")):
        if ws & bit:
            return text
    if flags & (1 << BIT_FAN):
        for bit, text in ((WS_DEFROST, "Defrosting"), (WS_TANK_FROST, "Tank frost protection"),
                          (WS_HP_FROST, "Frost protection"), (WS_PREHEAT, "Compressor preheating")):
            if ws & bit:
                return text
        return "Heating"
    if flags & (1 << BIT_HEATER_E):
        return "Electric heater"
    return "Idle"


def decode(point: Point, image: RegisterImage):
    """Decode a point to its HA value (number, bool, str) or None if not read yet."""
    if point.key == "OperatingState":
        return operating_state(image)
    if point.key == "ControllerOn":
        return image.controller_on()
    if point.kind == "tprg":
        raw = image.raw(point.address, 4)
        return None if raw is None else format_schedule(struct.unpack("<I", raw)[0] & 0xFFFFFF)
    value = image.u16(point.address)
    if value is None:
        return None
    if point.kind == "temp":
        return _s16(value) / 10
    if point.kind == "bool":
        return value != 0
    if point.kind == "bit":
        return bool(value & (1 << point.bit))
    if point.options:
        return point.options.get(value, str(value))
    return value


def encode(point: Point, payload: str) -> int:
    """
    Convert an HA command payload to the raw 16-bit register value.
    Raises ValueError for anything the PCWU would not accept.
    """
    if not point.writable or point.address is None:
        raise ValueError(f"{point.key} is not writable")
    text = payload.strip()
    if point.platform == "switch":
        if text.upper() == "ON":
            return 1
        if text.upper() == "OFF":
            return 0
        raise ValueError(f"invalid switch payload {payload!r}")
    if point.platform == "select":
        for raw, label in point.options.items():
            if label == text:
                return raw
        raise ValueError(f"invalid option {payload!r} for {point.key}")
    value = float(text)
    if value != value or not point.minimum <= value <= point.maximum:
        raise ValueError(f"{value} outside {point.minimum}..{point.maximum} for {point.key}")
    steps = (value - point.minimum) / point.step
    if abs(steps - round(steps)) > 1e-6:
        raise ValueError(f"{value} is not a multiple of {point.step} for {point.key}")
    if point.kind == "temp":
        return round(value * 10) & 0xFFFF
    return round(value) & 0xFFFF
