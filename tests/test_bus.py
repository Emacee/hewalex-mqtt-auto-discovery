import pytest

import pcwu
from bus import GecoClient, GecoDeviceError, GecoTimeout, Transport
from pcwu_simulator import SimulatorServer


@pytest.fixture
def sim():
    server = SimulatorServer().start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(sim):
    transport = Transport("127.0.0.1", sim.port)
    transport.open()
    yield GecoClient(transport, 1, 1, 2, 2, timeout=1.0, retries=1)
    transport.close()


def test_read_status_and_config_blocks(sim, client):
    status = client.read(*pcwu.STATUS_BLOCK)
    config = client.read(*pcwu.CONFIG_BLOCK)  # 236 bytes -> two requests (226 + 10)
    assert status == sim.pcwu.read(*pcwu.STATUS_BLOCK)
    assert config == sim.pcwu.read(*pcwu.CONFIG_BLOCK)
    image = pcwu.RegisterImage()
    image.update(pcwu.STATUS_START, status)
    image.update(pcwu.CONFIG_START, config)
    assert pcwu.decode(pcwu.POINTS_BY_KEY["T1"], image) == 8.5
    assert pcwu.decode(pcwu.POINTS_BY_KEY["T4"], image) == -50.0
    assert pcwu.decode(pcwu.POINTS_BY_KEY["TapWaterTemp"], image) == 50.0
    assert pcwu.decode(pcwu.POINTS_BY_KEY["TimeProgramHPM-F"], image) == "06:00-22:00"


def test_write_is_acknowledged_and_stored(sim, client):
    client.write_u16(308, 480)
    assert sim.pcwu.word(308) == 480
    assert client.read(308, 2) == b"\xe0\x01"
    assert sim.pcwu.writes[-1] == (308, b"\xe0\x01")


def test_device_error_frame(client):
    with pytest.raises(GecoDeviceError):
        client.read(0, 100)


def test_no_answer_for_other_address(sim):
    transport = Transport("127.0.0.1", sim.port)
    transport.open()
    try:
        with pytest.raises(GecoTimeout):
            GecoClient(transport, 1, 1, 5, 6, timeout=0.3, retries=0).read(120, 10)
    finally:
        transport.close()


def test_retry_after_lost_response_and_noise(sim, client):
    sim.drop_next = 1
    sim.noise = True
    assert client.read(128, 2) == b"\x55\x00"
