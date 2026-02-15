"""
MQTT Client with Home Assistant MQTT Auto-Discovery.

Publishes Hewalex PCWU sensor data and control entities to MQTT
using the HA discovery protocol so entities appear automatically.

Discovery format: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""

import json
import logging
import time
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

DEVICE_INFO = {
    "identifiers": ["hewalex_pcwu"],
    "name": "Hewalex PCWU",
    "manufacturer": "Hewalex",
    "model": "PCWU Heat Pump",
    "sw_version": "1.0.0",
}

# ──────────────────────────────────────────────────────────────────────
# HA entity definitions: sensors (read-only from status registers)
# ──────────────────────────────────────────────────────────────────────
# (key_in_parsed_data, ha_name, device_class, unit, icon, category)

SENSOR_ENTITIES = [
    ('T1',  'PCWU T1 Ambient',           'temperature', '°C', None, None),
    ('T2',  'PCWU T2 Tank Bottom',        'temperature', '°C', None, None),
    ('T3',  'PCWU T3 Tank Top',           'temperature', '°C', None, None),
    ('T4',  'PCWU T4 Boiler',             'temperature', '°C', None, 'diagnostic'),
    ('T5',  'PCWU T5 Void',               'temperature', '°C', None, 'diagnostic'),
    ('T6',  'PCWU T6 Water Inlet',        'temperature', '°C', None, None),
    ('T7',  'PCWU T7 Water Outlet',       'temperature', '°C', None, None),
    ('T8',  'PCWU T8 Evaporator',         'temperature', '°C', None, None),
    ('T9',  'PCWU T9 Before Compressor',  'temperature', '°C', None, 'diagnostic'),
    ('T10', 'PCWU T10 After Compressor',  'temperature', '°C', None, 'diagnostic'),
    ('EV1', 'PCWU Expansion Valve',       None, 'steps', 'mdi:valve', 'diagnostic'),
    ('WaitingStatus', 'PCWU Waiting Status', None, None, 'mdi:clock-outline', 'diagnostic'),
]

BINARY_SENSOR_ENTITIES = [
    ('FanON',             'PCWU Fan',              'running', 'mdi:fan',              None),
    ('CirculationPumpON', 'PCWU Circulation Pump', 'running', 'mdi:water-pump',       None),
    ('HeatPumpON',        'PCWU Heat Pump',        'running', 'mdi:heat-pump',        None),
    ('CompressorON',      'PCWU Compressor',       'running', 'mdi:air-conditioner',  None),
    ('HeaterEON',         'PCWU Electric Heater',   'heat',   'mdi:radiator',         None),
    ('IsManual',          'PCWU Manual Mode',       None,     'mdi:hand-back-left',   'diagnostic'),
]

# ──────────────────────────────────────────────────────────────────────
# HA entity definitions: controls (writable config registers)
# ──────────────────────────────────────────────────────────────────────
# (key, ha_name, entity_type, config_extras)

SWITCH_ENTITIES = [
    ('HeatPumpEnabled',     'PCWU Heat Pump Enable',          'mdi:heat-pump-outline'),
    ('HeaterEEnabled',      'PCWU Electric Heater Enable',    'mdi:radiator'),
    ('AntiFreezingEnabled', 'PCWU Anti-Freeze Protection',    'mdi:snowflake-alert'),
    ('ExtControllerHPOFF',  'PCWU External HP Deactivation',  'mdi:power-plug-off'),
]

NUMBER_ENTITIES = [
    # (key, ha_name, min, max, step, unit, device_class, icon)
    ('TapWaterTemp',      'PCWU Target Temperature',    10, 60, 0.5, '°C',  'temperature', 'mdi:thermometer'),
    ('TapWaterHysteresis','PCWU Start Hysteresis',       2, 10, 0.5, '°C',  None,          'mdi:thermometer-lines'),
    ('AmbientMinTemp',    'PCWU Min Ambient Temp',     -10, 10, 0.5, '°C',  'temperature', 'mdi:thermometer-low'),
    ('DefrostingInterval','PCWU Defrost Interval',      30, 90,   1, 'min', None,          'mdi:snowflake-melt'),
    ('DefrostingStartTemp','PCWU Defrost Start Temp', -30,  0, 0.5, '°C',  'temperature', 'mdi:snowflake'),
    ('DefrostingStopTemp', 'PCWU Defrost Stop Temp',    2, 30, 0.5, '°C',  'temperature', 'mdi:snowflake-off'),
    ('DefrostingMaxTime',  'PCWU Max Defrost Duration',  1, 12,   1, 'min', None,          'mdi:timer-outline'),
    ('CircPumpMinTemp',    'PCWU Circ Pump Min Temp',   20, 60, 0.5, '°C',  'temperature', 'mdi:water-pump'),
]

SELECT_ENTITIES = [
    # (key, ha_name, options_map, icon)
    ('TapWaterSensor',       'PCWU Controlling Sensor',
     {'T2 (Tank Bottom)': 0, 'T3 (Tank Top)': 1, 'T7 (Water Outlet)': 2},
     'mdi:thermometer-check'),
    ('WaterPumpOperationMode', 'PCWU Water Pump Mode',
     {'Continuous': 0, 'Synchronous': 1},
     'mdi:water-pump'),
    ('FanOperationMode',     'PCWU Fan Mode',
     {'Max': 0, 'Min': 1, 'Day/Night': 2},
     'mdi:fan'),
    ('CircPumpMode',         'PCWU Circulation Pump Mode',
     {'Intermittent': 0, 'Continuous': 1},
     'mdi:water-pump'),
    ('HeaterEPowerLimit',    'PCWU Heater Power Limit',
     {'Off': 0, 'Low': 1, 'Medium': 2, 'High': 3},
     'mdi:flash'),
]


class HewalexMQTT:
    """MQTT client for Hewalex PCWU with HA auto-discovery."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 topic_prefix: str, discovery_prefix: str,
                 on_command_callback=None):
        self.host = host
        self.port = port
        self.topic_prefix = topic_prefix
        self.discovery_prefix = discovery_prefix
        self.on_command = on_command_callback
        self.connected = False
        self._subscriptions = {}  # topic -> handler

        # Build reverse maps for selects
        self._select_reverse = {}
        for key, name, options_map, icon in SELECT_ENTITIES:
            self._select_reverse[key] = {v: k for k, v in options_map.items()}

        self.client = mqtt.Client(client_id="hewalex_pcwu", clean_session=True)
        if user:
            self.client.username_pw_set(user, password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # LWT (Last Will and Testament) for availability
        avail_topic = f"{topic_prefix}/status"
        self.client.will_set(avail_topic, "offline", qos=1, retain=True)

    def connect(self):
        """Connect to the MQTT broker."""
        logger.info("Connecting to MQTT broker %s:%d", self.host, self.port)
        try:
            self.client.connect(self.host, self.port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.error("MQTT connection failed: %s", e)
            raise

    def disconnect(self):
        """Disconnect cleanly."""
        avail_topic = f"{self.topic_prefix}/status"
        self.client.publish(avail_topic, "offline", qos=1, retain=True)
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to MQTT broker")
            self.connected = True
            # Publish online status
            avail_topic = f"{self.topic_prefix}/status"
            client.publish(avail_topic, "online", qos=1, retain=True)
            # Re-subscribe to command topics
            for topic in self._subscriptions:
                client.subscribe(topic, qos=1)
            # Publish discovery configs
            self._publish_discovery()
        else:
            logger.error("MQTT connection failed with code %d", rc)

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%d), will reconnect", rc)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode('utf-8', errors='replace')
        logger.debug("MQTT message: %s = %s", topic, payload)

        if topic in self._subscriptions:
            self._subscriptions[topic](payload)

    def _uid(self, key: str) -> str:
        """Generate a unique ID for an entity."""
        return f"hewalex_pcwu_{key.lower()}"

    def _publish_discovery(self):
        """Publish HA MQTT discovery configs for all entities."""
        avail_topic = f"{self.topic_prefix}/status"

        # ── Sensors ──
        for key, name, dev_class, unit, icon, category in SENSOR_ENTITIES:
            config = {
                "name": name,
                "unique_id": self._uid(key),
                "state_topic": f"{self.topic_prefix}/sensor/{key}",
                "availability_topic": avail_topic,
                "device": DEVICE_INFO,
            }
            if dev_class:
                config["device_class"] = dev_class
            if unit:
                config["unit_of_measurement"] = unit
            if icon:
                config["icon"] = icon
            if category:
                config["entity_category"] = category
            if dev_class == 'temperature':
                config["state_class"] = "measurement"
                config["suggested_display_precision"] = 1

            disc_topic = f"{self.discovery_prefix}/sensor/{self._uid(key)}/config"
            self.client.publish(disc_topic, json.dumps(config), qos=1, retain=True)

        # ── Binary Sensors ──
        for key, name, dev_class, icon, category in BINARY_SENSOR_ENTITIES:
            config = {
                "name": name,
                "unique_id": self._uid(key),
                "state_topic": f"{self.topic_prefix}/binary_sensor/{key}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "availability_topic": avail_topic,
                "device": DEVICE_INFO,
            }
            if dev_class:
                config["device_class"] = dev_class
            if icon:
                config["icon"] = icon
            if category:
                config["entity_category"] = category

            disc_topic = f"{self.discovery_prefix}/binary_sensor/{self._uid(key)}/config"
            self.client.publish(disc_topic, json.dumps(config), qos=1, retain=True)

        # ── Switches (boolean config controls) ──
        for key, name, icon in SWITCH_ENTITIES:
            cmd_topic = f"{self.topic_prefix}/switch/{key}/set"
            config = {
                "name": name,
                "unique_id": self._uid(key),
                "state_topic": f"{self.topic_prefix}/switch/{key}",
                "command_topic": cmd_topic,
                "payload_on": "ON",
                "payload_off": "OFF",
                "availability_topic": avail_topic,
                "device": DEVICE_INFO,
                "entity_category": "config",
            }
            if icon:
                config["icon"] = icon

            disc_topic = f"{self.discovery_prefix}/switch/{self._uid(key)}/config"
            self.client.publish(disc_topic, json.dumps(config), qos=1, retain=True)

            self._subscribe_command(cmd_topic, key, 'switch')

        # ── Numbers (numeric config controls) ──
        for key, name, mn, mx, step, unit, dev_class, icon in NUMBER_ENTITIES:
            cmd_topic = f"{self.topic_prefix}/number/{key}/set"
            config = {
                "name": name,
                "unique_id": self._uid(key),
                "state_topic": f"{self.topic_prefix}/number/{key}",
                "command_topic": cmd_topic,
                "min": mn,
                "max": mx,
                "step": step,
                "availability_topic": avail_topic,
                "device": DEVICE_INFO,
                "entity_category": "config",
            }
            if unit:
                config["unit_of_measurement"] = unit
            if dev_class:
                config["device_class"] = dev_class
            if icon:
                config["icon"] = icon

            disc_topic = f"{self.discovery_prefix}/number/{self._uid(key)}/config"
            self.client.publish(disc_topic, json.dumps(config), qos=1, retain=True)

            self._subscribe_command(cmd_topic, key, 'number')

        # ── Selects (enumeration config controls) ──
        for key, name, options_map, icon in SELECT_ENTITIES:
            cmd_topic = f"{self.topic_prefix}/select/{key}/set"
            config = {
                "name": name,
                "unique_id": self._uid(key),
                "state_topic": f"{self.topic_prefix}/select/{key}",
                "command_topic": cmd_topic,
                "options": list(options_map.keys()),
                "availability_topic": avail_topic,
                "device": DEVICE_INFO,
                "entity_category": "config",
            }
            if icon:
                config["icon"] = icon

            disc_topic = f"{self.discovery_prefix}/select/{self._uid(key)}/config"
            self.client.publish(disc_topic, json.dumps(config), qos=1, retain=True)

            self._subscribe_command(cmd_topic, key, 'select')

        logger.info("Published HA discovery configs for all entities")

    def _subscribe_command(self, topic: str, key: str, entity_type: str):
        """Subscribe to a command topic and register handler."""
        def handler(payload, _key=key, _type=entity_type):
            self._handle_command(_key, _type, payload)

        self._subscriptions[topic] = handler
        if self.connected:
            self.client.subscribe(topic, qos=1)

    def _handle_command(self, key: str, entity_type: str, payload: str):
        """Handle an incoming command from HA."""
        logger.info("Command received: %s [%s] = %s", key, entity_type, payload)

        if entity_type == 'switch':
            value = payload.upper() in ('ON', 'TRUE', '1')
            if self.on_command:
                self.on_command(key, value)

        elif entity_type == 'number':
            try:
                value = float(payload)
                if self.on_command:
                    self.on_command(key, value)
            except ValueError:
                logger.error("Invalid number value for %s: %s", key, payload)

        elif entity_type == 'select':
            # Find the matching option and get its raw value
            for sel_key, sel_name, options_map, _ in SELECT_ENTITIES:
                if sel_key == key:
                    if payload in options_map:
                        raw_value = options_map[payload]
                        if self.on_command:
                            self.on_command(key, raw_value)
                    else:
                        logger.error("Invalid select option for %s: %s", key, payload)
                    break

    def publish_status(self, parsed_status: dict):
        """Publish parsed status register values to MQTT."""
        if not self.connected:
            return

        # Sensors
        for key, name, dev_class, unit, icon, category in SENSOR_ENTITIES:
            if key in parsed_status:
                val = parsed_status[key]
                if isinstance(val, float):
                    val = round(val, 1)
                topic = f"{self.topic_prefix}/sensor/{key}"
                self.client.publish(topic, str(val), qos=0, retain=True)

        # Binary sensors
        for key, name, dev_class, icon, category in BINARY_SENSOR_ENTITIES:
            if key in parsed_status:
                val = "ON" if parsed_status[key] else "OFF"
                topic = f"{self.topic_prefix}/binary_sensor/{key}"
                self.client.publish(topic, val, qos=0, retain=True)

    def publish_config(self, parsed_config: dict):
        """Publish parsed config register values to MQTT state topics."""
        if not self.connected:
            return

        # Switches
        for key, name, icon in SWITCH_ENTITIES:
            if key in parsed_config:
                val = "ON" if parsed_config[key] else "OFF"
                topic = f"{self.topic_prefix}/switch/{key}"
                self.client.publish(topic, val, qos=0, retain=True)

        # Numbers
        for key, name, mn, mx, step, unit, dev_class, icon in NUMBER_ENTITIES:
            if key in parsed_config:
                val = parsed_config[key]
                if isinstance(val, float):
                    val = round(val, 1)
                topic = f"{self.topic_prefix}/number/{key}"
                self.client.publish(topic, str(val), qos=0, retain=True)

        # Selects
        for key, name, options_map, icon in SELECT_ENTITIES:
            if key in parsed_config:
                raw_val = parsed_config[key]
                reverse = self._select_reverse.get(key, {})
                display_val = reverse.get(raw_val, str(raw_val))
                topic = f"{self.topic_prefix}/select/{key}"
                self.client.publish(topic, display_val, qos=0, retain=True)

    def publish_raw_registers(self, reg_type: str, base: int, values: list[int]):
        """Publish raw register values for debugging."""
        topic = f"{self.topic_prefix}/debug/{reg_type}"
        data = {str(base + i): v for i, v in enumerate(values)}
        self.client.publish(topic, json.dumps(data), qos=0, retain=False)
