#!/usr/bin/env python3
"""
Hewalex PCWU Home Assistant Add-on

Connects to a Hewalex PCWU heat pump via a Waveshare (or similar)
RS485-to-WiFi adapter, reads status and config registers using the
GECO protocol, and publishes values to MQTT with HA auto-discovery.

Supports two communication modes:
  - direct:    Actively sends register read/write requests to the PCWU
  - eavesdrop: Passively listens to traffic between G-426 controller and PCWU
               (read-only, no write capability)

v1.0.1 fixes:
  - Register start address is now 16-bit LE (fixes config reads at base=300)
  - Response data extracted from payload[10:] not payload[9:] (fixes misaligned temps)
  - Writes use read-modify-write: full register block sent back with sub_fnc=0xA0
  - Persistent socket connection (not reopened per request)
  - Packet-length-aware serial reads prevent partial/misaligned packets
"""

import os
import sys
import time
import logging
import signal
import socket
import serial

from geco_protocol import (
    build_read_request, build_write_request, parse_packet,
    find_packets, extract_registers, registers_to_bytes,
    FNC_READ_STATUS_REQ, FNC_READ_STATUS_RESP,
    FNC_READ_CONFIG_REQ, FNC_READ_CONFIG_RESP,
    HEADER_LEN, START_BYTE,
)
from pcwu_registers import (
    STATUS_REG_BASE, STATUS_REG_COUNT,
    CONFIG_REG_BASE, CONFIG_REG_COUNT,
    parse_status_registers, parse_config_registers,
    encode_config_value,
)
from mqtt_ha import HewalexMQTT

# ──────────────────────────────────────────────────────────────────────
# Configuration from environment (set by run.sh from HA add-on options)
# ──────────────────────────────────────────────────────────────────────

DEVICE_ADDRESS = os.environ.get('DEVICE_ADDRESS', '192.168.1.100')
DEVICE_PORT = int(os.environ.get('DEVICE_PORT', '8899'))
MODE = os.environ.get('MODE', 'direct')
POLL_INTERVAL = int(os.environ.get('POLL_INTERVAL', '30'))
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'info').upper()

MQTT_HOST = os.environ.get('MQTT_HOST', '127.0.0.1')
MQTT_PORT = int(os.environ.get('MQTT_PORT', '1883'))
MQTT_USER = os.environ.get('MQTT_USER', '')
MQTT_PASSWORD = os.environ.get('MQTT_PASSWORD', '')
MQTT_TOPIC_PREFIX = os.environ.get('MQTT_TOPIC_PREFIX', 'hewalex')
MQTT_DISCOVERY_PREFIX = os.environ.get('MQTT_DISCOVERY_PREFIX', 'homeassistant')

CON_HARD_ID = int(os.environ.get('CONTROLLER_HARD_ID', '1'))
CON_SOFT_ID = int(os.environ.get('CONTROLLER_SOFT_ID', '1'))
DEV_HARD_ID = int(os.environ.get('DEVICE_HARD_ID', '2'))
DEV_SOFT_ID = int(os.environ.get('DEVICE_SOFT_ID', '2'))

# ──────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('hewalex')

# ──────────────────────────────────────────────────────────────────────
# Globals
# ──────────────────────────────────────────────────────────────────────

running = True
write_queue = []              # list of (register_name, value) pending write
cached_config_regs = None     # list[int] — last-read raw config register values


def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ──────────────────────────────────────────────────────────────────────
# Serial / TCP connection management
# ──────────────────────────────────────────────────────────────────────

