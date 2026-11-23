"""The PyGMC integration - GQ GMC Geiger counters over USB serial."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import GMCConfigEntry, GMCCoordinator, async_update_issues

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GMCConfigEntry) -> bool:
    """Set up a GMC counter from a config entry."""
    coordinator = GMCCoordinator(hass, entry)
    # First refresh connects & sets coordinator.identity - sensors need it.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GMCConfigEntry) -> bool:
    """Unload a config entry."""
    async_update_issues(hass, entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GMCConfigEntry) -> None:
    """Delete repair issues of an entry removed while it couldn't load."""
    # Unload never ran for an entry that failed to load... clean up here too.
    async_update_issues(hass, entry)
