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

from .coordinator import GMCConfigEntry, GMCCoordinator
from .entity import GMCEntity

if TYPE_CHECKING:
    # Added in Home Assistant 2025.3; annotations are never evaluated at runtime.
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# The coordinator does all I/O.
PARALLEL_UPDATES = 0

UNIT_COUNTS_PER_MINUTE: Final = "CPM"
UNIT_COUNTS_PER_SECOND: Final = "CPS"
# Coded with U+03BC, the micro sign Home Assistant recommends over U+00B5.
UNIT_MICROSIEVERT_PER_HOUR: Final = "μSv/h"

SENSORS: Final = (
    SensorEntityDescription(
        key="cpm",
        translation_key="cpm",
        native_unit_of_measurement=UNIT_COUNTS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="dose_rate",
        translation_key="dose_rate",
        native_unit_of_measurement=UNIT_MICROSIEVERT_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    SensorEntityDescription(
        # A one second count sampled once a minute is mostly noise.
        key="cps",
        translation_key="cps",
        native_unit_of_measurement=UNIT_COUNTS_PER_SECOND,
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
        # pygmc notes the first readings are unreliable until the sensor settles.
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
    async_add_entities(
        GMCSensor(coordinator, description)
        for description in SENSORS
        if description.key in coordinator.data
    )


class GMCSensor(GMCEntity, SensorEntity):
    """A reading of a GMC counter."""

    def __init__(
        self, coordinator: GMCCoordinator, description: SensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the latest reading."""
        return self.coordinator.data[self.entity_description.key]