class SerialConnection:
    """
    Manages a persistent serial-over-TCP connection.

    Key design decisions:
    - Connection is opened once and reused across poll cycles
    - read_packet() accumulates bytes until a complete valid packet is found
    - Input buffer is flushed before each new request to discard stale data
    - Connection is automatically reopened on error
    """

    def __init__(self, address: str, port: int):
        self.url = f'socket://{address}:{port}'
        self.ser = None

    def connect(self):
        """Open or reopen the TCP socket connection."""
        self.close()
        logger.info("Opening connection to %s", self.url)
        self.ser = serial.serial_for_url(self.url, baudrate=38400, timeout=3)
        # Short initial timeout — we'll manage timing in read_packet
        self.ser.timeout = 3
        logger.info("Connection established")

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    @property
    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def flush_input(self):
        """Discard any stale bytes in the receive buffer."""
        if self.ser:
            self.ser.reset_input_buffer()
            # Also drain anything the OS has buffered
            old_timeout = self.ser.timeout
            self.ser.timeout = 0.1
            try:
                while self.ser.read(512):
                    pass
            except Exception:
                pass
            self.ser.timeout = old_timeout

    def send(self, data: bytes):
        """Send raw bytes."""
        if not self.is_open:
            raise serial.SerialException("Not connected")
        logger.debug("TX (%d bytes): %s", len(data), data.hex())
        self.ser.write(data)
        self.ser.flush()

    def read_packet(self, expected_fnc: int, timeout: float = 5.0) -> dict | None:
        """
        Read bytes until we find a valid GECO packet with the expected FNC.

        Uses a two-phase approach:
        1. Read until we see a 0x69 start byte and have at least 8 header bytes
        2. Parse the payload_len from the header, then read until we have the
           full packet (header + payload_len bytes)
        3. Validate both CRCs before accepting

        This prevents the misaligned-read bug that caused garbage temperatures.
        """
        buffer = b''
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Try to read a chunk
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                break

            self.ser.timeout = min(remaining_time, 1.0)
            try:
                chunk = self.ser.read(256)
            except serial.SerialException as e:
                logger.error("Serial read error: %s", e)
                raise

            if chunk:
                buffer += chunk
                logger.debug("RX +%d bytes, buffer=%d", len(chunk), len(buffer))
            elif not buffer:
                # Nothing received at all yet, keep waiting
                continue

            # Try to find valid packets in accumulated buffer
            packets = find_packets(buffer)
            for parsed, end_pos in packets:
                if parsed['fnc'] == expected_fnc:
                    logger.debug("Got response FNC=0x%02X (%d bytes)",
                                  expected_fnc, parsed['total_len'])
                    return parsed
                else:
                    logger.debug("Skipping packet FNC=0x%02X (want 0x%02X)",
                                  parsed['fnc'], expected_fnc)

            # Trim processed data from buffer but keep potential partial packet
            if packets:
                last_end = max(end for _, end in packets)
                buffer = buffer[last_end:]

            # Safety: don't let buffer grow unbounded
            if len(buffer) > 2048:
                # Keep only the tail — any valid packet start is at most ~120 bytes back
                buffer = buffer[-512:]

        logger.warning("Timeout waiting for response FNC=0x%02X (buffer had %d bytes)",
                        expected_fnc, len(buffer))
        if buffer:
            logger.debug("Remaining buffer: %s", buffer[:64].hex())
        return None


# ──────────────────────────────────────────────────────────────────────
# Direct mode: request-response polling
# ──────────────────────────────────────────────────────────────────────

def send_and_receive(conn: SerialConnection, request: bytes,
                     expected_fnc: int) -> dict | None:
    """Flush stale data, send request, read response."""
    conn.flush_input()
    conn.send(request)
    return conn.read_packet(expected_fnc, timeout=5.0)


def read_status(conn: SerialConnection) -> list[int] | None:
    """Read status registers from PCWU. Returns raw register list or None."""
    req = build_read_request(
        DEV_HARD_ID, CON_HARD_ID,
        DEV_SOFT_ID, CON_SOFT_ID,
        FNC_READ_STATUS_REQ, STATUS_REG_BASE, STATUS_REG_COUNT
    )
    resp = send_and_receive(conn, req, FNC_READ_STATUS_RESP)
    if resp and 'reg_data' in resp:
        raw_regs = extract_registers(resp['reg_data'], resp.get('reg_count', STATUS_REG_COUNT))
        if len(raw_regs) >= 14:  # minimum: we need at least T1-T10 + status
            return raw_regs
        else:
            logger.warning("Status response too short: %d registers", len(raw_regs))
    else:
        logger.warning("No valid status response received")
    return None


def read_config(conn: SerialConnection) -> list[int] | None:
    """Read config registers from PCWU. Returns raw register list or None."""
    req = build_read_request(
        DEV_HARD_ID, CON_HARD_ID,
        DEV_SOFT_ID, CON_SOFT_ID,
        FNC_READ_CONFIG_REQ, CONFIG_REG_BASE, CONFIG_REG_COUNT
    )
    resp = send_and_receive(conn, req, FNC_READ_CONFIG_RESP)
    if resp and 'reg_data' in resp:
        raw_regs = extract_registers(resp['reg_data'], resp.get('reg_count', CONFIG_REG_COUNT))
        if len(raw_regs) >= 10:  # minimum sanity check
            return raw_regs
        else:
            logger.warning("Config response too short: %d registers", len(raw_regs))
    else:
        logger.warning("No valid config response received")
    return None


