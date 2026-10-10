# Changelog

## 2.0.0

Rewrite of the protocol layer. 1.0.x did not talk the GECO protocol
correctly; please upgrade.

### Fixed

- Registers are byte addresses (temperatures at 128–146, settings at 302–537);
  1.0.x used word offsets from 100/300 and read the wrong registers.
- Values are little-endian; 1.0.x decoded them big-endian.
- Writing uses the real single-register write (function 0x60, marker 0x80,
  2 bytes) and waits for the 0x70 acknowledge of that register. The block
  write with sub-function 0xA0 of 1.0.x does not exist and could have
  overwritten unrelated settings.
- Every write is verified by reading the register back; values outside the
  controller's range are refused (the PCWU would silently reset them to the
  factory default).
- Responses are matched on function, addresses, start register and length.
- Eavesdrop mode decodes the status frames the PCWU writes to the controller
  (0x60 at 120) instead of looking for responses that only carry display data.
- Status bit 2 is the water pump (not a circulation pump flag); the "heat pump
  on" flag (unreliable according to the protocol notes) and the non-existent
  "heater power limit" are gone.
- Circulation pump mode is register 430 (0 = continuous, 1 = interrupted).

### Added

- Operating state sensor (heating, defrosting, waiting reasons, errors).
- Electric heater E targets and blocking, anti-legionella, controller-on and
  restart-timer entities; heat pump and heater E schedules (read only).
- `expert_mode` option for installer settings (defrost, heater P, external
  contacts, circulation and boiler pump).
- Serial devices (`/dev/ttyUSB0`) as alternative to a TCP adapter.
- Entities become unavailable when the PCWU does not answer; discovery and
  states are re-sent when Home Assistant restarts.
- Old 1.0.x entities and retained topics are removed automatically.
- Test suite, PCWU simulator.

### Changed

- MQTT is no longer required to come from the Mosquitto add-on (`mqtt:want`).
- State topics are `hewalex/<key>`, commands `hewalex/<key>/set`,
  availability `hewalex/availability`.
- Base image Alpine 3.22, paho-mqtt 2.1.

## 1.0.1 (2026-02-15)

- Attempted fixes for wrong temperatures and writes (not working, see 2.0.0).

## 1.0.0 (2026-02-15)

- Initial release.
