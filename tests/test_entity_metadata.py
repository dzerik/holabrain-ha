"""Entity metadata contracts: categories, translated names and icon translations.

Every entity this integration creates is described by a declarative spec, and three parts
of that description are only ever seen by the user: the entity category (which decides
whether a control lands on the device page or in its configuration section), the
translation key (a typo shows up as a raw slug in the UI) and the icon.

None of that is exercised by the behavioural tests, so a wrong or missing value ships
silently. These tests read the registry the same way the platforms do.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import fields

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.holabrain.registry import CATEGORIES
from tests.conftest import DISHWASHER_CODE

COMPONENT = pathlib.Path("custom_components/holabrain")

# Spec tuple -> the entity section it is translated under.
_SPEC_PLATFORMS = {
    "sensors": "sensor",
    "binary_sensors": "binary_sensor",
    "switches": "switch",
    "selects": "select",
    "numbers": "number",
    "buttons": "button",
}


def _strings() -> dict:
    return json.loads((COMPONENT / "strings.json").read_text())


def _icons() -> dict:
    return json.loads((COMPONENT / "icons.json").read_text())


def _declared_keys() -> set[tuple[str, str]]:
    """Every ``(platform, translation_key)`` the declarative registry produces."""
    keys: set[tuple[str, str]] = set()
    for category in CATEGORIES.values():
        for spec_field in fields(category):
            platform = _SPEC_PLATFORMS.get(spec_field.name)
            if platform is None:
                continue
            for spec in getattr(category, spec_field.name):
                keys.add((platform, spec.translation_key))
    return keys


def test_every_declared_entity_has_a_translated_name() -> None:
    """A spec whose translation key is missing renders as an unreadable slug."""
    entity_strings = _strings()["entity"]
    missing = {
        (platform, key)
        for platform, key in _declared_keys()
        if key not in entity_strings.get(platform, {})
    }
    assert not missing


def test_icon_translations_only_name_entities_that_exist() -> None:
    """icons.json is not validated at runtime; a stale key is silently ignored forever."""
    entity_strings = _strings()["entity"]
    unknown = {
        (platform, key)
        for platform, icons in _icons()["entity"].items()
        for key in icons
        if key not in entity_strings.get(platform, {})
    }
    assert not unknown


def test_every_action_is_documented_and_has_an_icon() -> None:
    """An action without strings shows up in the UI as its raw slug."""
    import yaml

    actions = set(yaml.safe_load((COMPONENT / "services.yaml").read_text()))
    strings = _strings()
    assert actions <= set(strings["services"])
    assert actions <= set(_icons()["services"])
    for name in actions:
        described = strings["services"][name]
        assert described["name"] and described["description"]


async def test_lifetime_counters_are_diagnostic_and_settings_are_configuration(
    hass: HomeAssistant, setup_integration, entity_id_of
) -> None:
    """Categories decide where a control appears, so they are part of the UI contract.

    A lifetime water counter next to the door sensor, or a water-hardness setting among the
    controls a user touches daily, is the difference between a usable device page and a
    wall of values.
    """
    assert await setup_integration()
    registry = er.async_get(hass)

    total_water = entity_id_of("sensor", f"{DISHWASHER_CODE}_totalWaterVol")
    assert registry.async_get(total_water).entity_category is EntityCategory.DIAGNOSTIC

    softener = entity_id_of("number", f"{DISHWASHER_CODE}_softWaterGear")
    assert registry.async_get(softener).entity_category is EntityCategory.CONFIG

    # The controls a user actually operates must stay uncategorised, or they disappear from
    # the main device card.
    running = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")
    assert registry.async_get(running).entity_category is None