def write_config_register(conn: SerialConnection, reg_name: str, value) -> bool:
    """
    Write a single config register using read-modify-write.

    1. Use cached config registers (from last read_config)
    2. Modify the target register
    3. Send the entire block back with sub_fnc=0xA0
    4. Read the response to confirm
    """
    global cached_config_regs

    if cached_config_regs is None:
        logger.warning("Cannot write %s: no cached config registers. "
                        "Will read first on next poll cycle.", reg_name)
        return False

    # Encode the user value to a register offset + raw 16-bit value
    encoded = encode_config_value(reg_name, value)
    if encoded is None:
        logger.error("Failed to encode %s = %s", reg_name, value)
        return False

    reg_offset, raw_value = encoded
    if reg_offset >= len(cached_config_regs):
        logger.error("Register offset %d out of range (have %d cached regs)",
                      reg_offset, len(cached_config_regs))
        return False

    # Modify the cached block
    old_value = cached_config_regs[reg_offset]
    cached_config_regs[reg_offset] = raw_value
    logger.info("Writing %s: offset=%d old=0x%04X new=0x%04X",
                 reg_name, reg_offset, old_value, raw_value)

    # Build and send the write-back request with the full modified block
    reg_data = registers_to_bytes(cached_config_regs)
    req = build_write_request(
        DEV_HARD_ID, CON_HARD_ID,
        DEV_SOFT_ID, CON_SOFT_ID,
        CONFIG_REG_BASE, len(cached_config_regs),
        reg_data
    )

    resp = send_and_receive(conn, req, FNC_READ_CONFIG_RESP)
    if resp and 'reg_data' in resp:
        # Update cache with the confirmed values from the device
        confirmed_regs = extract_registers(resp['reg_data'],
                                            resp.get('reg_count', CONFIG_REG_COUNT))
        if confirmed_regs:
            cached_config_regs = confirmed_regs
            if reg_offset < len(confirmed_regs):
                if confirmed_regs[reg_offset] == raw_value:
                    logger.info("Write confirmed: %s = 0x%04X", reg_name, raw_value)
                    return True
                else:
                    logger.warning("Write NOT confirmed: %s expected=0x%04X got=0x%04X",
                                    reg_name, raw_value, confirmed_regs[reg_offset])
                    return False
        logger.info("Write sent, response received (could not verify value)")
        return True
    else:
        # Revert cache on failure
        cached_config_regs[reg_offset] = old_value
        logger.error("Write failed: no response for %s", reg_name)
        return False


def poll_direct(conn: SerialConnection, mqtt_client: HewalexMQTT):
    """
    Direct mode: one poll cycle.

    Order: process writes → read status → read config.
    Inter-request delay of 1s to avoid overwhelming the RS485 bus.
    """
    global write_queue, cached_config_regs

    # 1. Process pending writes (needs cached config from a prior read)
    writes_processed = []
    while write_queue:
        reg_name, value = write_queue.pop(0)
        success = write_config_register(conn, reg_name, value)
        writes_processed.append((reg_name, value, success))
        time.sleep(1.0)

    # 2. Read status registers
    raw_status = read_status(conn)
    if raw_status:
        parsed = parse_status_registers(raw_status)
        mqtt_client.publish_status(parsed)
        if LOG_LEVEL == 'DEBUG':
            mqtt_client.publish_raw_registers('status', STATUS_REG_BASE, raw_status)
            logger.debug("Status: %s", {k: v for k, v in parsed.items()
                                         if k.startswith('T') or k.endswith('ON')})

    time.sleep(1.0)

    # 3. Read config registers (and cache for future writes)
    raw_config = read_config(conn)
    if raw_config:
        cached_config_regs = list(raw_config)  # cache a copy for write-back
        parsed = parse_config_registers(raw_config)
        mqtt_client.publish_config(parsed)
        if LOG_LEVEL == 'DEBUG':
            mqtt_client.publish_raw_registers('config', CONFIG_REG_BASE, raw_config)
            logger.debug("Config: %s", parsed)

    # Log write results after re-reading (so HA state is up to date)
    for reg_name, value, success in writes_processed:
        if success:
            logger.info("Write complete: %s = %s", reg_name, value)
        else:
            logger.warning("Write may have failed: %s = %s", reg_name, value)


# ──────────────────────────────────────────────────────────────────────
# Eavesdrop mode: passive packet capture
# ──────────────────────────────────────────────────────────────────────

