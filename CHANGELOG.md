# Changelog

## 1.0.0 (2026-02-12)

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
