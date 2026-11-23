"""Data update coordinator for PyGMC."""

from __future__ import annotations

import threading
from typing import Any, Final, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_BAUDRATE, DOMAIN, LOGGER, SCAN_INTERVAL, TROUBLESHOOTING_URL
from .gmc import (
    DeviceMismatchError,
    GMCDevice,
    GMCError,
    GMCIdentity,
    PortPermissionError,
    open_device,
)

type GMCConfigEntry = ConfigEntry[GMCCoordinator]
type GMCData = dict[str, float | None]

# Failures only the user can fix get a repair issue besides unavailable entities.
REPAIRABLE_ERRORS: Final = (DeviceMismatchError, PortPermissionError)


@callback
def async_update_issues(
    hass: HomeAssistant, entry: ConfigEntry, err: GMCError | None = None
) -> None:
    """Raise the repair issue for a failure the user must fix; delete the others."""
    for error in REPAIRABLE_ERRORS:
        issue_id = f"{error.translation_key}_{entry.entry_id}"
        if isinstance(err, error):
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                learn_more_url=TROUBLESHOOTING_URL,
                severity=ir.IssueSeverity.ERROR,
                translation_key=error.translation_key,
                translation_placeholders={
                    "title": entry.title,
                    **err.translation_placeholders,
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


class GMCCoordinator(DataUpdateCoordinator[GMCData]):
    """Own the serial connection to one GMC counter and poll it."""

    config_entry: GMCConfigEntry
    # Set by the first refresh, before any entity is created.
    identity: GMCIdentity
    calibration: dict[str, Any]

    def __init__(self, hass: HomeAssistant, entry: GMCConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self._device: GMCDevice | None = None
        # A scheduled refresh can overlap one requested by homeassistant.update_entity,
        # and unloading can overlap a refresh that is still connecting.
        self._lock = threading.Lock()

    @override
    async def _async_update_data(self) -> GMCData:
        entry = self.config_entry
        try:
            data = await self.hass.async_add_executor_job(self._poll)
        except GMCError as err:
            async_update_issues(self.hass, entry, err)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key=err.translation_key,
                translation_placeholders=err.translation_placeholders,
            ) from err
        async_update_issues(self.hass, entry)
        if self.identity.baudrate != entry.data[CONF_BAUDRATE]:
            # Try the counter's new baud rate first when reconnecting.
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_BAUDRATE: self.identity.baudrate}
            )
        return data

    @override
    async def async_shutdown(self) -> None:
        """Close the serial port when the entry unloads."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self._close)

    def _poll(self) -> GMCData:
        with self._lock:
            if self._device is None:
                entry = self.config_entry
                self._device = open_device(
                    entry.data[CONF_PORT], entry.data[CONF_BAUDRATE], entry.unique_id
                )
                self.identity = self._device.identity
                self.calibration = self._device.calibration
            try:
                return self._device.read()
            except GMCError:
                # Reconnect on the next refresh.
                self._device.close()
                self._device = None
                raise

    def _close(self) -> None:
        with self._lock:
            if self._device is not None:
                self._device.close()
                self._device = None
