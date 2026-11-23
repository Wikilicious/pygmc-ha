"""Tests for setting up PyGMC and keeping the counter connected."""

from __future__ import annotations

from datetime import timedelta
import os

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.pygmc.const import CONF_BAUDRATE, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PORT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .conftest import setup_integration
from .fake_gmc import OTHER_SERIAL_NUMBER, FakeBus, FakeGMC, gmc_320

CPM = "sensor.gmc_320_counts_per_minute"
DOSE_RATE = "sensor.gmc_320_dose_rate"
BATTERY_VOLTAGE = "sensor.gmc_320_battery_voltage"

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root may open any file regardless of permissions"
)


async def next_poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time to the next scheduled poll."""
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    # Scheduled refreshes run as background tasks.
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_setup_and_unload(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry
) -> None:
    """The entry keeps one connection open while loaded and closes it on unload."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert len(bus.open_connections) == 1

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not bus.open_connections


@pytest.mark.parametrize(
    ("counter", "reason"),
    [
        (gmc_320(powered=False), "does not answer"),
        (gmc_320(silent_commands={b"<GETCFG>>"}), "Communicating with the counter"),
        (gmc_320(silent_commands={b"<GETCPM>>"}), "Communicating with the counter"),
    ],
)
async def test_setup_retry(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry, reason: str
) -> None:
    """A counter that does not answer properly is retried, without leaking ports."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert reason in str(config_entry.reason)
    assert not bus.open_connections


async def test_setup_retry_unplugged(
    hass: HomeAssistant,
    bus: FakeBus,
    config_entry: MockConfigEntry,
    port: str,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An unplugged counter is retried; that needs no repair."""
    bus.unplug(port)
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "may be unplugged" in str(config_entry.reason)
    assert not issue_registry.issues


@needs_non_root
async def test_permission_issue(
    hass: HomeAssistant,
    bus: FakeBus,
    config_entry: MockConfigEntry,
    port: str,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A port Home Assistant may not open raises a repair issue until fixed."""
    bus.deny_access(port)
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    issue = issue_registry.async_get_issue(
        DOMAIN, f"permission_denied_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {"title": "GMC-320", "port": port}

    bus.allow_access(port)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert not issue_registry.issues


@pytest.mark.parametrize("counter", [gmc_320(serial_number=OTHER_SERIAL_NUMBER)])
async def test_issue_removed_with_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Repair issues of an entry that never loaded go away with the entry."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert issue_registry.issues

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not issue_registry.issues


@pytest.mark.parametrize("counter", [gmc_320(serial_number=OTHER_SERIAL_NUMBER)])
async def test_device_mismatch_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    port: str,
    counter: FakeGMC,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Another counter on the port raises a repair issue until the right one is back."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    issue = issue_registry.async_get_issue(
        DOMAIN, f"device_mismatch_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders == {
        "title": "GMC-320",
        "port": port,
        "serial_number": OTHER_SERIAL_NUMBER,
    }

    counter.serial_number = config_entry.unique_id
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert not issue_registry.issues


async def test_unplugged_and_back(
    hass: HomeAssistant,
    bus: FakeBus,
    config_entry: MockConfigEntry,
    counter: FakeGMC,
    port: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pulling the cable makes the sensors unavailable until it is plugged back in."""
    await setup_integration(hass, config_entry)
    assert hass.states.get(CPM).state == "18"

    bus.unplug(port)
    await next_poll(hass, freezer)
    assert hass.states.get(CPM).state == STATE_UNAVAILABLE
    assert not bus.open_connections

    # Still unplugged: the entry keeps trying without opening anything.
    await next_poll(hass, freezer)
    assert hass.states.get(CPM).state == STATE_UNAVAILABLE

    bus.plug(counter)
    counter.cpm = 25
    await next_poll(hass, freezer)
    assert hass.states.get(CPM).state == "25"
    assert len(bus.open_connections) == 1


async def test_counter_stops_answering(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    counter: FakeGMC,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A counter switched off goes unavailable and recovers once switched on."""
    await setup_integration(hass, config_entry)

    counter.powered = False
    await next_poll(hass, freezer)
    assert hass.states.get(CPM).state == STATE_UNAVAILABLE

    counter.powered = True
    await next_poll(hass, freezer)
    assert hass.states.get(CPM).state == "18"


async def test_lost_secondary_reply(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    counter: FakeGMC,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A lost secondary reply blanks only that value, not the radiation readings."""
    await setup_integration(hass, config_entry)

    counter.silent_commands = {b"<GETVOLT>>"}
    counter.cpm = 30
    await next_poll(hass, freezer)
    assert hass.states.get(BATTERY_VOLTAGE).state == STATE_UNKNOWN
    assert hass.states.get(CPM).state == "30"
    assert float(hass.states.get(DOSE_RATE).state) == pytest.approx(0.195)

    counter.silent_commands = set()
    await next_poll(hass, freezer)
    assert hass.states.get(BATTERY_VOLTAGE).state == "4.2"


@pytest.mark.parametrize("counter", [gmc_320(baudrate=57600)])
async def test_baudrate_change_remembered(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A counter set to another baud rate is found and the new rate stored."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_BAUDRATE] == 57600
    assert config_entry.data[CONF_PORT]
