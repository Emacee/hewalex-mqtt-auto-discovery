"""
MQTT client with Home Assistant MQTT discovery.

Every PCWU point becomes an entity of one "Hewalex PCWU" device. Commands from
HA are put on a queue and executed by the bus thread; the entity state is only
updated from what the PCWU reports back (never optimistic).
"""

from __future__ import annotations

import json
import logging
import queue
import time

import paho.mqtt.client as mqtt

from pcwu import Point, STATE_OPTIONS

logger = logging.getLogger("hewalex.mqtt")

DEVICE_ID = "hewalex_pcwu"

# Entities published by add-on version 1.0.x that no longer exist under the same
# platform/key. Their retained discovery configs are removed on startup.
LEGACY_DISCOVERY = [
    ("binary_sensor", "circulationpumpon"),
    ("binary_sensor", "heatpumpon"),
    ("binary_sensor", "ismanual"),
    ("number", "circpumpmintemp"),
    ("select", "circpumpmode"),
    ("select", "heaterepowerlimit"),
]

# Retained state topics of version 1.0.x ("<prefix>/<platform>/<key>"), cleared on startup.
LEGACY_STATE = (
    [f"sensor/{k}" for k in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "EV1", "WaitingStatus")]
    + [f"binary_sensor/{k}" for k in ("FanON", "CirculationPumpON", "HeatPumpON", "CompressorON", "HeaterEON",
                                      "IsManual")]
    + [f"switch/{k}" for k in ("HeatPumpEnabled", "HeaterEEnabled", "AntiFreezingEnabled", "ExtControllerHPOFF")]
    + [f"number/{k}" for k in ("TapWaterTemp", "TapWaterHysteresis", "AmbientMinTemp", "DefrostingInterval",
                               "DefrostingStartTemp", "DefrostingStopTemp", "DefrostingMaxTime", "CircPumpMinTemp")]
    + [f"select/{k}" for k in ("TapWaterSensor", "WaterPumpOperationMode", "FanOperationMode", "CircPumpMode",
                               "HeaterEPowerLimit")]
    + ["status"]
)


