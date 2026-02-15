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

Based on protocol research from:
  - https://github.com/aelias-eu/hewalex-geco-protocol
  - https://github.com/mvdklip/Domoticz-Hewalex
  - https://github.com/Chibald/Hewalex2Mqtt
"""

import os
import sys
import time
import logging
import signal
import serial

from geco_protocol import (
    build_read_request, build_write_request, parse_packet,
    find_packets, extract_registers, signed16,
    FNC_READ_STATUS_REQ, FNC_READ_STATUS_RESP,
    FNC_READ_CONFIG_REQ, FNC_READ_CONFIG_RESP,
    HEADER_LEN,
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
MODE = os.environ.get('MODE', 'direct')  # 'direct' or 'eavesdrop'
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
write_queue = []  # list of (register_name, value) tuples pending write


def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ──────────────────────────────────────────────────────────────────────
# Serial / TCP connection
# ──────────────────────────────────────────────────────────────────────

def open_connection() -> serial.Serial:
    """Open a TCP socket connection to the RS485-to-WiFi adapter via pyserial."""
    url = f'socket://{DEVICE_ADDRESS}:{DEVICE_PORT}'
    logger.info("Opening connection to %s", url)
    ser = serial.serial_for_url(url, baudrate=38400, timeout=5)
    return ser


def send_and_receive(ser: serial.Serial, request: bytes,
                     expected_fnc: int, timeout: float = 5.0) -> dict | None:
    """
    Send a request and wait for the matching response.

    Flushes any stale data, sends the request, then reads until we get
    a valid response with the expected function code or timeout.
    """
    # Flush input buffer
    ser.reset_input_buffer()

    logger.debug("TX (%d bytes): %s", len(request), request.hex())
    ser.write(request)

    # Read response
    start_time = time.time()
    buffer = b''

    while time.time() - start_time < timeout:
        chunk = ser.read(256)
        if chunk:
            buffer += chunk
            logger.debug("RX chunk (%d bytes), total buffer: %d",
                          len(chunk), len(buffer))

            # Try to parse packets from buffer
            packets = find_packets(buffer)
            for parsed, end_pos in packets:
                if parsed['fnc'] == expected_fnc:
                    logger.debug("Got expected response FNC=0x%02X", expected_fnc)
                    return parsed

            # If buffer is getting large without valid packets, trim
            if len(buffer) > 1024:
                buffer = buffer[-512:]
        else:
            time.sleep(0.1)

    logger.warning("Timeout waiting for response FNC=0x%02X", expected_fnc)
    return None


# ──────────────────────────────────────────────────────────────────────
# Direct communication mode
# ──────────────────────────────────────────────────────────────────────

def poll_direct(ser: serial.Serial, mqtt_client: HewalexMQTT):
    """
    Direct mode: send register read requests and process responses.
    Also processes any pending write commands.
    """
    global write_queue

    # 1. Process pending writes first
    while write_queue:
        reg_name, value = write_queue.pop(0)
        result = encode_config_value(reg_name, value)
        if result is None:
            logger.error("Failed to encode write for %s = %s", reg_name, value)
            continue

        reg_offset, raw_value = result
        logger.info("Writing config: %s (offset=%d) = %d (raw=0x%04X)",
                     reg_name, reg_offset, raw_value, raw_value)

        req = build_write_request(
            DEV_HARD_ID, CON_HARD_ID,
            DEV_SOFT_ID, CON_SOFT_ID,
            CONFIG_REG_BASE, reg_offset,
            raw_value, CONFIG_REG_COUNT
        )
        resp = send_and_receive(ser, req, FNC_READ_CONFIG_RESP, timeout=5)
        if resp and 'reg_data' in resp:
            logger.info("Write confirmed for %s", reg_name)
        else:
            logger.warning("No confirmation for write to %s", reg_name)

        time.sleep(0.5)  # small delay between operations

    # 2. Read status registers
    req = build_read_request(
        DEV_HARD_ID, CON_HARD_ID,
        DEV_SOFT_ID, CON_SOFT_ID,
        FNC_READ_STATUS_REQ, STATUS_REG_BASE, STATUS_REG_COUNT
    )
    resp = send_and_receive(ser, req, FNC_READ_STATUS_RESP, timeout=5)

    if resp and 'reg_data' in resp:
        raw_regs = extract_registers(resp['reg_data'], resp.get('reg_count', STATUS_REG_COUNT))
        if raw_regs:
            parsed = parse_status_registers(raw_regs)
            logger.debug("Status: %s", parsed)
            mqtt_client.publish_status(parsed)

            if LOG_LEVEL == 'DEBUG':
                mqtt_client.publish_raw_registers('status', STATUS_REG_BASE, raw_regs)
    else:
        logger.warning("Failed to read status registers")

    time.sleep(1)  # gap between requests

    # 3. Read config registers
    req = build_read_request(
        DEV_HARD_ID, CON_HARD_ID,
        DEV_SOFT_ID, CON_SOFT_ID,
        FNC_READ_CONFIG_REQ, CONFIG_REG_BASE, CONFIG_REG_COUNT
    )
    resp = send_and_receive(ser, req, FNC_READ_CONFIG_RESP, timeout=5)

    if resp and 'reg_data' in resp:
        raw_regs = extract_registers(resp['reg_data'], resp.get('reg_count', CONFIG_REG_COUNT))
        if raw_regs:
            parsed = parse_config_registers(raw_regs)
            logger.debug("Config: %s", parsed)
            mqtt_client.publish_config(parsed)

            if LOG_LEVEL == 'DEBUG':
                mqtt_client.publish_raw_registers('config', CONFIG_REG_BASE, raw_regs)
    else:
        logger.warning("Failed to read config registers")


# ──────────────────────────────────────────────────────────────────────
# Eavesdrop communication mode
# ──────────────────────────────────────────────────────────────────────

def poll_eavesdrop(ser: serial.Serial, mqtt_client: HewalexMQTT):
    """
    Eavesdrop mode: passively listen for G-426 ↔ PCWU traffic.

    In this mode we cannot send commands; we just parse responses
    that the PCWU sends to the G-426 controller and extract data.
    """
    buffer = b''
    read_timeout = POLL_INTERVAL

    ser.timeout = read_timeout

    while running:
        try:
            chunk = ser.read(512)
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
                        if raw_regs:
                            status = parse_status_registers(raw_regs)
                            logger.debug("Eavesdrop status: %s", status)
                            mqtt_client.publish_status(status)

                    elif fnc == FNC_READ_CONFIG_RESP and 'reg_data' in parsed:
                        raw_regs = extract_registers(
                            parsed['reg_data'],
                            parsed.get('reg_count', CONFIG_REG_COUNT)
                        )
                        if raw_regs:
                            config = parse_config_registers(raw_regs)
                            logger.debug("Eavesdrop config: %s", config)
                            mqtt_client.publish_config(config)

                # Keep only unprocessed data in buffer
                if packets:
                    last_end = packets[-1][1]
                    buffer = buffer[last_end:]

                # Prevent buffer from growing unbounded
                if len(buffer) > 4096:
                    buffer = buffer[-2048:]

        except serial.SerialTimeoutException:
            continue
        except Exception as e:
            logger.error("Eavesdrop error: %s", e)
            time.sleep(1)


# ──────────────────────────────────────────────────────────────────────
# Command handler (called from MQTT when HA sends a command)
# ──────────────────────────────────────────────────────────────────────

def on_ha_command(register_name: str, value):
    """Queue a write command from Home Assistant."""
    if MODE == 'eavesdrop':
        logger.warning("Cannot write in eavesdrop mode, ignoring command: %s = %s",
                        register_name, value)
        return

    logger.info("Queuing write command: %s = %s", register_name, value)
    write_queue.append((register_name, value))


# ──────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────

def main():
    global running

    logger.info("=" * 60)
    logger.info("Hewalex PCWU Add-on starting")
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
    for _ in range(30):
        if mqtt_client.connected:
            break
        time.sleep(1)
    else:
        logger.error("MQTT connection timeout")
        sys.exit(1)

    # Main loop with automatic reconnection
    ser = None
    consecutive_errors = 0

    while running:
        try:
            # Open serial/TCP connection if needed
            if ser is None or not ser.is_open:
                try:
                    ser = open_connection()
                    consecutive_errors = 0
                    logger.info("Serial connection established")
                except Exception as e:
                    consecutive_errors += 1
                    backoff = min(consecutive_errors * 5, 60)
                    logger.error("Connection failed (%d): %s. Retrying in %ds",
                                  consecutive_errors, e, backoff)
                    time.sleep(backoff)
                    continue

            if MODE == 'eavesdrop':
                # Eavesdrop mode runs its own inner loop
                poll_eavesdrop(ser, mqtt_client)
            else:
                # Direct mode: poll, sleep, repeat
                poll_direct(ser, mqtt_client)
                consecutive_errors = 0

                # Sleep between polls (interruptible)
                for _ in range(POLL_INTERVAL * 2):
                    if not running:
                        break
                    # Check for pending writes more frequently
                    if write_queue:
                        break
                    time.sleep(0.5)

        except serial.SerialException as e:
            logger.error("Serial error: %s", e)
            consecutive_errors += 1
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            backoff = min(consecutive_errors * 5, 60)
            logger.info("Reconnecting in %ds...", backoff)
            time.sleep(backoff)

        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            consecutive_errors += 1
            time.sleep(5)

    # Cleanup
    logger.info("Shutting down...")
    if ser and ser.is_open:
        ser.close()
    mqtt_client.disconnect()
    logger.info("Goodbye!")


if __name__ == '__main__':
    main()
