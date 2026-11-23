"""Base entity for PyGMC."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GMCCoordinator


class GMCEntity(CoordinatorEntity[GMCCoordinator]):
    """An entity of a GMC counter."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GMCCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        identity = coordinator.identity
        self._attr_unique_id = f"{identity.serial_number}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity.serial_number)},
            manufacturer=MANUFACTURER,
            model=identity.model,
            name=identity.model,
            serial_number=identity.serial_number,
            sw_version=identity.firmware or None,
        )
