"""The appliance-type catalogue: turning "0xCA" into something a person can read.

An appliance the integration does not model yet is otherwise reported as a bare token,
which is useless in a bug report and alarming in a repair issue. The cloud publishes the
answer as a tree of categories; the shapes below are taken from a real reply.
"""

from __future__ import annotations

from custom_components.holabrain.aiodollin.api.catalog import DeviceCatalog

# One category holding two sub-categories of the same type — the real tree does this,
# because an air conditioner is sold in several form factors.
TREE = {
    "categorys": [
        {
            "categoryId": "1",
            "name": "Air",
            "subCategorys": [
                {
                    "name": "Cassette/Duct/Ceiling&Floor",
                    "deviceTypes": ["0xAC"],
                    "models": [{"model": "20020AC1", "modelName": "Air Conditioner"}],
                },
                {
                    "name": "Split",
                    "deviceTypes": ["0xAC"],
                    "models": [
                        {"model": "20020AC1", "modelName": "Air Conditioner"},
                        {"model": "20020AC9", "modelName": "Air Conditioner"},
                    ],
                },
            ],
        },
        {
            "categoryId": "2",
            "name": "Kitchen",
            "subCategorys": [
                {
                    "name": "Refrigerator",
                    "deviceTypes": ["0xCA"],
                    "models": [{"model": "310A056C", "modelName": "Fridge"}],
                }
            ],
        },
    ]
}


def test_a_type_listed_twice_keeps_one_entry_and_both_models() -> None:
    """Sub-categories are form factors, not separate appliance types.

    Letting the second sub-category overwrite the first would drop half the models — and
    the models are how the per-model endpoints are addressed.
    """
    catalog = DeviceCatalog.from_tree(TREE)

    assert set(catalog.types) == {"0xAC", "0xCA"}
    assert catalog.types["0xAC"].models == ("20020AC1", "20020AC9")


def test_a_type_is_described_with_its_name_and_model() -> None:
    """This string ends up in a repair issue, so it has to read as a sentence."""
    catalog = DeviceCatalog.from_tree(TREE)

    assert catalog.describe("0xCA", "310A056C") == "Refrigerator (0xCA), model 310A056C"
    assert catalog.describe("0xCA") == "Refrigerator (0xCA)"


def test_an_unknown_type_still_produces_something_usable() -> None:
    """The catalogue is a nicety; it must never be the reason a user is told nothing.

    A type the cloud omits, an empty catalogue, or a fetch that failed — all three degrade
    to the raw code, which is still what an issue report needs.
    """
    catalog = DeviceCatalog.from_tree(TREE)

    assert catalog.describe("0xZZ") == "0xZZ"
    assert catalog.name_for("0xZZ") is None
    assert DeviceCatalog().describe("0xE1", "760EY179") == "0xE1, model 760EY179"


def test_a_malformed_tree_does_not_raise() -> None:
    """Undocumented endpoint: a shape change must degrade, not break setting up."""
    for payload in (None, {}, {"categorys": "nope"}, {"categorys": [None]}):
        assert DeviceCatalog.from_tree(payload).types == {}

    partial = {"categorys": [{"subCategorys": [{"name": "X", "deviceTypes": [], "models": []}]}]}
    assert DeviceCatalog.from_tree(partial).types == {}


def test_a_cached_catalogue_round_trips() -> None:
    """It is stored between restarts, so the two directions have to agree."""
    catalog = DeviceCatalog.from_tree(TREE)
    restored = DeviceCatalog.from_dict(catalog.to_dict())

    assert restored.describe("0xAC") == catalog.describe("0xAC")
    assert restored.types["0xAC"].models == catalog.types["0xAC"].models
