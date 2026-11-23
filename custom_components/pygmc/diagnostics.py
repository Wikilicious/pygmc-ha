"""Diagnostics support for PyGMC."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import GMCConfigEntry

TO_REDACT: Final = {"serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GMCConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "device": asdict(coordinator.identity),
            "calibration": coordinator.calibration,
            "data": coordinator.data,
            "last_update_success": coordinator.last_update_success,
        },
        TO_REDACT,
    )
