"""
End to end: add-on (main.py) <-> simulated PCWU over TCP <-> Mosquitto <-> "Home Assistant".

Needs a `mosquitto` binary on PATH; skipped otherwise.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time

import paho.mqtt.client as mqtt
import pytest

from pcwu_simulator import SimulatorServer

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rootfs", "app", "main.py")

pytestmark = pytest.mark.skipif(shutil.which("mosquitto") is None, reason="mosquitto not installed")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def broker(tmp_path):
    port = free_port()
    conf = tmp_path / "mosquitto.conf"
    conf.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
    proc = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield port
    proc.terminate()
    proc.wait(5)


class HomeAssistant:
    """Minimal MQTT client recording what HA would see."""

    def __init__(self, port):
        self.messages = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self._on_message
        self.client.connect("127.0.0.1", port)
        self.client.subscribe("homeassistant/#")
        self.client.subscribe("hewalex/#")
        self.client.loop_start()

    def _on_message(self, client, userdata, msg):
        self.messages[msg.topic] = msg.payload.decode()

    def wait(self, topic, expected=None, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.messages.get(topic)
            if value is not None and (expected is None or value == expected):
                return value
            time.sleep(0.05)
        raise AssertionError(f"{topic}: expected {expected!r}, last value {self.messages.get(topic)!r}")

    def command(self, key, payload):
        self.client.publish(f"hewalex/{key}/set", payload, qos=1)

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()


def start_addon(broker_port, sim_port, **extra):
    env = dict(os.environ, DEVICE_ADDRESS="127.0.0.1", DEVICE_PORT=str(sim_port), MQTT_HOST="127.0.0.1",
               MQTT_PORT=str(broker_port), POLL_INTERVAL="5", LOG_LEVEL="debug", ADDON_VERSION="test", **extra)
    return subprocess.Popen([sys.executable, "-u", APP], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)


def stop_addon(proc):
    proc.terminate()
    out, _ = proc.communicate(timeout=10)
    return out


def test_direct_mode(broker):
    sim = SimulatorServer().start()
    ha = HomeAssistant(broker)
    # Retained discovery of add-on 1.0.x that must be cleaned up
    ha.client.publish("homeassistant/binary_sensor/hewalex_pcwu_circulationpumpon/config", "{}", retain=True)
    addon = start_addon(broker, sim.port)
    try:
        disc = json.loads(ha.wait("homeassistant/number/hewalex_pcwu_tapwatertemp/config"))
        assert disc["command_topic"] == "hewalex/TapWaterTemp/set"
        assert (disc["min"], disc["max"], disc["step"]) == (10, 60, 1)
        assert disc["device"]["identifiers"] == ["hewalex_pcwu"]
        assert json.loads(ha.wait("homeassistant/sensor/hewalex_pcwu_t1/config"))["device_class"] == "temperature"
        assert json.loads(ha.wait("homeassistant/select/hewalex_pcwu_fanoperationmode/config"))["options"] == \
            ["Max", "Min", "Day/Night"]
        ha.wait("homeassistant/binary_sensor/hewalex_pcwu_circulationpumpon/config", "")
        assert "homeassistant/switch/hewalex_pcwu_extcontrollerhpoff/config" not in ha.messages or \
            ha.messages["homeassistant/switch/hewalex_pcwu_extcontrollerhpoff/config"] == ""

        ha.wait("hewalex/availability", "online")
        ha.wait("hewalex/T1", "8.5")
        ha.wait("hewalex/T4", "-50.0")
        ha.wait("hewalex/CompressorON", "ON")
        ha.wait("hewalex/OperatingState", "Heating")
        ha.wait("hewalex/TapWaterTemp", "50.0")
        ha.wait("hewalex/TimeProgramHPM-F", "06:00-22:00")
        assert ha.messages.get("homeassistant/number/hewalex_pcwu_defrostingstarttemp/config", "") == ""

        # Home Assistant restart (birth message): discovery and states are sent again
        ha.messages.clear()
        ha.client.publish("homeassistant/status", "online")
        ha.wait("homeassistant/sensor/hewalex_pcwu_t1/config")
        ha.wait("hewalex/T1", "8.5")

        # number: written, acknowledged, read back
        ha.command("TapWaterTemp", "48")
        ha.wait("hewalex/TapWaterTemp", "48.0")
        assert sim.pcwu.word(308) == 480

        # out of range: refused by the add-on, nothing sent to the PCWU
        writes = len(sim.pcwu.writes)
        ha.command("TapWaterTemp", "75")
        time.sleep(1)
        assert sim.pcwu.word(308) == 480 and len(sim.pcwu.writes) == writes

        # switch: disabling the heat pump changes the operating state
        ha.command("HeatPumpEnabled", "OFF")
        ha.wait("hewalex/HeatPumpEnabled", "OFF")
        ha.wait("hewalex/OperatingState", "Waiting: disabled")
        assert sim.pcwu.word(304) == 0
        ha.command("HeatPumpEnabled", "ON")
        ha.wait("hewalex/HeatPumpEnabled", "ON")
        ha.wait("hewalex/OperatingState", "Heating")

        # select
        ha.command("FanOperationMode", "Day/Night")
        ha.wait("hewalex/FanOperationMode", "Day/Night")
        assert sim.pcwu.word(330) == 2

        # the PCWU acknowledges but keeps its old value: HA shows the real value
        sim.pcwu.ignore_writes.add(366)
        ha.wait("hewalex/HeaterEHPONTemp", "45.0")
        ha.command("HeaterEHPONTemp", "35")
        time.sleep(1.5)
        assert ha.messages["hewalex/HeaterEHPONTemp"] == "45.0"

        # every write went to the right register with a 2 byte payload
        assert [start for start, _ in sim.pcwu.writes] == [308, 304, 304, 330, 366]
        assert all(len(data) == 2 for _, data in sim.pcwu.writes)
    finally:
        out = stop_addon(addon)
        ha.stop()
        sim.shutdown()
        sim.server_close()
    assert "Traceback" not in out, out
    assert "PCWU did not accept HeaterEHPONTemp" in out
    assert "Rejected TapWaterTemp = '75'" in out


def test_direct_mode_device_unreachable(broker):
    ha = HomeAssistant(broker)
    addon = start_addon(broker, free_port())
    try:
        ha.wait("homeassistant/sensor/hewalex_pcwu_t1/config")
        time.sleep(2)
        assert ha.messages.get("hewalex/availability", "offline") == "offline"
        assert "hewalex/T1" not in ha.messages
    finally:
        out = stop_addon(addon)
        ha.stop()
    assert "Traceback" not in out, out


def test_eavesdrop_mode(broker):
    sim = SimulatorServer(eavesdrop=True).start()
    ha = HomeAssistant(broker)
    addon = start_addon(broker, sim.port, MODE="eavesdrop", EXPERT_MODE="true")
    try:
        ha.wait("homeassistant/sensor/hewalex_pcwu_t1/config")
        ha.wait("hewalex/availability", "online")
        ha.wait("hewalex/T1", "8.5")
        ha.wait("hewalex/T7", "45.7")
        ha.wait("hewalex/OperatingState", "Heating")
        # no controls in read-only mode: their discovery is removed
        ha.wait("homeassistant/number/hewalex_pcwu_tapwatertemp/config", "")
        ha.wait("homeassistant/switch/hewalex_pcwu_heatpumpenabled/config", "")
        ha.command("HeatPumpEnabled", "OFF")
        time.sleep(1)
        assert sim.pcwu.writes == []
    finally:
        out = stop_addon(addon)
        ha.stop()
        sim.shutdown()
        sim.server_close()
    assert "Traceback" not in out, out
