"""
Hewalex PCWU Register Map

Status registers (base=100, FNC=0x40/0x50):
  Read-only values: temperatures, operating states, equipment status.

Config registers (base=300, FNC=0x60/0x70):
  Read/write values: setpoints, modes, time programs, protection settings.

Register data types:
  word   - unsigned 16-bit integer
  te10   - signed 16-bit, divide by 10 for °C
  bool   - 0=False, 1=True (stored as word)
  mask   - bitmask within a word register
  tprg   - time program (24-bit bitmask, one bit per hour)
  date   - packed date (high=value1, low=value2)

Sources:
  - https://github.com/Chibald/Hewalex2Mqtt
  - https://github.com/mvdklip/Domoticz-Hewalex
  - https://www.elektroda.pl/rtvforum/topic3499254.html
"""

import logging
from geco_protocol import signed16, extract_registers

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# STATUS REGISTER MAP (base=100, request 50 registers → regs 100-149)
# ──────────────────────────────────────────────────────────────────────
# offset = register_number - 100
# Each entry: (offset, name, type, description)

STATUS_REG_BASE = 100
STATUS_REG_COUNT = 50

STATUS_REGISTERS = [
    # Date and time
    (0,  'DateYearMonth',   'date_ym',  'Year (hi) / Month (lo)'),
    (1,  'DateDayWeekday',  'date_dw',  'Day (hi) / Weekday (lo)'),
    (2,  'TimeHourMinute',  'time_hm',  'Hour (hi) / Minute (lo)'),
    (3,  'TimeSecond',      'time_s',   'Second (hi byte)'),

    # Temperature sensors
    (4,  'T1',   'te10', 'Ambient temperature'),
    (5,  'T2',   'te10', 'Tank bottom temperature'),
    (6,  'T3',   'te10', 'Tank top temperature'),
    (7,  'T4',   'te10', 'Solid fuel boiler temperature'),
    (8,  'T5',   'te10', 'Void sensor'),
    (9,  'T6',   'te10', 'Water inlet temperature'),
    (10, 'T7',   'te10', 'Water outlet temperature'),
    (11, 'T8',   'te10', 'Evaporator temperature'),
    (12, 'T9',   'te10', 'Before compressor temperature'),
    (13, 'T10',  'te10', 'After compressor temperature'),

    # Operating status
    (14, 'StatusBits',      'word', 'Combined status bitmask'),
    (15, 'IsManual',        'bool', 'Manual mode active'),
    (16, 'EV1',             'word', 'Expansion valve position'),
    (17, 'WaitingStatus',   'word', '0=available, 2=disabled'),
]

# Bitmask definitions for StatusBits (register offset 14)
# These map individual bits to named boolean states
STATUS_BITMASKS = {
    'FanON':              (14, 0x0001),
    'CirculationPumpON':  (14, 0x0002),
    'HeatPumpON':         (14, 0x0004),
    'CompressorON':       (14, 0x0008),
    'HeaterEON':          (14, 0x0010),
}

# ──────────────────────────────────────────────────────────────────────
# CONFIG REGISTER MAP (base=300, request 50 registers → regs 300-349)
# ──────────────────────────────────────────────────────────────────────
# Each entry: (offset, name, type, description, writable, min, max)

CONFIG_REG_BASE = 300
CONFIG_REG_COUNT = 50

CONFIG_REGISTERS = [
    # offset, name, type, description, writable, min_val, max_val
    (0,  'InstallationScheme',   'word', 'Installation scheme (1-9)',          False, 1, 9),
    (1,  'HeatPumpEnabled',      'bool', 'Heat pump enabled',                 True,  0, 1),
    (2,  'HeaterEEnabled',       'bool', 'Electric heater enabled',           True,  0, 1),
    (3,  'HeaterEPowerLimit',    'word', 'Electric heater power limit',       True,  0, 3),
    (4,  'TapWaterSensor',       'word', 'Controlling sensor (0=T2,1=T3,2=T7)', True, 0, 2),
    (5,  'TapWaterTemp',         'te10', 'Target temperature (°C)',           True,  100, 600),
    (6,  'TapWaterHysteresis',   'te10', 'Start-up hysteresis (°C)',          True,  20, 100),
    (7,  'AmbientMinTemp',       'te10', 'Min ambient temp (°C)',             True, -100, 100),

    # Time programs: each is 3 consecutive 8-bit values packed into registers
    # representing 24 hours (1 bit per hour), but stored across reg boundaries
    (8,  'TimeProgramHPM_F_hi',  'word', 'Time program HP Mon-Fri (hi)',      True, 0, 65535),
    (9,  'TimeProgramHPM_F_lo',  'word', 'Time program HP Mon-Fri (lo)',      True, 0, 65535),
    (10, 'TimeProgramHPSat_hi',  'word', 'Time program HP Saturday (hi)',     True, 0, 65535),
    (11, 'TimeProgramHPSat_lo',  'word', 'Time program HP Saturday (lo)',     True, 0, 65535),
    (12, 'TimeProgramHPSun_hi',  'word', 'Time program HP Sunday (hi)',       True, 0, 65535),
    (13, 'TimeProgramHPSun_lo',  'word', 'Time program HP Sunday (lo)',       True, 0, 65535),

    # Protection and operation settings
    (14, 'AntiFreezingEnabled',      'bool', 'Anti-freezing protection',      True,  0, 1),
    (15, 'WaterPumpOperationMode',   'word', 'Pump mode (0=Continuous,1=Synchronous)', True, 0, 1),
    (16, 'FanOperationMode',         'word', 'Fan mode (0=Max,1=Min,2=Day/Night)',     True, 0, 2),

    # Defrosting parameters
    (17, 'DefrostingInterval',   'word', 'Defrost delay (30-90 min)',         True, 30, 90),
    (18, 'DefrostingStartTemp',  'te10', 'Defrost start temp (°C)',           True, -300, 0),
    (19, 'DefrostingStopTemp',   'te10', 'Defrost stop temp (°C)',            True, 20, 300),
    (20, 'DefrostingMaxTime',    'word', 'Max defrost duration (1-12 min)',   True, 1, 12),

    # Additional controls
    (21, 'ExtControllerHPOFF',   'bool', 'External HP deactivation',         True, 0, 1),

    # Circulation pump settings
    (22, 'CircPumpMinTemp',      'te10', 'Min circ pump temp (°C)',           True, 200, 600),
    (23, 'CircPumpMode',         'word', 'Circ pump mode (0=Intermittent,1=Continuous)', True, 0, 1),
]

