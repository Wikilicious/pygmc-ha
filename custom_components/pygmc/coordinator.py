"""Data update coordinator for PyGMC."""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any, Final, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_BAUDRATE, DOMAIN, LOGGER, SCAN_INTERVAL, TROUBLESHOOTING_URL
from .gmc import (
    BrlttyError,
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
REPAIRABLE_ERRORS: Final = (BrlttyError, DeviceMismatchError, PortPermissionError)


@callback
def async_delete_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the repair issues raised for a config entry."""
    for error in REPAIRABLE_ERRORS:
        ir.async_delete_issue(hass, DOMAIN, f"{error.translation_key}_{entry.entry_id}")


class GMCCoordinator(DataUpdateCoordinator[GMCData]):
    """Own the serial connection to one GMC counter and poll it."""

    config_entry: GMCConfigEntry
    # Set when the first connection succeeds, before any entity is created.
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
        # A scheduled refresh can overlap one requested by homeassistant.update_entity.
        self._lock = threading.Lock()
        self._issue: str | UndefinedType | None = UNDEFINED

    @override
    async def _async_setup(self) -> None:
        """Connect before the first refresh, so the counter's identity is known."""
        await self._async_run(self._connect)

    @override
    async def _async_update_data(self) -> GMCData:
        return await self._async_run(self._poll)

    @override
    async def async_shutdown(self) -> None:
        """Close the serial port when the entry unloads."""
        await super().async_shutdown()
        await self.hass.async_add_executor_job(self._disconnect)

    async def _async_run[T](self, job: Callable[[], T]) -> T:
        """Run a blocking job, translating failures for the user."""
        try:
            result = await self.hass.async_add_executor_job(job)
        except GMCError as err:
            self._async_update_issue(err)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key=err.translation_key,
                translation_placeholders=err.translation_placeholders,
            ) from err
        self._async_update_issue(None)
        self._async_store_baudrate()
        return result

    def _connect(self) -> None:
        with self._lock:
            self._open()

    def _poll(self) -> GMCData:
        with self._lock:
            device = self._open()
            try:
                return device.read()
            except GMCError:
                self._close()
                raise

    def _disconnect(self) -> None:
        with self._lock:
            self._close()

    def _open(self) -> GMCDevice:
        """Return the open device, connecting first if needed. Hold the lock."""
        if self._device is None:
            data = self.config_entry.data
            device = open_device(
                data[CONF_PORT], data[CONF_BAUDRATE], self.config_entry.unique_id
            )
            self._device = device
            self.identity = device.identity
            self.calibration = device.calibration
        return self._device

    def _close(self) -> None:
        """Close the device if it is open. Hold the lock."""
        device, self._device = self._device, None
        if device is not None:
            device.close()

    @callback
    def _async_update_issue(self, err: GMCError | None) -> None:
        """Raise a repair issue for a failure the user must fix, or clear it."""
        key = err.translation_key if isinstance(err, REPAIRABLE_ERRORS) else None
        if key == self._issue:
            return
        entry = self.config_entry
        # Until the first result, issues may linger from before a reload.
        for error in REPAIRABLE_ERRORS:
            if error.translation_key != key:
                ir.async_delete_issue(
                    self.hass, DOMAIN, f"{error.translation_key}_{entry.entry_id}"
                )
        if err is not None and key is not None:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{key}_{entry.entry_id}",
                is_fixable=False,
                learn_more_url=TROUBLESHOOTING_URL,
                severity=ir.IssueSeverity.ERROR,
                translation_key=key,
                translation_placeholders={
                    "title": entry.title,
                    **err.translation_placeholders,
                },
            )
        self._issue = key

    @callback
    def _async_store_baudrate(self) -> None:
        """Remember the baud rate the counter answered at, if it changed."""
        entry = self.config_entry
        if self.identity.baudrate != entry.data[CONF_BAUDRATE]:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_BAUDRATE: self.identity.baudrate}
            )
