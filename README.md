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

1. Connect the RS485 adapter, see [Wiring](#wiring-direct-or-eavesdrop) below.
2. *Settings → Add-ons → Add-on store → ⋮ → Repositories*, add
   `https://github.com/Emacee/hewalex-mqtt-auto-discovery`.
3. Install **Hewalex PCWU**, set `device_address` to the IP of your adapter
   and `mode` to `direct` or `eavesdrop` to match the wiring.
4. Start it. The device shows up under *Settings → Devices & services → MQTT*.

All options and the entity list are in [DOCS.md](DOCS.md).

## Wiring: direct or eavesdrop

The PCWU main board has **two RS485 ports**. One carries the cable to the
G-426 controller (the panel with the display), the other one is **free**. Where
you connect the RS485 adapter decides what the add-on can do:

|                         | **Direct** (recommended)                     | **Eavesdrop**                                   |
|-------------------------|----------------------------------------------|-------------------------------------------------|
| Adapter connected to    | the **free RS485 port** on the PCWU board    | the **cable between PCWU and G-426**, in parallel |
| Temperatures and status | yes                                          | yes                                             |
| Settings (read)         | yes                                          | no                                              |
| Settings (change)       | yes                                          | **no**                                          |
| Controller menu change  | free port: 38400 baud, address 2 / 2         | none                                            |
| Opening the heat pump   | yes (connection box)                         | not necessarily (can be done at the G-426)      |
| Add-on option `mode`    | `direct`                                     | `eavesdrop`                                     |

```
                           PCWU main board
                ┌────────────────────────────────────┐
                │  RS485 port, FREE   RS485 port     │
                │  (A B GND +12V)     (A B GND +12V) │
                └──────┬───────────────────┬─────────┘
                       │                   │  4-wire cable
     DIRECT            │                   │
     ┌─────────────────┴──┐                │        ┌──────────────┐
     │ RS485 WiFi adapter │                ├────────┤ G-426 panel  │
     └────────────────────┘                │        └──────────────┘
                                           │
     EAVESDROP                             │ (T-tap anywhere on this cable)
     ┌────────────────────┐                │
     │ RS485 WiFi adapter ├────────────────┘
     └────────────────────┘
```

### Direct mode: adapter on the free port (read + write)

On this port the PCWU is a *slave*: it says nothing until it is asked, and
answers every request from the add-on. The G-426 keeps working normally on the
other port.

1. **Switch the heat pump off at the mains.** The connection box also holds
   230 V.
2. Open the connection/fuse box of the PCWU. On the main board you find two
   RS485 connectors: one with the cable to the G-426, one **free** (often with
   an empty screw plug).
3. Connect the adapter to the free connector: **A to A, B to B, GND to GND**.
   Copy the order and colours of the occupied connector next to it. The
   +12 V on the connector can power an adapter that accepts 12 V (the
   Elfin EW11 does); measure it first and check your adapter's input range.
4. Switch the heat pump back on. In the **G-426 menu, RS485 settings of this
   port**, set baud rate **38400**, physical address **2**, logical address
   **2**. The factory values (255 / 65535) do not work.
5. Set the adapter as below and, in the add-on, `mode: direct`,
   `device_hard_id: 2`, `device_soft_id: 2` (the values from step 4),
   `controller_hard_id: 1`, `controller_soft_id: 1`.

Expected log: `RS485 line is quiet: adapter is on the PCWU's free port,
enabling controls`, then `Device reachable`. All controls appear in Home
Assistant.

### Eavesdrop mode: adapter on the controller cable (read only)

On this cable the PCWU is the *master*: twice a second it reads two registers
from the G-426 and writes its status block (temperatures, fan, compressor,
pumps) to it. The add-on only listens to that status.

1. Find the 4-wire cable between the PCWU and the G-426 panel. You can tap it
   at either end: at the terminal in the PCWU or at the back of the G-426.
2. Connect the adapter **in parallel**: A to A, B to B, GND to GND, next to
   the existing wires. Do not interrupt the cable; the G-426 needs its A/B
   and its 12 V supply to keep working.
3. No setting on the G-426 changes; this cable runs at 38400 baud.
4. Set the adapter as below and, in the add-on, `mode: eavesdrop`. The address
   options are not used in this mode.

Expected log: `Receiving status frames from the PCWU`, then `Device reachable`.
Only temperatures and status entities appear.

**Why settings cannot be changed on this cable.** The PCWU and the G-426 own
this line; a second master would collide with the PCWU's own traffic, and
requests sent here are answered by the G-426, which keeps its settings in its
own register layout. The PCWU itself does not answer requests on this cable.
This was verified on a real PCWU with the add-on's cable test (see below), so
changing settings needs the free port.

### Adapter settings (both modes)

| Setting | Value |
|---|---|
| Serial | 38400 baud, 8 data bits, no parity, 1 stop bit, no flow control |
| Mode | transparent serial ↔ TCP (not a Modbus gateway) |
| Network | TCP **server**, port 8899 (or any port; set `device_port` to match) |
| IP address | fixed (DHCP reservation), entered as `device_address` |

A USB-RS485 stick works too: set `device_address` to e.g. `/dev/ttyUSB0`.

### Which cable am I on?

Start the add-on in `direct` mode and read the log. It listens before it
sends anything and recognises the controller cable by the PCWU's own traffic:

| Log message | Meaning |
|---|---|
| `RS485 line is quiet … enabling controls` + `Device reachable` | free port, everything works |
| `ERROR … the adapter is on the controller cable` + `RS485 cable test` | controller cable: nothing is written, the add-on continues read only. Switch to `mode: eavesdrop`, or move the adapter to the free port |
| `no response to read …` (repeated) | free port, but the PCWU does not answer: check the G-426 RS485 settings (38400, 2 / 2), the address options, and A/B |
| `No PCWU status frame in the last 60 s` (eavesdrop) | nothing heard: wrong cable (the free port is silent), A and B swapped, or wrong adapter settings |

**A and B swapped?** Manufacturers label A/B (or +/−, D+/D−) differently.
If the adapter is reachable but no frame is ever recognised, swap A and B.

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
