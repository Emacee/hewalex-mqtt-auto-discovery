# Changelog

## 2.1.2

- Eavesdrop: "Controller on" and "Operating state" now follow what the G-426
  itself reports to the PCWU (its register 252: 0x10 on, 0x08 off) instead of
  PCWU register 194, which reads 1 on a real PCWU while the controller is on.
  Changes are logged ("G-426 controller reports status 0x10 (on)").
- Eavesdrop with `log_level: debug`: every other frame on the line is logged
  when its content changes, and the status block is published on
  `<prefix>/debug/status`.

## 2.1.1

- Cable test: when the G-426 controller answers, its settings are shown as raw
  words (the controller uses its own register layout, decoding them with the
  PCWU map gave meaningless values); temperatures are compared with a 0.5 °C
  tolerance; the free port's factory address (255/65535) is also probed, with
  a one-line conclusion.

## 2.1.0

### Added

- Direct mode first listens to the RS485 line. When the PCWU's own traffic to
  its G-426 controller shows up, the adapter is on the controller cable
  instead of the PCWU's free port: no controls are offered, every write is
  refused and the add-on continues read only, like eavesdrop mode.
- On that cable a one-time, read-only cable test is written to the log: who
  answers on the configured address (the controller keeps a copy of the
  PCWU status, recognisable by a message counter in register 196), which
  settings that party holds, and whether the PCWU itself accepts requests on
  this cable.
- Writes are also refused when traffic from another master appears later, or
  during a short listen just before each write.
- `pcwu_simulator.py --eavesdrop` simulates a controller that answers requests.

### Changed

- Controls are published once the line is known to be the free port.
- Device info shows the add-on version; no bashio deprecation warning.

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
