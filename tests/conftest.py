"""Fixtures for PyGMC tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pygmc.const import CONF_BAUDRATE, DOMAIN
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant

from .fake_gmc import SERIAL_NUMBER, FakeBus, FakeGMC, FakeSerial, gmc_320


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/ in every test."""


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default, as Home Assistant's tests do."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
def bus(tmp_path: Path) -> Generator[FakeBus]:
    """Replace pyserial with fake ports that tests plug counters into."""
    directory = tmp_path / "dev"
    directory.mkdir()
    fake_bus = FakeBus(directory)
    FakeSerial.bus = fake_bus
    with (
        patch("serial.Serial", FakeSerial),
        patch("serial.tools.list_ports.comports", side_effect=fake_bus.comports),
    ):
        yield fake_bus


@pytest.fixture
def counter() -> FakeGMC:
    """Return the counter plugged in by the port fixture."""
    return gmc_320()


@pytest.fixture
def port(bus: FakeBus, counter: FakeGMC) -> str:
    """Plug the counter in and return its port."""
    return bus.plug(counter)


@pytest.fixture
def config_entry(port: str) -> MockConfigEntry:
    """Return a config entry for the counter."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="GMC-320",
        unique_id=SERIAL_NUMBER,
        data={CONF_PORT: port, CONF_BAUDRATE: 115200},
        version=1,
        minor_version=1,
    )


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration from a config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
