"""DeviceState parsing and push-merge tests.

Push frames are partial and oddly shaped; these tests pin the merge semantics that the
coordinator relies on, including the identity optimization and immutability.
"""

from custom_components.holabrain.aiodollin.dto.state import DeviceState


def test_from_query_flat_body():
    state = DeviceState.from_query("t1", {"power": "3", "runState": "1"})
    assert state.get("power") == "3"
    assert state.online is True


def test_from_query_nested_under_query_key():
    state = DeviceState.from_query("t1", {"query": {"power": "1"}})
    assert state.get("power") == "1"


def test_apply_push_online_change():
    state = DeviceState.from_query("t1", {"power": "1"})
    offline = state.apply_push({"onlineChange": {"online": 0}})
    assert offline.online is False
    # The attribute payload is untouched by a pure online change.
    assert offline.get("power") == "1"


def test_apply_push_status_delta_is_flattened():
    state = DeviceState.from_query("t1", {"power": "1", "runState": "2"})
    updated = state.apply_push({"status": {"runState": "1"}})
    assert updated.get("runState") == "1"
    assert updated.get("power") == "1"  # untouched keys survive


def test_apply_push_scalar_frame():
    state = DeviceState.from_query("t1", {"power": "1"})
    updated = state.apply_push({"remainTimeL": "42"})
    assert updated.get("remainTimeL") == "42"


def test_apply_push_empty_frame_returns_same_instance():
    # No-op frames are common (heartbeats); avoid churning coordinator state.
    state = DeviceState.from_query("t1", {"power": "1"})
    assert state.apply_push({}) is state


def test_apply_push_does_not_mutate_original():
    state = DeviceState.from_query("t1", {"power": "1"})
    state.apply_push({"power": "0"})
    assert state.get("power") == "1"  # original unchanged (immutability)


def test_online_string_zero_is_offline():
    state = DeviceState.from_query("t1", {"power": "1", "online": "0"})
    assert state.online is False
