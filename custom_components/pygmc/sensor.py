"""Sensor platform for PyGMC."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GMCConfigEntry, GMCCoordinator

if TYPE_CHECKING:
    # Added in Home Assistant 2025.3. Annotations are never evaluated at runtime so
    # older releases don't trip over the import.
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# The coordinator does all the serial I/O. Entities just read coordinator.data.
PARALLEL_UPDATES = 0

SENSORS: Final = (
    SensorEntityDescription(
        key="cpm",
        translation_key="cpm",
        native_unit_of_measurement="CPM",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="dose_rate",
        translation_key="dose_rate",
        # Greek small letter mu (U+03BC) - what Home Assistant recommends - NOT the
        # micro sign (U+00B5). They look identical. Don't "fix" it.
        native_unit_of_measurement="μSv/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    SensorEntityDescription(
        # A one second count sampled once a minute is mostly noise. Off by default.
        key="cps",
        translation_key="cps",
        native_unit_of_measurement="CPS",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        # Temperamental (pun intended). See pygmc DeviceRFC1201.get_temp() notes:
        # GMC-300S returned 0, then 86, then settled at room temperature.
        # Off by default.
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GMCConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a sensor for each reading the counter supports."""
    coordinator = entry.runtime_data
    # coordinator.data only has keys the device supports (see gmc.OPTIONAL_READINGS)
    async_add_entities(
        GMCSensor(coordinator, description)
        for description in SENSORS
        if description.key in coordinator.data
    )


class GMCSensor(CoordinatorEntity[GMCCoordinator], SensorEntity):
    """A reading of a GMC counter."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: GMCCoordinator, description: SensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        identity = coordinator.identity
        self._attr_unique_id = f"{identity.serial_number}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity.serial_number)},
            manufacturer=MANUFACTURER,
            model=identity.model,
            name=identity.model,
            serial_number=identity.serial_number,
            # Empty str when pygmc couldn't split the version. None hides it.
            sw_version=identity.firmware or None,
        )

    @property
    def native_value(self) -> float | None:
        """Return the latest reading."""
        return self.coordinator.data[self.entity_description.key]
