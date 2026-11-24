"""Device-registry helpers compatible across Home Assistant versions.

Home Assistant 2026.8 made device identifiers and connections unique per config
entry and deprecated several device-registry APIs:

* ``DeviceInfo(via_device=...)`` -> ``via_device_id``
* ``DeviceRegistry.async_get_device(identifiers=...)`` -> scoped lookups
* ``DeviceRegistry.devices`` used as a mapping -> iterate / ``async_get()``

These helpers use the new APIs when they are available and transparently fall
back to the deprecated ones on older Home Assistant releases, so the
integration keeps working on both.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

# ``async_get_device_by_identifier`` was added in Home Assistant 2026.8. When
# it is present we are on a version where identifiers are unique per config
# entry and the via-device must be referenced by id.
_SCOPED_LOOKUP_ATTR = "async_get_device_by_identifier"


def async_get_device(
    hass: HomeAssistant,
    identifier: tuple[str, str],
    entry_id: str,
) -> dr.DeviceEntry | None:
    """Look up a device by an identifier scoped to a config entry."""
    registry = dr.async_get(hass)
    scoped_lookup = getattr(registry, _SCOPED_LOOKUP_ATTR, None)
    if scoped_lookup is not None:
        return scoped_lookup(identifier, entry_id)

    # Home Assistant < 2026.8 fallback (global lookup, deprecated on newer
    # versions). Only used when the scoped lookup is unavailable.
    return registry.async_get_device(identifiers={identifier})


def async_via_device_kwargs(
    hass: HomeAssistant,
    identifier: tuple[str, str],
    entry_id: str,
) -> dict[str, object]:
    """Return the kwargs linking a device to its via device.

    On Home Assistant 2026.8+ this returns ``{"via_device_id": ...}`` (empty
    when the via device does not exist yet). On older releases it falls back to
    the legacy ``{"via_device": identifier}`` form.
    """
    registry = dr.async_get(hass)
    if not hasattr(registry, _SCOPED_LOOKUP_ATTR):
        return {"via_device": identifier}

    device = async_get_device(hass, identifier, entry_id)
    return {"via_device_id": device.id} if device else {}
