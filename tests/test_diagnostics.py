"""Tests for PyGMC diagnostics."""

from __future__ import annotations

from http import HTTPStatus

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import setup_integration


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Diagnostics explain the counter's readings without its serial number."""
    await setup_integration(hass, config_entry)
    assert await async_setup_component(hass, "diagnostics", {})
    client = await hass_client()

    response = await client.get(
        f"/api/diagnostics/config_entry/{config_entry.entry_id}"
    )
    assert response.status == HTTPStatus.OK
    diagnostics = (await response.json())["data"]
    # Ports live in a per-test temporary directory.
    assert diagnostics == snapshot(exclude=props("port"))
