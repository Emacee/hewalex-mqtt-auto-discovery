# Hewalex PCWU

Monitor and control a Hewalex PCWU heat pump (G-426 controller) from Home
Assistant. The add-on talks to the heat pump over RS485 through a
RS485-to-WiFi/Ethernet adapter (or a USB adapter) and publishes everything with
MQTT discovery: a **Hewalex PCWU** device appears automatically under
*Settings → Devices & services → MQTT*.

## Requirements

- An MQTT broker. With the **Mosquitto broker** add-on installed and the MQTT
  integration set up, nothing has to be configured; otherwise fill in
  `mqtt_host` and friends.
- A RS485 adapter: Elfin EW11, Waveshare RS485-to-WiFi/ETH, USR-W610, … in
  *TCP server* mode, **38400 baud, 8 data bits, no parity, 1 stop bit**
  (or a USB-RS485 stick, see `device_address`).

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

## Options

| Option | Default | |
|---|---|---|
| `device_address` | `192.168.1.100` | IP address of the adapter, or a serial device such as `/dev/ttyUSB0` |
| `device_port` | `8899` | TCP port of the adapter (ignored for a serial device) |
| `mode` | `direct` | `direct` (read + write) or `eavesdrop` (read only) |
| `poll_interval` | `30` | Seconds between reads (direct) / state updates (eavesdrop) |
| `expert_mode` | `false` | Also expose installer settings, see below |
| `controller_hard_id` / `controller_soft_id` | `1` / `1` | Address the add-on uses as master |
| `device_hard_id` / `device_soft_id` | `2` / `2` | Physical / logical address set on the controller for the free port |
| `mqtt_host` … `mqtt_password` | empty | Only needed when not using the Mosquitto add-on |
| `mqtt_topic_prefix` | `hewalex` | State topics: `hewalex/<key>`, commands: `hewalex/<key>/set` |
| `mqtt_discovery_prefix` | `homeassistant` | Must match the MQTT integration |
| `log_level` | `info` | `debug` logs every frame and publishes raw register dumps on `hewalex/debug/#` |

## Entities

### Measurements and status

| Entity | Register | |
|---|---|---|
| T1 ambient … T10 after compressor | 128–146 | temperatures (T4 and T5 disabled by default) |
| Fan, Water pump, Compressor, Electric heater E | 196 bits 0, 2, 11, 12 | running |
| Operating state | 194, 196, 202 | Heating, Defrosting, Idle, Waiting: …, Error: … |
| Controller on | G-426 register 252 (eavesdrop), 194 (direct) | the G-426 controller is switched on |
| Expansion valve position | 198 | steps |
| Waiting status code, Restart timer | 202, 206 | raw values, diagnostic |
| Installation scheme | 302 | diagnostic, read only |
| Schedules (heat pump, heater E) | 314–325, 374–385 | on-hours, e.g. `06:00-22:00` |

### Settings (direct mode)

| Entity | Register | Range |
|---|---|---|
| Heat pump enabled | 304 | on/off |
| Hot water target | 308 | 10–60 °C |
| Start hysteresis | 310 | 2–10 °C |
| Minimum ambient temperature | 312 | −10–10 °C |
| Controlling sensor | 306 | T2 / T3 / T7 |
| Frost protection | 326 | on/off |
| Water pump mode | 328 | Continuous / Synchronous |
| Fan mode | 330 | Max / Min / Day/Night |
| Electric heater E enabled | 364 | on/off |
| Heater E target (heat pump on / off) | 366 / 368 | 30–60 °C |
| Heater E blocked while heat pump runs | 370 | on/off |
| Anti-legionella | 498 | on/off |

### Expert mode

Only for installers who know their installation. Adds: defrost interval
(30–90 min), defrost start (−30–0 °C) and stop (2–30 °C) temperature, maximum
defrost time (1–12 min), heater P on/off and blocking, circulation pump mode,
boiler pump priority, anti-legionella heater selection and the four external
contact switches; plus read-only heater P, circulation pump and boiler pump
temperatures and the expansion valve mode. The installation scheme and the
expansion valve parameters are never written.

## How writing works

1. The value is checked against the range the controller allows. The PCWU
   itself silently replaces an invalid value by its factory default, so
   invalid values are refused before anything is sent.
2. One 16-bit register is written (function 0x60) and the add-on waits for
   the PCWU's acknowledge (0x70) for exactly that register.
3. The register is read back. Home Assistant always shows the value read
   from the PCWU; if it differs from what was asked, the log says so.

Every frame carries a CRC-8 over the header and a CRC-16/XMODEM over the
payload; frames with a wrong CRC, wrong addresses or a different register
than requested are ignored. Reads and writes are retried twice.

## Troubleshooting

- **Entities unavailable** – the add-on marks the device unavailable after
  three failed polls. Check the log: `no response` means wrong IP/port, wrong
  addresses on the controller, A/B swapped or a wrong baud rate; `invalid
  bytes received` points at baud rate / parity.
- **Commands do nothing** – check `mode: direct` and that the adapter is on
  the free port, not on the controller cable.
- **"PCWU did not accept …"** – the controller refused the value (outside the
  range of your firmware); the entity shows the actual value.
- Set `log_level: debug` to see every frame and get raw dumps of both register
  blocks on `hewalex/debug/status` and `hewalex/debug/config`.

## Upgrading from 1.0.x

Version 1.0.x decoded the registers wrongly (wrong addresses and byte order)
and its write command could not work. 2.0.0 removes the old entities that no
longer exist (circulation pump flag, heat pump flag, manual mode, heater power
limit, the old circulation pump settings). Entities that still exist keep
their entity ID.

## Credits

Protocol reverse engineering by krzysztof1111111111
([elektroda.pl](https://www.elektroda.pl/rtvforum/topic3499254.html)),
[aelias-eu/hewalex-geco-protocol](https://github.com/aelias-eu/hewalex-geco-protocol)
and [mvdklip/Domoticz-Hewalex](https://github.com/mvdklip/Domoticz-Hewalex),
whose register map this add-on follows.
