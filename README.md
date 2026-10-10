# Hewalex PCWU – Home Assistant add-on

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

Home Assistant add-on for **Hewalex PCWU** heat pumps (G-426 controller). It
talks to the heat pump over RS485 (via a RS485-to-WiFi/Ethernet adapter or a
USB stick) and publishes everything with **MQTT discovery**, so a
*Hewalex PCWU* device with sensors, switches, numbers and selects appears in
Home Assistant without any YAML.

```
┌──────────────┐  RS485 38400 8N1  ┌──────────────────┐   TCP    ┌─────────────┐  MQTT  ┌────────────────┐
│ Hewalex PCWU │◄─────────────────►│ RS485 WiFi/LAN   │◄────────►│ this add-on │◄──────►│ Home Assistant │
│ (free port)  │                   │ adapter (EW11 …) │          └─────────────┘        └────────────────┘
└──────────────┘                   └──────────────────┘
```

## Installation

1. *Settings → Add-ons → Add-on store → ⋮ → Repositories*, add
   `https://github.com/Emacee/hewalex-mqtt-auto-discovery`.
2. Install **Hewalex PCWU**, set `device_address` to the IP of your adapter.
3. Start it. The device shows up under *Settings → Devices & services → MQTT*.

Wiring, controller settings, all options and the entity list are in
[DOCS.md](DOCS.md).

## Protocol

The G-426 uses Hewalex' GECO protocol: an 8 byte header with a CRC-8/DVB-S2
and a payload with a CRC-16/XMODEM (sent high byte first). Registers are
**byte** addresses; values are little-endian 16-bit words, temperatures signed
and ×10. The status block is 120–301, settings are 302–537. A write is a
single-register 0x60 request acknowledged with 0x70; the add-on reads every
written register back. See the docstring of
[`rootfs/app/geco.py`](rootfs/app/geco.py) for the exact frame layout.

## Development

```
pip install pyserial==3.5 paho-mqtt==2.1.0 pytest
python -m pytest tests            # end-to-end tests also need `mosquitto` on PATH
python tests/pcwu_simulator.py    # simulated PCWU on 127.0.0.1:8899
```

The tests compare every frame and every decoded register with the
[Domoticz-Hewalex](https://github.com/mvdklip/Domoticz-Hewalex) library
(`tests/reference`), and run the add-on end to end against a simulated PCWU
and a Mosquitto broker.

## Credits

Protocol reverse engineering by krzysztof1111111111 on
[elektroda.pl](https://www.elektroda.pl/rtvforum/topic3499254.html),
[aelias-eu/hewalex-geco-protocol](https://github.com/aelias-eu/hewalex-geco-protocol),
[mvdklip/Domoticz-Hewalex](https://github.com/mvdklip/Domoticz-Hewalex) and
[Chibald/Hewalex2Mqtt](https://github.com/Chibald/Hewalex2Mqtt).
