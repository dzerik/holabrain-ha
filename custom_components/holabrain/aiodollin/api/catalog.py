"""The ecosystem's own catalogue of appliance types.

The integration knows an appliance's ``deviceType`` — a code like ``0xCA`` — because the
account tells it. What it cannot know from that alone is what the code *means*, which is
why an unsupported appliance used to be reported to the user as a bare token.

The cloud publishes the answer: a tree of categories, each holding sub-categories that name
a device type and list the models sold under it. Two things make it worth fetching:

* it turns "0xCA" into "Refrigerator" wherever the integration has to talk about an
  appliance it does not model yet — a repair issue, a diagnostics dump, an issue report; and
* it lists the models (``sn8``) per type, which is the key other endpoints are addressed by.

The catalogue is a property of the region rather than of the account, and it changes at the
pace of a product line-up, so the caller is expected to cache it for a day. There is a flat
``devicetype/list`` variant as well; it answers with a subset and is on its way out, so this
uses the tree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..auth.manager import AuthManager

EP_DEVICE_TYPE_TREE = "/v1/product/category/devicetype/tree"


@dataclass(frozen=True)
class CatalogType:
    """One appliance type as the ecosystem describes it."""

    device_type: str
    name: str
    #: Model codes (``sn8``) sold under this type. Also the key ``function/dict`` and the
    #: plugin endpoints are addressed by.
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeviceCatalog:
    """Every appliance type the region's cloud knows about."""

    types: Mapping[str, CatalogType] = field(default_factory=dict)

    def name_for(self, device_type: str) -> str | None:
        """A human-readable name for a type code, or ``None`` if the cloud omits it."""
        entry = self.types.get(device_type)
        return entry.name if entry is not None else None

    def describe(self, device_type: str, model: str | None = None) -> str:
        """The best label available for an appliance, always safe to show to a user.

        Falls back to the raw code, which is still more use in a bug report than nothing.
        """
        name = self.name_for(device_type)
        head = f"{name} ({device_type})" if name else device_type
        return f"{head}, model {model}" if model else head

    def to_dict(self) -> dict[str, Any]:
        return {
            code: {"name": entry.name, "models": list(entry.models)}
            for code, entry in sorted(self.types.items())
        }

    @classmethod
    def from_dict(cls, payload: Any) -> DeviceCatalog:
        """Rebuild a cached catalogue."""
        if not isinstance(payload, dict):
            return cls()
        types = {
            str(code): CatalogType(
                device_type=str(code),
                name=str((entry or {}).get("name", "")),
                models=tuple(str(m) for m in (entry or {}).get("models", []) if m),
            )
            for code, entry in payload.items()
            if isinstance(entry, dict)
        }
        return cls(types=types)

    @classmethod
    def from_tree(cls, payload: Any) -> DeviceCatalog:
        """Flatten the cloud's category tree into ``device_type -> description``.

        A type can appear under more than one sub-category (an air conditioner is sold as
        several form factors), so names and model lists are merged rather than overwritten:
        whichever sub-category is read first must not decide what the type is called.
        """
        names: dict[str, str] = {}
        models: dict[str, list[str]] = {}
        for category in _as_list(_get(payload, "categorys")):
            for sub in _as_list(_get(category, "subCategorys")):
                codes = [str(code) for code in _as_list(_get(sub, "deviceTypes")) if code]
                if not codes:
                    continue
                label = str(_get(sub, "name") or "")
                for code in codes:
                    if label and code not in names:
                        names[code] = label
                    bucket = models.setdefault(code, [])
                    for entry in _as_list(_get(sub, "models")):
                        model = _get(entry, "model")
                        # A model can be listed under several sub-categories of one type.
                        if model and str(model) not in bucket:
                            bucket.append(str(model))
                        # The per-model name is more specific than the sub-category's when
                        # the sub-category is a form factor rather than an appliance kind.
                        model_name = _get(entry, "modelName")
                        if model_name and code not in names:
                            names[code] = str(model_name)
        return cls(
            types={
                code: CatalogType(code, names.get(code, ""), tuple(models.get(code, ())))
                for code in {*names, *models}
            }
        )


def _get(source: Any, key: str) -> Any:
    return source.get(key) if isinstance(source, dict) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class CatalogApi:
    """Reads the ecosystem's appliance-type catalogue."""

    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    async def async_tree(self) -> DeviceCatalog:
        """Fetch the full catalogue for the client's region."""
        response = await self._auth.oem(EP_DEVICE_TYPE_TREE, {})
        return DeviceCatalog.from_tree(response.get("data"))
