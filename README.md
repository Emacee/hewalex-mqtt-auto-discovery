# Hewalex PCWU - Home Assistant Add-on

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Home Assistant add-on to control and monitor **Hewalex PCWU** heat pumps via MQTT
with automatic entity discovery.

## Quick Start

1. Add this repository to your Home Assistant add-on store
2. Install the "Hewalex PCWU" add-on
3. Configure the IP address of your RS485-to-WiFi adapter
4. Start the add-on — entities appear automatically in HA

## Architecture

```
┌──────────────┐    RS485     ┌──────────────────┐    TCP/WiFi    ┌──────────────┐
│  Hewalex PCWU│◄────────────►│ Waveshare RS485  │◄──────────────►│  This Add-on │
│  Heat Pump   │    38400 8N1 │ WiFi Adapter     │   socket://    │  (Docker)    │
└──────────────┘              └──────────────────┘                └──────┬───────┘
                                                                        │ MQTT
                                                                        ▼
                                                                 ┌──────────────┐
                                                                 │Home Assistant│
                                                                 │  (Sensors,   │
                                                                 │  Switches,   │
                                                                 │  Controls)   │
                                                                 └──────────────┘
```

## Documentation

See [DOCS.md](DOCS.md) for full documentation including hardware setup,
configuration options, entity list, and troubleshooting.

## Credits

Protocol implementation based on the excellent reverse engineering work by:

- [aelias-eu/hewalex-geco-protocol](https://github.com/aelias-eu/hewalex-geco-protocol)
- [mvdklip/Domoticz-Hewalex](https://github.com/mvdklip/Domoticz-Hewalex)
- [Chibald/Hewalex2Mqtt](https://github.com/Chibald/Hewalex2Mqtt)
- [Elektroda.pl community](https://www.elektroda.pl/rtvforum/topic3499254.html)

Add-on packaging inspired by [alexbelgium/hassio-addons](https://github.com/alexbelgium/hassio-addons).
