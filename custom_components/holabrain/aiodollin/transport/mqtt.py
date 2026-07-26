"""Cloud-push client over AWS IoT MQTT (mutual TLS).

The device publishes JSON status frames to ``<region>/<region>_<thing_code>/dev``. This thin
wrapper runs paho's network loop on its own thread and hands decoded ``(topic, payload)``
pairs to a caller-supplied callback. That callback runs on paho's thread, so a Home
Assistant caller must marshal it back onto the event loop.

paho is imported lazily so importing aiodollin never hard-requires it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

MessageCallback = Callable[[str, dict[str, Any]], None]
StateCallback = Callable[[Any], None]


class MqttClient:
    """A reconnecting AWS IoT MQTT subscriber with mutual-TLS auth."""

    def __init__(
        self,
        *,
        endpoint: str,
        port: int,
        client_id: str,
        ssl_context: Any,
        on_message: MessageCallback,
        on_connect: StateCallback | None = None,
        on_disconnect: StateCallback | None = None,
    ) -> None:
        import paho.mqtt.client as mqtt

        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._endpoint = endpoint
        self._port = port
        self._subscriptions: list[str] = []

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        self._client.tls_set_context(ssl_context)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_message = self._handle_message
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect

    def connect(self) -> None:
        """Start the background network loop and connect asynchronously."""
        self._client.connect_async(self._endpoint, self._port, keepalive=60)
        self._client.loop_start()

    def subscribe(self, topics: str | Iterable[str]) -> None:
        """Subscribe to one or more topics; remembered for re-subscribe on reconnect."""
        if isinstance(topics, str):
            topics = [topics]
        for topic in topics:
            if topic not in self._subscriptions:
                self._subscriptions.append(topic)
            self._client.subscribe(topic, qos=0)

    def disconnect(self) -> None:
        """Say goodbye to the broker, then stop the network loop.

        The order matters: ``disconnect()`` only queues a DISCONNECT packet, and it is the
        network loop that puts it on the wire. Stopping the loop first would drop the packet
        and leave the broker holding the session open until it times out — which, with a
        client id derived from the config entry, blocks the *next* connection after a reload.
        """
        self._client.disconnect()
        self._client.loop_stop()

    # -- paho callbacks (run on paho's network thread) ---------------------------------
    def _handle_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # Keep the pipeline alive on a malformed frame instead of crashing the loop.
            payload = {"_raw": message.payload[:256].hex()}
        if not isinstance(payload, dict):
            payload = {"_value": payload}
        self._on_message(message.topic, payload)

    def _handle_connect(
        self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any = None
    ) -> None:
        # Re-subscribe on every (re)connect — subscriptions do not survive a broker drop.
        for topic in self._subscriptions:
            client.subscribe(topic, qos=0)
        if self._on_connect is not None:
            self._on_connect(reason_code)

    def _handle_disconnect(
        self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any = None
    ) -> None:
        if self._on_disconnect is not None:
            self._on_disconnect(reason_code)
