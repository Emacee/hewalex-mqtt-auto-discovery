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

## Wiring

### Direct mode (recommended, read + write)

The PCWU main board has two RS485 ports. The G-426 controller uses one of
them; the other one is free.

1. Switch the heat pump off at the mains and open the fuse/connection box.
2. Connect A, B (and GND, optionally +12 V to power the adapter) of the free
   RS485 port to the adapter. Copy the colour coding of the port that goes to
   the controller.
3. On the G-426 controller open the RS485 settings of the free port and set
   baud rate **38400**, physical address **2** and logical address **2**.
   (Factory values are 255 / 65535, which do not work.)
4. Configure the adapter for 38400 8N1, TCP server, and give it a fixed IP.

The add-on is master on this port; the controller keeps working as usual on
the other one.

### Eavesdrop mode (read only)

Connect the adapter in parallel to A and B of the cable between G-426 and the
heat pump. The PCWU sends its status to the controller twice a second; the
add-on decodes that. Settings cannot be read or changed in this mode, so only
temperatures and status entities are created.

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
| Controller on | 194 | the G-426 display/controller is switched on |
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
