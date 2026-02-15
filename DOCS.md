# Hewalex PCWU - Home Assistant Add-on

Control and monitor your Hewalex PCWU heat pump directly from Home Assistant.

## Overview

This add-on connects to your Hewalex PCWU heat pump via a Waveshare (or similar)
RS485-to-WiFi/Ethernet adapter, reads sensor data and configuration, and publishes
everything to MQTT with **Home Assistant auto-discovery** — entities appear
automatically in your HA dashboard.

### Supported Hardware

- **Heat pumps**: Hewalex PCWU 2.5kW, PCWU 3.0kW (and variants)
- **Controllers**: GECO G-426
- **Adapters**: Any RS485-to-WiFi/Ethernet bridge (Waveshare, Elfin EW11, USR-W610, etc.)

### Features

- 10 temperature sensors (T1–T10)
- Equipment status (compressor, fan, pump, heater)
- Full config control (target temp, hysteresis, fan mode, defrost settings, etc.)
- Two communication modes (Direct and Eavesdrop)
- MQTT auto-discovery for seamless HA integration
- Automatic reconnection on connection loss

## Hardware Setup

### Option A: Direct Communication (Recommended)

Connect your RS485 adapter to the **dedicated (free) RS485 port** on the PCWU
mainboard. This allows both reading and writing.

1. Open the PCWU fuse box
2. Find the free RS485 connector (1st port; the 2nd is used by the G-426 controller)
3. Wire A, B and optionally +12V/GND to your RS485 adapter
4. On the G-426 controller, go to RS485 settings for port 1:
   - Baud rate: **38400**
   - Physical address: **2**
   - Logical address: **2**
5. Configure your WiFi/Ethernet adapter to match (38400 8N1, TCP server mode)

### Option B: Eavesdropping (Read-Only)

Tap into the existing RS485 bus between the G-426 and the PCWU by wiring your
adapter **in parallel** to the A and B lines. This is read-only.

> **Warning**: Eavesdrop mode cannot send any commands to the heat pump.

## Add-on Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `device_address` | `192.168.1.100` | IP address of your RS485-to-WiFi adapter |
| `device_port` | `8899` | TCP port of the adapter |
| `mode` | `direct` | `direct` (read+write) or `eavesdrop` (read-only) |
| `poll_interval` | `30` | Seconds between register polls (5–300) |
| `mqtt_host` | *(auto)* | MQTT broker host (auto-detected from HA if empty) |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | *(auto)* | MQTT username |
| `mqtt_password` | *(auto)* | MQTT password |
| `mqtt_topic_prefix` | `hewalex` | Base MQTT topic |
| `mqtt_discovery_prefix` | `homeassistant` | HA discovery prefix |
| `controller_hard_id` | `1` | Controller physical RS485 address |
| `controller_soft_id` | `1` | Controller logical address |
| `device_hard_id` | `2` | PCWU physical RS485 address |
| `device_soft_id` | `2` | PCWU logical address |
| `log_level` | `info` | Logging verbosity (`debug`, `info`, `warning`, `error`) |

### MQTT Auto-Detection

If you leave `mqtt_host` empty, the add-on will automatically use the MQTT
broker configured in Home Assistant (via the Mosquitto add-on or external broker).

## Entities Created

### Sensors (read-only)

| Entity | Description |
|--------|-------------|
| PCWU T1 Ambient | Outside / inlet air temperature |
| PCWU T2 Tank Bottom | Water tank bottom temperature |
| PCWU T3 Tank Top | Water tank top temperature |
| PCWU T4 Boiler | Solid fuel boiler temperature |
| PCWU T5 Void | Unused sensor slot |
| PCWU T6 Water Inlet | Heat pump water inlet |
| PCWU T7 Water Outlet | Heat pump water outlet |
| PCWU T8 Evaporator | Evaporator temperature |
| PCWU T9 Before Compressor | Suction line temperature |
| PCWU T10 After Compressor | Discharge line temperature |
| PCWU Expansion Valve | Expansion valve position (steps) |
| PCWU Waiting Status | 0 = available, 2 = disabled |

### Binary Sensors (read-only)

| Entity | Description |
|--------|-------------|
| PCWU Fan | Fan is running |
| PCWU Circulation Pump | Circulation pump is running |
| PCWU Heat Pump | Heat pump is active |
| PCWU Compressor | Compressor is running |
| PCWU Electric Heater | Electric heater is on |
| PCWU Manual Mode | Manual mode is active |

### Controls (read/write, Direct mode only)

| Entity | Type | Range | Description |
|--------|------|-------|-------------|
| PCWU Heat Pump Enable | Switch | ON/OFF | Enable/disable heat pump |
| PCWU Electric Heater Enable | Switch | ON/OFF | Enable/disable heater |
| PCWU Anti-Freeze Protection | Switch | ON/OFF | Anti-freezing protection |
| PCWU External HP Deactivation | Switch | ON/OFF | External deactivation |
| PCWU Target Temperature | Number | 10–60°C | Hot water setpoint |
| PCWU Start Hysteresis | Number | 2–10°C | Start-up hysteresis |
| PCWU Min Ambient Temp | Number | −10–10°C | Minimum ambient temperature |
| PCWU Defrost Interval | Number | 30–90 min | Defrost cycle delay |
| PCWU Defrost Start Temp | Number | −30–0°C | Defrost activation temp |
| PCWU Defrost Stop Temp | Number | 2–30°C | Defrost finish temp |
| PCWU Max Defrost Duration | Number | 1–12 min | Maximum defrost time |
| PCWU Controlling Sensor | Select | T2/T3/T7 | Which sensor controls HP |
| PCWU Water Pump Mode | Select | Continuous/Synchronous | Pump operation mode |
| PCWU Fan Mode | Select | Max/Min/Day-Night | Fan operation mode |
| PCWU Circ Pump Mode | Select | Intermittent/Continuous | Circulation pump mode |

## Troubleshooting

### No data / connection errors

1. Verify your RS485 adapter is reachable: `ping 192.168.1.100`
2. Verify TCP port is open: `nc -zv 192.168.1.100 8899`
3. Check baud rate matches: adapter and G-426 controller must both be **38400**
4. Set `log_level` to `debug` to see raw protocol data

### Incorrect temperature values

The register offsets may vary slightly between firmware versions. Enable debug
logging to see raw register dumps at `hewalex/debug/status` and
`hewalex/debug/config` MQTT topics, then cross-reference with known values on
your G-426 controller display.

### Commands not working

- Ensure you are in `direct` mode (not `eavesdrop`)
- Verify controller/device IDs match your G-426 settings
- Check that you're connected to the **dedicated** RS485 port (not eavesdropping)

## Protocol Notes

This add-on implements the GECO protocol used by Hewalex G-422/G-426 controllers.
The protocol was reverse-engineered by the community; key references:

- [hewalex-geco-protocol](https://github.com/aelias-eu/hewalex-geco-protocol)
- [Domoticz-Hewalex](https://github.com/mvdklip/Domoticz-Hewalex)
- [Hewalex2Mqtt](https://github.com/Chibald/Hewalex2Mqtt)
- [Elektroda forum thread](https://www.elektroda.pl/rtvforum/topic3499254.html)

The register map in this add-on is based on these sources. If your firmware uses
different register offsets, you can modify `pcwu_registers.py` inside the
container at `/app/pcwu_registers.py`.

## License

GPL-3.0 (consistent with upstream Hewalex2Mqtt and Domoticz-Hewalex projects)
