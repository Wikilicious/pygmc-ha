"""Tests for the PyGMC config flow."""

from __future__ import annotations

from collections.abc import Generator
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pygmc.const import CONF_BAUDRATE, DOMAIN
from homeassistant.config_entries import SOURCE_IGNORE, SOURCE_USB, SOURCE_USER
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .fake_gmc import OTHER_SERIAL_NUMBER, SERIAL_NUMBER, FakeBus, FakeGMC, gmc_320

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root may open any file regardless of permissions"
)


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep created entries from connecting, so flows are tested alone."""
    with patch(
        "custom_components.pygmc.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


def usb_discovery(port: str) -> UsbServiceInfo:
    """Return what Home Assistant reports when a CH340 adapter is plugged in."""
    return UsbServiceInfo(
        device=port,
        vid="1A86",
        pid="7523",
        serial_number=None,
        manufacturer=None,
        description="USB2.0-Serial",
    )


def port_field(result: FlowResult) -> tuple[list[str], Any]:
    """Return the offered ports and the suggested one."""
    for key, selector in result["data_schema"].schema.items():
        if key == CONF_PORT:
            options = [option["value"] for option in selector.config["options"]]
            return options, (key.description or {}).get("suggested_value")
    raise AssertionError("No port field")


async def start_user_flow(hass: HomeAssistant) -> FlowResult:
    """Start a user flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def submit(hass: HomeAssistant, result: FlowResult, port: str) -> FlowResult:
    """Submit a port to a flow form."""
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PORT: port}
    )


async def test_user_flow(hass: HomeAssistant, bus: FakeBus, counter: FakeGMC) -> None:
    """Picking the counter's port creates an entry keyed by its serial number."""
    other = bus.plug(name="ttyACM0", usb_id=(0x10C4, 0xEA60))
    port = bus.plug(counter, name="ttyUSB0")

    result = await start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # The port of the GMC's USB chip comes first and is preselected.
    assert port_field(result) == ([port, other], port)

    result = await submit(hass, result, port)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "GMC-320"
    assert result["data"] == {CONF_PORT: port, CONF_BAUDRATE: 115200}
    assert result["result"].unique_id == SERIAL_NUMBER
    assert not bus.open_connections


async def test_user_flow_no_ports(hass: HomeAssistant, bus: FakeBus) -> None:
    """Without serial ports the form still opens, for typing a path."""
    result = await start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert port_field(result) == ([], None)


@pytest.mark.parametrize("counter", [gmc_320(baudrate=57600)])
async def test_user_flow_finds_baudrate(hass: HomeAssistant, port: str) -> None:
    """The baud rate the counter answers at is stored."""
    result = await submit(hass, await start_user_flow(hass), f" {port} ")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_PORT: port, CONF_BAUDRATE: 57600}


async def test_user_flow_port_not_found(
    hass: HomeAssistant, bus: FakeBus, counter: FakeGMC
) -> None:
    """A missing port is reported; plugging the counter in then works."""
    missing = str(bus.directory / "ttyUSB0")
    result = await submit(hass, await start_user_flow(hass), missing)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "port_not_found"}
    assert result["description_placeholders"] == {"port": missing}

    bus.plug(counter)
    result = await submit(hass, result, missing)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("counter", "error", "placeholders"),
    [
        (None, "no_response", {}),
        (gmc_320(powered=False), "no_response", {}),
        (
            gmc_320(version="GMC-999Re 1.00"),
            "unsupported_model",
            {"version": "GMC-999Re 1.00"},
        ),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    bus: FakeBus,
    counter: FakeGMC | None,
    error: str,
    placeholders: dict[str, str],
) -> None:
    """Ports without a supported counter are reported."""
    port = bus.plug(counter)
    result = await submit(hass, await start_user_flow(hass), port)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert result["description_placeholders"] == {"port": port, **placeholders}
    assert not bus.open_connections


@needs_non_root
async def test_user_flow_permission_denied(
    hass: HomeAssistant, bus: FakeBus, port: str
) -> None:
    """A port Home Assistant may not open is reported as such."""
    bus.deny_access(port)
    result = await submit(hass, await start_user_flow(hass), port)
    assert result["errors"] == {"base": "permission_denied"}

    bus.allow_access(port)
    result = await submit(hass, result, port)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_brltty(
    hass: HomeAssistant, bus: FakeBus, udev_rule_dirs: tuple[Path, Path]
) -> None:
    """A port taken by brltty is blamed on brltty, unless its rule is overridden."""
    etc, usr = udev_rule_dirs
    (usr / "85-brltty.rules").write_text(
        '# GMC counters\nENV{PRODUCT}=="1a86/7523/*", ENV{BRLTTY_BRAILLE_DRIVER}="bm"\n'
    )
    missing = str(bus.directory / "ttyUSB0")
    result = await submit(hass, await start_user_flow(hass), missing)
    assert result["errors"] == {"base": "brltty"}

    # A file in /etc replaces the packaged rule file of the same name.
    (etc / "85-brltty.rules").write_text('#ENV{PRODUCT}=="1a86/7523/*"\n')
    result = await submit(hass, result, missing)
    assert result["errors"] == {"base": "port_not_found"}


async def test_user_flow_communication_error(hass: HomeAssistant, port: str) -> None:
    """Serial errors pygmc lets through are reported with their message."""
    with patch(
        "custom_components.pygmc.gmc.Discovery", side_effect=TimeoutError("timed out")
    ):
        result = await submit(hass, await start_user_flow(hass), port)
    assert result["errors"] == {"base": "communication_error"}
    assert result["description_placeholders"] == {"port": port, "error": "timed out"}