# Time program helper: map friendly names to register pairs
TIME_PROGRAMS = {
    'TimeProgramHPM_F':  (8, 9),    # Mon-Fri
    'TimeProgramHPSat':  (10, 11),   # Saturday
    'TimeProgramHPSun':  (12, 13),   # Sunday
}


def parse_status_registers(raw_regs: list[int]) -> dict:
    """
    Parse raw status register values into a named dictionary.

    Args:
        raw_regs: list of unsigned 16-bit values (from extract_registers)

    Returns:
        dict with named values, temperatures in °C, booleans as True/False
    """
    result = {}

    for offset, name, dtype, desc in STATUS_REGISTERS:
        if offset >= len(raw_regs):
            continue

        val = raw_regs[offset]

        if dtype == 'te10':
            result[name] = signed16(val) / 10.0
        elif dtype == 'bool':
            result[name] = bool(val)
        elif dtype == 'date_ym':
            result['Year'] = 2000 + ((val >> 8) & 0xFF)
            result['Month'] = val & 0xFF
        elif dtype == 'date_dw':
            result['Day'] = (val >> 8) & 0xFF
            result['Weekday'] = val & 0xFF
        elif dtype == 'time_hm':
            result['Hour'] = (val >> 8) & 0xFF
            result['Minute'] = val & 0xFF
        elif dtype == 'time_s':
            result['Second'] = (val >> 8) & 0xFF
        else:  # word
            result[name] = val

    # Extract bitmask flags
    for name, (offset, mask) in STATUS_BITMASKS.items():
        if offset < len(raw_regs):
            result[name] = bool(raw_regs[offset] & mask)

    return result


def parse_config_registers(raw_regs: list[int]) -> dict:
    """
    Parse raw config register values into a named dictionary.

    Args:
        raw_regs: list of unsigned 16-bit values

    Returns:
        dict with named values
    """
    result = {}

    for entry in CONFIG_REGISTERS:
        offset, name, dtype = entry[0], entry[1], entry[2]

        if offset >= len(raw_regs):
            continue

        val = raw_regs[offset]

        if dtype == 'te10':
            result[name] = signed16(val) / 10.0
        elif dtype == 'bool':
            result[name] = bool(val)
        else:  # word
            result[name] = val

    # Build combined time program strings
    for prog_name, (hi_off, lo_off) in TIME_PROGRAMS.items():
        if hi_off < len(raw_regs) and lo_off < len(raw_regs):
            hi = raw_regs[hi_off]
            lo = raw_regs[lo_off]
            combined = (hi << 16) | lo
            # Build hour-by-hour string: "1" = on, "0" = off for hours 0-23
            hours = ''.join('1' if combined & (1 << i) else '0' for i in range(24))
            result[prog_name] = hours

    return result


def encode_config_value(name: str, user_value) -> tuple[int, int] | None:
    """
    Encode a user-provided value for writing to a config register.

    Args:
        name: register name
        user_value: value from the user (str, int, float, bool)

    Returns:
        (register_offset, raw_16bit_value) or None if invalid
    """
    for entry in CONFIG_REGISTERS:
        offset, reg_name, dtype, desc, writable = entry[0], entry[1], entry[2], entry[3], entry[4]
        if reg_name != name:
            continue
        if not writable:
            logger.warning("Register %s is not writable", name)
            return None

        min_val = entry[5] if len(entry) > 5 else None
        max_val = entry[6] if len(entry) > 6 else None

        if dtype == 'te10':
            raw = int(float(user_value) * 10)
            check_val = raw  # check BEFORE two's complement conversion
            if raw < 0:
                raw = raw & 0xFFFF  # two's complement for signed
        elif dtype == 'bool':
            if isinstance(user_value, str):
                raw = 1 if user_value.lower() in ('true', '1', 'on', 'yes') else 0
            else:
                raw = 1 if user_value else 0
            check_val = raw
        else:  # word
            raw = int(user_value)
            check_val = raw

        # Range check on the logical value (before two's complement)
        if min_val is not None and max_val is not None:
            if check_val < min_val or check_val > max_val:
                logger.warning("Value %s for %s out of range [%s, %s]",
                               user_value, name, min_val, max_val)
                return None

        return (offset, raw & 0xFFFF)

    logger.warning("Unknown config register: %s", name)
    return None


def get_writable_configs() -> list[dict]:
    """Return metadata for all writable config registers."""
    result = []
    for entry in CONFIG_REGISTERS:
        offset, name, dtype, desc, writable = entry[0], entry[1], entry[2], entry[3], entry[4]
        if writable:
            info = {
                'name': name,
                'type': dtype,
                'description': desc,
                'offset': offset,
            }
            if len(entry) > 5:
                info['min'] = entry[5]
            if len(entry) > 6:
                info['max'] = entry[6]
            result.append(info)
    return result