def run_eavesdrop(conn: SerialConnection, mqtt_client: HewalexMQTT):
    """
    Eavesdrop mode: passively listen to G-426 ↔ PCWU traffic.

    Runs in a continuous loop (not per-poll). Captures response packets
    from the PCWU and publishes their contents. Cannot send commands.
    """
    logger.info("Entering eavesdrop mode — listening for traffic...")
    buffer = b''
    last_status_time = 0
    last_config_time = 0

    while running:
        try:
            conn.ser.timeout = 2.0
            chunk = conn.ser.read(512)
            if chunk:
                buffer += chunk

                packets = find_packets(buffer)
                for parsed, end_pos in packets:
                    fnc = parsed['fnc']

                    if fnc == FNC_READ_STATUS_RESP and 'reg_data' in parsed:
                        raw_regs = extract_registers(
                            parsed['reg_data'],
                            parsed.get('reg_count', STATUS_REG_COUNT)
                        )
                        if raw_regs and len(raw_regs) >= 14:
                            status = parse_status_registers(raw_regs)
                            mqtt_client.publish_status(status)
                            last_status_time = time.time()
                            logger.debug("Eavesdrop: status update (%d regs)", len(raw_regs))

                    elif fnc == FNC_READ_CONFIG_RESP and 'reg_data' in parsed:
                        raw_regs = extract_registers(
                            parsed['reg_data'],
                            parsed.get('reg_count', CONFIG_REG_COUNT)
                        )
                        if raw_regs and len(raw_regs) >= 10:
                            config = parse_config_registers(raw_regs)
                            mqtt_client.publish_config(config)
                            last_config_time = time.time()
                            logger.debug("Eavesdrop: config update (%d regs)", len(raw_regs))

                # Trim processed data
                if packets:
                    last_end = max(end for _, end in packets)
                    buffer = buffer[last_end:]

                if len(buffer) > 4096:
                    buffer = buffer[-2048:]

            # Log if we haven't seen data in a while
            now = time.time()
            if last_status_time and (now - last_status_time > 120):
                logger.warning("No status update in >120s — check RS485 connection")
                last_status_time = now  # reset to avoid spamming

        except serial.SerialTimeoutException:
            continue
        except serial.SerialException as e:
            logger.error("Eavesdrop serial error: %s", e)
            raise  # let main loop handle reconnection
        except Exception as e:
            logger.error("Eavesdrop error: %s", e, exc_info=True)
            time.sleep(1)


# ──────────────────────────────────────────────────────────────────────
# Command handler (called from MQTT when HA sends a command)
# ──────────────────────────────────────────────────────────────────────

def on_ha_command(register_name: str, value):
    """Queue a write command from Home Assistant."""
    if MODE == 'eavesdrop':
        logger.warning("Cannot write in eavesdrop mode: %s = %s", register_name, value)
        return

    logger.info("Queuing write: %s = %s", register_name, value)
    write_queue.append((register_name, value))


# ──────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────

def main():
    global running

    logger.info("=" * 60)
    logger.info("Hewalex PCWU Add-on v1.0.1 starting")
    logger.info("  Mode: %s", MODE)
    logger.info("  Device: %s:%d", DEVICE_ADDRESS, DEVICE_PORT)
    logger.info("  MQTT: %s:%d", MQTT_HOST, MQTT_PORT)
    logger.info("  Controller IDs: hard=%d soft=%d", CON_HARD_ID, CON_SOFT_ID)
    logger.info("  Device IDs: hard=%d soft=%d", DEV_HARD_ID, DEV_SOFT_ID)
    logger.info("  Poll interval: %ds", POLL_INTERVAL)
    logger.info("=" * 60)

    # Initialize MQTT
    mqtt_client = HewalexMQTT(
        host=MQTT_HOST,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASSWORD,
        topic_prefix=MQTT_TOPIC_PREFIX,
        discovery_prefix=MQTT_DISCOVERY_PREFIX,
        on_command_callback=on_ha_command,
    )

    try:
        mqtt_client.connect()
    except Exception as e:
        logger.error("Failed to connect to MQTT: %s", e)
        sys.exit(1)

    # Wait for MQTT connection
    for i in range(30):
        if mqtt_client.connected:
            break
        time.sleep(1)
    else:
        logger.error("MQTT connection timeout after 30s")
        sys.exit(1)

    logger.info("MQTT connected, starting main loop")

    # Serial connection (persistent)
    conn = SerialConnection(DEVICE_ADDRESS, DEVICE_PORT)
    consecutive_errors = 0

    while running:
        try:
            # Ensure connection is open
            if not conn.is_open:
                try:
                    conn.connect()
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    backoff = min(consecutive_errors * 5, 60)
                    logger.error("Connection failed (#%d): %s — retry in %ds",
                                  consecutive_errors, e, backoff)
                    time.sleep(backoff)
                    continue

            if MODE == 'eavesdrop':
                run_eavesdrop(conn, mqtt_client)
            else:
                # Direct mode: poll cycle
                poll_direct(conn, mqtt_client)
                consecutive_errors = 0

                # Interruptible sleep between polls
                for _ in range(POLL_INTERVAL * 2):
                    if not running:
                        break
                    if write_queue:
                        logger.debug("Write pending, interrupting sleep")
                        break
                    time.sleep(0.5)

        except serial.SerialException as e:
            logger.error("Serial error: %s", e)
            consecutive_errors += 1
            conn.close()
            backoff = min(consecutive_errors * 5, 60)
            logger.info("Reconnecting in %ds...", backoff)
            time.sleep(backoff)

        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            consecutive_errors += 1
            conn.close()
            time.sleep(5)

    # Cleanup
    logger.info("Shutting down...")
    conn.close()
    mqtt_client.disconnect()
    logger.info("Goodbye!")


if __name__ == '__main__':
    main()