class HewalexMqtt:
    def __init__(self, host: str, port: int, user: str, password: str, topic_prefix: str,
                 discovery_prefix: str, version: str, commands: queue.Queue):
        self.topic_prefix = topic_prefix.strip("/")
        self.discovery_prefix = discovery_prefix.strip("/")
        self.version = version
        self.commands = commands
        self.points: list[Point] = []
        self.hidden: list[Point] = []
        self._cache: dict[str, str] = {}
        self._available = False
        self.connected = False
        self._host, self._port = host, port

        self.availability_topic = f"{self.topic_prefix}/availability"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hewalex-pcwu-{int(time.time())}")
        if user:
            self.client.username_pw_set(user, password or None)
        self.client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 60)

    # ── connection ───────────────────────────────────────────────────

    def start(self, points: list[Point], hidden: list[Point]) -> None:
        self.points = points
        self.hidden = hidden
        logger.info("Connecting to MQTT broker %s:%d", self._host, self._port)
        self.client.connect_async(self._host, self._port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.publish(self.availability_topic, "offline", qos=1, retain=True).wait_for_publish(2)
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error("MQTT connection refused: %s", reason_code)
            return
        logger.info("Connected to MQTT broker")
        self.connected = True
        client.subscribe(f"{self.topic_prefix}/+/set", qos=1)
        client.subscribe(f"{self.discovery_prefix}/status", qos=1)
        self._publish_discovery()
        self._republish_all()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        if reason_code.is_failure:
            logger.warning("MQTT connection lost (%s), reconnecting", reason_code)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")
        if msg.topic == f"{self.discovery_prefix}/status":
            if payload == "online":
                logger.info("Home Assistant came online, republishing discovery and states")
                self._publish_discovery()
                self._republish_all()
            return
        key = msg.topic[len(self.topic_prefix) + 1:-len("/set")]
        self.commands.put((key, payload))

    # ── discovery ────────────────────────────────────────────────────

    def _uid(self, point: Point) -> str:
        return f"{DEVICE_ID}_{point.key.lower().replace('-', '_')}"

    def _discovery_topic(self, platform: str, object_id: str) -> str:
        return f"{self.discovery_prefix}/{platform}/{object_id}/config"

    def state_topic(self, point: Point) -> str:
        return f"{self.topic_prefix}/{point.key}"

    def discovery_payload(self, point: Point) -> dict:
        config = {
            "name": point.name,
            "unique_id": self._uid(point),
            "state_topic": self.state_topic(point),
            "availability_topic": self.availability_topic,
            "device": {
                "identifiers": [DEVICE_ID],
                "name": "Hewalex PCWU",
                "manufacturer": "Hewalex",
                "model": "PCWU heat pump (G-426)",
            },
            "origin": {
                "name": "Hewalex PCWU add-on",
                "sw_version": self.version,
                "support_url": "https://github.com/Emacee/hewalex-mqtt-auto-discovery",
            },
        }
        if point.icon:
            config["icon"] = point.icon
        if point.category:
            config["entity_category"] = point.category
        if not point.enabled:
            config["enabled_by_default"] = False
        if point.device_class:
            config["device_class"] = point.device_class
        if point.unit:
            config["unit_of_measurement"] = point.unit
        if point.state_class:
            config["state_class"] = point.state_class

        if point.platform == "sensor":
            if point.key == "OperatingState":
                config["options"] = STATE_OPTIONS
            elif point.device_class == "enum":
                config["options"] = list(point.options.values())
            if point.kind == "temp":
                config["suggested_display_precision"] = 1
        elif point.platform in ("binary_sensor", "switch"):
            config["payload_on"] = "ON"
            config["payload_off"] = "OFF"
        if point.writable:
            config["command_topic"] = f"{self.topic_prefix}/{point.key}/set"
            config["retain"] = False
        if point.platform == "switch":
            config["state_on"] = "ON"
            config["state_off"] = "OFF"
        elif point.platform == "number":
            config.update(min=point.minimum, max=point.maximum, step=point.step, mode="box")
        elif point.platform == "select":
            config["options"] = list(point.options.values())
        return config

    def _publish_discovery(self) -> None:
        for platform, object_id in LEGACY_DISCOVERY:
            self.client.publish(self._discovery_topic(platform, f"{DEVICE_ID}_{object_id}"), "", qos=1, retain=True)
        for suffix in LEGACY_STATE:
            self.client.publish(f"{self.topic_prefix}/{suffix}", "", qos=1, retain=True)
        for point in self.hidden:
            self.client.publish(self._discovery_topic(point.platform, self._uid(point)), "", qos=1, retain=True)
        for point in self.points:
            self.client.publish(self._discovery_topic(point.platform, self._uid(point)),
                                json.dumps(self.discovery_payload(point)), qos=1, retain=True)
        logger.info("Published Home Assistant discovery for %d entities", len(self.points))

    # ── state ────────────────────────────────────────────────────────

    @staticmethod
    def format_value(point: Point, value) -> str:
        if isinstance(value, bool):
            return "ON" if value else "OFF"
        if isinstance(value, float):
            return f"{value:.1f}"
        return str(value)

    def publish_value(self, point: Point, value, force: bool = False) -> None:
        if value is None:
            return
        text = self.format_value(point, value)
        if not force and self._cache.get(point.key) == text:
            return
        self._cache[point.key] = text
        if self.connected:
            self.client.publish(self.state_topic(point), text, qos=0, retain=True)

    def set_available(self, available: bool) -> None:
        if available != self._available:
            logger.info("Device %s", "reachable" if available else "NOT reachable, entities unavailable")
        self._available = available
        if self.connected:
            self.client.publish(self.availability_topic, "online" if available else "offline", qos=1, retain=True)

    def _republish_all(self) -> None:
        self.client.publish(self.availability_topic, "online" if self._available else "offline", qos=1, retain=True)
        for point in self.points:
            if point.key in self._cache:
                self.client.publish(self.state_topic(point), self._cache[point.key], qos=0, retain=True)

    def forget_cache(self) -> None:
        self._cache.clear()

    def publish_debug(self, name: str, start: int, data: bytes) -> None:
        if self.connected:
            payload = {"start": start, "hex": data.hex(),
                       "words": {str(start + i): data[i] | (data[i + 1] << 8) for i in range(0, len(data) - 1, 2)}}
            self.client.publish(f"{self.topic_prefix}/debug/{name}", json.dumps(payload), qos=0, retain=False)
