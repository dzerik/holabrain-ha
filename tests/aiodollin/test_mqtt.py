"""Push transport: the parts that only misbehave against a real broker.

``MqttClient`` is a thin wrapper, but the three things it does wrong are expensive: dropping
the goodbye packet (the broker then keeps the session and refuses the next connection with
the same client id), forgetting subscriptions across a reconnect (silence that looks exactly
like an idle appliance), and letting one malformed frame kill the network thread.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.holabrain.aiodollin.transport.mqtt import MqttClient


class FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


@pytest.fixture
def paho_client():
    """Replace paho's client class so no socket is opened."""
    client = MagicMock(name="paho_client")
    with patch("paho.mqtt.client.Client", return_value=client):
        yield client


def _build(paho_client, received: list[tuple[str, dict[str, Any]]]) -> MqttClient:
    return MqttClient(
        endpoint="broker.invalid",
        port=8883,
        client_id="holabrain-test",
        ssl_context=MagicMock(name="ssl"),
        on_message=lambda topic, payload: received.append((topic, payload)),
    )


def test_the_broker_is_told_goodbye_before_the_loop_is_stopped(paho_client) -> None:
    """``disconnect()`` only queues the packet — the network loop is what sends it.

    Stopping the loop first drops it, so the broker holds the session open until it times
    out. With a client id derived from the config entry that blocks the reconnect after a
    reload for minutes.
    """
    client = _build(paho_client, [])
    client.connect()

    client.disconnect()

    names = [call[0] for call in paho_client.method_calls]
    assert names.index("disconnect") < names.index("loop_stop")


def test_subscriptions_are_restored_after_the_broker_drops_us(paho_client) -> None:
    """A reconnect that does not re-subscribe is worse than no push at all: the channel
    looks healthy, so polling stays disabled, and no status ever arrives again."""
    client = _build(paho_client, [])
    client.subscribe(["eu/eu_1/dev", "eu/eu_1/hbt"])
    paho_client.reset_mock()

    # paho signals a fresh session by calling on_connect again.
    client._handle_connect(paho_client, None, None, 0, None)

    subscribed = {call.args[0] for call in paho_client.subscribe.call_args_list}
    assert subscribed == {"eu/eu_1/dev", "eu/eu_1/hbt"}


def test_a_frame_that_is_not_json_does_not_take_the_channel_down(paho_client) -> None:
    """One appliance sending garbage must not stop the others being updated."""
    received: list[tuple[str, dict[str, Any]]] = []
    client = _build(paho_client, received)

    client._handle_message(paho_client, None, FakeMessage("eu/eu_1/dev", b"\xff\xfe not json"))
    client._handle_message(paho_client, None, FakeMessage("eu/eu_1/dev", b'{"power":"1"}'))

    assert len(received) == 2
    assert received[1] == ("eu/eu_1/dev", {"power": "1"})


def test_a_json_frame_that_is_not_an_object_is_wrapped_rather_than_dropped(
    paho_client,
) -> None:
    """The callback contract is a dict; a bare scalar must not reach it as a list or int."""
    received: list[tuple[str, dict[str, Any]]] = []
    client = _build(paho_client, received)

    client._handle_message(paho_client, None, FakeMessage("eu/eu_1/hbt", b"[1, 2]"))

    assert received == [("eu/eu_1/hbt", {"_value": [1, 2]})]
