"""Diagnostics support for PyGMC."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import GMCConfigEntry
from .gmc import PYGMC_VERSION

TO_REDACT: Final = {"serial_number", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GMCConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
            },
            "device": asdict(coordinator.identity),
            "calibration": coordinator.calibration,
            "data": coordinator.data,
            "last_update_success": coordinator.last_update_success,
            "pygmc_version": PYGMC_VERSION,
        },
        TO_REDACT,
    )