async def test_user_flow_unexpected_error(hass: HomeAssistant, port: str) -> None:
    """Bugs are reported as unknown errors and the flow can be retried."""
    with patch("custom_components.pygmc.config_flow.probe", side_effect=RuntimeError):
        result = await submit(hass, await start_user_flow(hass), port)
    assert result["errors"] == {"base": "unknown"}

    result = await submit(hass, result, port)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_follows_moved_counter(
    hass: HomeAssistant, bus: FakeBus, counter: FakeGMC
) -> None:
    """Adding a configured counter found on another port updates its entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data={CONF_PORT: str(bus.directory / "ttyUSB0"), CONF_BAUDRATE: 57600},
    )
    entry.add_to_hass(hass)
    port = bus.plug(counter, name="ttyUSB1")

    result = await submit(hass, await start_user_flow(hass), port)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_PORT: port, CONF_BAUDRATE: 115200}


async def test_user_flow_hides_configured_ports(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry, port: str
) -> None:
    """Ports of configured counters are not offered again."""
    config_entry.add_to_hass(hass)
    other = bus.plug(name="ttyUSB1")
    result = await start_user_flow(hass)
    assert port_field(result) == ([other], other)


async def test_usb_discovery(hass: HomeAssistant, bus: FakeBus, port: str) -> None:
    """A discovered adapter is only probed once the user confirms."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "usb_confirm"
    assert result["description_placeholders"] == {"port": port}
    assert not bus.connections

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "GMC-320"
    assert result["data"] == {CONF_PORT: port, CONF_BAUDRATE: 115200}
    assert result["result"].unique_id == SERIAL_NUMBER


@pytest.mark.parametrize(
    ("counter", "reason", "placeholders"),
    [
        (None, "not_gmc_device", {}),
        (
            gmc_320(version="GMC-999Re 1.00"),
            "unsupported_model",
            {"version": "GMC-999Re 1.00"},
        ),
    ],
)
async def test_usb_discovery_aborts(
    hass: HomeAssistant,
    bus: FakeBus,
    counter: FakeGMC | None,
    reason: str,
    placeholders: dict[str, str],
) -> None:
    """Discovered hardware that is no supported counter aborts the flow."""
    port = bus.plug(counter)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert result["description_placeholders"] == {"port": port, **placeholders}


@needs_non_root
async def test_usb_discovery_error_then_retry(
    hass: HomeAssistant, bus: FakeBus, port: str
) -> None:
    """Errors the user can fix keep the confirmation open for another try."""
    bus.deny_access(port)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "permission_denied"}

    bus.allow_access(port)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_usb_discovery_unexpected_error(hass: HomeAssistant, port: str) -> None:
    """Bugs are reported as unknown errors on the confirmation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    with patch("custom_components.pygmc.config_flow.probe", side_effect=RuntimeError):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "unknown"}


async def test_usb_discovery_configured_port(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry, port: str
) -> None:
    """Discovery of a configured counter's port aborts without touching it."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not bus.connections


async def test_usb_discovery_ignored(
    hass: HomeAssistant, bus: FakeBus, port: str
) -> None:
    """An adapter the user ignored is not offered again."""
    MockConfigEntry(
        domain=DOMAIN, source=SOURCE_IGNORE, unique_id=f"1A86:7523_{port}"
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not bus.connections


async def test_usb_discovery_follows_moved_counter(
    hass: HomeAssistant, bus: FakeBus, counter: FakeGMC
) -> None:
    """A configured counter discovered on another port has its entry updated."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL_NUMBER,
        data={CONF_PORT: str(bus.directory / "ttyUSB0"), CONF_BAUDRATE: 115200},
    )
    entry.add_to_hass(hass)
    port = bus.plug(counter, name="ttyUSB1")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USB}, data=usb_discovery(port)
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_PORT] == port


async def test_reconfigure(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry, counter: FakeGMC
) -> None:
    """Reconfiguring moves the entry to the counter's new port."""
    config_entry.add_to_hass(hass)
    old_port = config_entry.data[CONF_PORT]
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert port_field(result) == ([old_port], old_port)

    bus.unplug(old_port)
    new_port = bus.plug(counter, name="ttyUSB1")
    result = await submit(hass, result, new_port)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {CONF_PORT: new_port, CONF_BAUDRATE: 115200}


async def test_reconfigure_wrong_device(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry
) -> None:
    """Another counter on the new port is refused."""
    config_entry.add_to_hass(hass)
    other = bus.plug(gmc_320(serial_number=OTHER_SERIAL_NUMBER), name="ttyUSB1")
    result = await submit(hass, await config_entry.start_reconfigure_flow(hass), other)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert config_entry.data[CONF_PORT] != other


async def test_reconfigure_errors(
    hass: HomeAssistant, bus: FakeBus, config_entry: MockConfigEntry, port: str
) -> None:
    """Errors keep the form open with the port entered."""
    config_entry.add_to_hass(hass)
    missing = str(bus.directory / "ttyUSB7")
    result = await submit(
        hass, await config_entry.start_reconfigure_flow(hass), missing
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "port_not_found"}
    assert port_field(result) == ([port], missing)

    with patch("custom_components.pygmc.config_flow.probe", side_effect=RuntimeError):
        result = await submit(hass, result, port)
    assert result["errors"] == {"base": "unknown"}

    result = await submit(hass, result, port)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
