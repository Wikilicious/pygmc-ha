"""Tests for the PyGMC sensors."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import setup_integration
from .fake_gmc import FakeGMC, gmc_320, gmc_500_plus


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize(
    "counter", [gmc_320(), gmc_500_plus()], ids=["gmc_320", "gmc_500_plus"]
)
async def test_sensors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    counter: FakeGMC,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Each counter gets the sensors its protocol supports."""
    await setup_integration(hass, config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)
