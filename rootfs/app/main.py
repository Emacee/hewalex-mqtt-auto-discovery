#!/usr/bin/env python3
"""
Hewalex PCWU Home Assistant add-on.

direct    – the add-on is master on the PCWU's free RS485 port: it polls the
            status block (120..301) and the settings block (302..537) and can
            change settings (0x60 write, verified by reading the register back).
eavesdrop – the adapter listens on the cable between G-426 controller and
            PCWU. The PCWU writes its status block to the controller twice a
            second (0x60 at 120); those frames are decoded. Read only.
"""

from __future__ import annotations

import logging
import os
import queue
import signal
import sys
import threading
import time

import serial

import pcwu
from bus import GecoClient, GecoDeviceError, GecoTimeout, Transport
from geco import FNC_WRITE, FrameParser, GecoError
from ha_mqtt import HewalexMqtt

VERSION = os.environ.get("ADDON_VERSION", "dev")


def env(name: str, default: str) -> str:
    value = os.environ.get(name, "")
    return default if value in ("", "null") else value


DEVICE_ADDRESS = env("DEVICE_ADDRESS", "192.168.1.100")
DEVICE_PORT = int(env("DEVICE_PORT", "8899"))
MODE = env("MODE", "direct")
POLL_INTERVAL = int(env("POLL_INTERVAL", "30"))
EXPERT_MODE = env("EXPERT_MODE", "false").lower() == "true"
LOG_LEVEL = env("LOG_LEVEL", "info").upper()

CON_HARD_ID = int(env("CONTROLLER_HARD_ID", "1"))
CON_SOFT_ID = int(env("CONTROLLER_SOFT_ID", "1"))
DEV_HARD_ID = int(env("DEVICE_HARD_ID", "2"))
DEV_SOFT_ID = int(env("DEVICE_SOFT_ID", "2"))

FAILURES_BEFORE_UNAVAILABLE = 3
FULL_REPUBLISH_INTERVAL = 600

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("hewalex")

stop_event = threading.Event()


def _stop(signum, frame):
    logger.info("Stop requested")
    stop_event.set()


class Bridge:
    def __init__(self, mqtt_client: HewalexMqtt, points: list[pcwu.Point]):
        self.mqtt = mqtt_client
        self.points = points
        self.image = pcwu.RegisterImage()
        self.last_full_publish = 0.0

    def publish(self, only_status: bool = False) -> None:
        force = time.monotonic() - self.last_full_publish > FULL_REPUBLISH_INTERVAL
        if force:
            self.last_full_publish = time.monotonic()
        for point in self.points:
            if only_status and point.address is not None and point.address >= pcwu.CONFIG_START:
                continue
            self.mqtt.publish_value(point, pcwu.decode(point, self.image), force=force)


# ──────────────────────────────────────────────────────────────────────
# Direct mode
# ──────────────────────────────────────────────────────────────────────

def poll(client: GecoClient, bridge: Bridge) -> None:
    for name, (start, count) in (("status", pcwu.STATUS_BLOCK), ("config", pcwu.CONFIG_BLOCK)):
        data = client.read(start, count)
        bridge.image.update(start, data)
        if LOG_LEVEL == "DEBUG":
            bridge.mqtt.publish_debug(name, start, data)
    bridge.publish()


def execute_command(client: GecoClient, bridge: Bridge, key: str, payload: str) -> None:
    point = pcwu.POINTS_BY_KEY.get(key)
    if point is None or point not in bridge.points or not point.writable:
        logger.warning("Ignoring command for unknown or read-only entity %s", key)
        return
    try:
        raw = pcwu.encode(point, payload)
    except ValueError as exc:
        logger.error("Rejected %s = %r: %s", key, payload, exc)
        bridge.mqtt.publish_value(point, pcwu.decode(point, bridge.image), force=True)
        return

    old = bridge.image.u16(point.address)
    logger.info("Writing %s (register %d) = %r -> raw %d (was %s)", key, point.address, payload, raw, old)
    client.write_u16(point.address, raw)
    readback = client.read(point.address, 2)
    bridge.image.update(point.address, readback)
    actual = bridge.image.u16(point.address)
    if actual == raw:
        logger.info("Verified %s = %s", key, pcwu.decode(point, bridge.image))
    else:
        logger.error("PCWU did not accept %s = %r: register %d reads %s instead of %d "
                     "(value probably outside the range the controller allows)",
                     key, payload, point.address, actual, raw)
    bridge.mqtt.publish_value(point, pcwu.decode(point, bridge.image), force=True)
    # Some settings change the operating state; refresh the status block soon.
    data = client.read(*pcwu.STATUS_BLOCK)
    bridge.image.update(pcwu.STATUS_START, data)
    bridge.publish(only_status=True)


