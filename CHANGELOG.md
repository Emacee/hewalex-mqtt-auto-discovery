# Changelog

## 1.0.1 (2026-02-15)

### Bug Fixes

- **Fixed garbage temperature values (e.g. T6 = -38.8°C)**: Response register data was
  parsed from payload byte offset 9 instead of 10, causing a 1-byte misalignment that
  produced random garbage for all sensor values
- **Fixed config register reads failing (sensors "unknown")**: Register start address
  was encoded as 8-bit (single byte), so base address 300 (0x012C) was truncated to
  44 (0x2C). Now correctly encoded as 16-bit little-endian
- **Fixed switches/controls not working**: Write implementation used a non-existent
  single-register write format. Now uses proper read-modify-write: reads all 50 config
  registers, modifies the target, writes the full block back with sub-function 0xA0
- **Persistent TCP connection**: Socket is now opened once and reused across poll cycles
  instead of reopening per request, which caused stale data in the receive buffer
- **Packet-length-aware reads**: Serial reads now accumulate bytes and use CRC validation
  to find complete packets, preventing partial reads from producing corrupt data
- **Cached config register block**: Last-read config values are cached for write-back,
  ensuring the read-modify-write pattern works correctly

## 1.0.0 (2026-02-15)

### Initial Release

- Full PCWU heat pump support (PCWU 2.5kW, 3.0kW)
- Direct communication mode (read + write)
- Eavesdrop mode (read-only)
- 10 temperature sensors (T1–T10)
- 6 binary status sensors (Fan, Pump, Compressor, Heater, etc.)
- 4 switch controls (Heat Pump Enable, Heater Enable, Anti-Freeze, etc.)
- 8 number controls (Target Temp, Hysteresis, Defrost settings, etc.)
- 5 select controls (Sensor, Fan Mode, Pump Mode, etc.)
- MQTT auto-discovery for Home Assistant
- Automatic reconnection on connection loss
- Debug mode with raw register dumps