def run_direct(transport: Transport, bridge: Bridge, commands: queue.Queue) -> None:
    client = GecoClient(transport, CON_HARD_ID, CON_SOFT_ID, DEV_HARD_ID, DEV_SOFT_ID)
    failures = 0
    next_poll = 0.0
    while not stop_event.is_set():
        try:
            if not transport.is_open:
                transport.open()
            if time.monotonic() >= next_poll:
                poll(client, bridge)
                failures = 0
                bridge.mqtt.set_available(True)
                next_poll = time.monotonic() + POLL_INTERVAL
            try:
                key, payload = commands.get(timeout=max(0.1, min(1.0, next_poll - time.monotonic())))
            except queue.Empty:
                continue
            execute_command(client, bridge, key, payload)
        except GecoDeviceError as exc:
            logger.error("%s", exc)
        except (GecoTimeout, GecoError, serial.SerialException, OSError) as exc:
            failures += 1
            logger.warning("Communication with PCWU failed (%d): %s", failures, exc)
            if failures >= FAILURES_BEFORE_UNAVAILABLE:
                bridge.mqtt.set_available(False)
            transport.close()
            next_poll = time.monotonic() + min(5 * failures, 60)
            stop_event.wait(min(5 * failures, 60))


# ──────────────────────────────────────────────────────────────────────
# Eavesdrop mode
# ──────────────────────────────────────────────────────────────────────

def run_eavesdrop(transport: Transport, bridge: Bridge, commands: queue.Queue) -> None:
    parser = FrameParser()
    last_frame = 0.0
    last_publish = 0.0
    last_warning = time.monotonic()
    seen = 0
    while not stop_event.is_set():
        try:
            if not transport.is_open:
                transport.open()
                parser.clear()
            while not commands.empty():
                key, _ = commands.get_nowait()
                logger.warning("Ignoring command for %s: eavesdrop mode is read only", key)
            chunk = transport.read(1.0)
            for frame in parser.feed(chunk):
                # Status block written by the PCWU to the controller display.
                if (frame.fnc == FNC_WRITE and not frame.is_error
                        and pcwu.STATUS_START <= frame.start < pcwu.CONFIG_START
                        and frame.start + frame.count <= pcwu.CONFIG_START):
                    bridge.image.update(frame.start, frame.data)
                    last_frame = time.monotonic()
                    seen += 1
                    if seen == 1:
                        logger.info("Receiving status frames from the PCWU")
            now = time.monotonic()
            receiving = last_frame and now - last_frame < 60
            if receiving and now - last_publish >= POLL_INTERVAL:
                bridge.publish()
                bridge.mqtt.set_available(True)
                last_publish = now
            elif not receiving and now - last_warning >= 60:
                logger.warning("No PCWU status frame in the last 60 s – check wiring (A/B), "
                               "adapter settings (38400 8N1) and the adapter address")
                bridge.mqtt.set_available(False)
                last_warning = now
        except (serial.SerialException, OSError) as exc:
            logger.warning("Connection to adapter failed: %s", exc)
            bridge.mqtt.set_available(False)
            transport.close()
            stop_event.wait(10)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if MODE not in ("direct", "eavesdrop"):
        logger.error("Unknown mode %r", MODE)
        sys.exit(1)

    logger.info("Hewalex PCWU add-on %s – mode %s, adapter %s:%s, controller %d/%d, PCWU %d/%d, poll %ds%s",
                VERSION, MODE, DEVICE_ADDRESS, DEVICE_PORT, CON_HARD_ID, CON_SOFT_ID, DEV_HARD_ID, DEV_SOFT_ID,
                POLL_INTERVAL, ", expert mode" if EXPERT_MODE else "")

    exposed = pcwu.points_for(expert=EXPERT_MODE, writable_allowed=(MODE == "direct"))
    hidden = [p for p in pcwu.POINTS if p not in exposed]

    commands: queue.Queue = queue.Queue()
    mqtt_client = HewalexMqtt(
        host=env("MQTT_HOST", "127.0.0.1"), port=int(env("MQTT_PORT", "1883")),
        user=env("MQTT_USER", ""), password=env("MQTT_PASSWORD", ""),
        topic_prefix=env("MQTT_TOPIC_PREFIX", "hewalex"),
        discovery_prefix=env("MQTT_DISCOVERY_PREFIX", "homeassistant"),
        version=VERSION, commands=commands,
    )
    mqtt_client.start(exposed, hidden)

    bridge = Bridge(mqtt_client, exposed)
    transport = Transport(DEVICE_ADDRESS, DEVICE_PORT)
    try:
        if MODE == "direct":
            run_direct(transport, bridge, commands)
        else:
            run_eavesdrop(transport, bridge, commands)
    finally:
        transport.close()
        mqtt_client.stop()
        logger.info("Stopped")


if __name__ == "__main__":
    main()
