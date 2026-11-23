"""Config flow for PyGMC."""

from __future__ import annotations

import os
from typing import Any, override

from serial.tools import list_ports
import voluptuous as vol

from homeassistant.components import usb
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PORT
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import CONF_BAUDRATE, DOMAIN, GMC_USB_PID, GMC_USB_VID, LOGGER
from .gmc import GMCError, GMCIdentity, NoResponseError, UnsupportedModelError, probe


def _hex_id(value: int | None) -> str | None:
    # 6790 --> '1A86'
    return None if value is None else f"{value:04X}"


def _list_ports(in_use: set[str]) -> list[SelectOptionDict]:
    """
    List serial ports not already used by an entry.

    Likely GMC counters (CH340 vid/pid) first, then everything else.
    Not filtering out the rest... a GMC behind some other adapter should still work.
    """
    # realpath - /dev/serial/by-id/... and /dev/ttyUSB0 can be the same port
    in_use_devices = {os.path.realpath(port) for port in in_use}
    options: list[SelectOptionDict] = []
    for port in sorted(
        list_ports.comports(),
        key=lambda p: ((p.vid, p.pid) != (GMC_USB_VID, GMC_USB_PID), p.device),
    ):
        if os.path.realpath(port.device) in in_use_devices:
            continue
        path = usb.get_serial_by_id(port.device)
        label = usb.human_readable_device_name(
            path,
            port.serial_number,
            port.manufacturer,
            port.description,
            _hex_id(port.vid),
            _hex_id(port.pid),
        )
        options.append(SelectOptionDict(value=path, label=label))
    return options


def _ports_match(port: str, others: set[str]) -> bool:
    return os.path.realpath(port) in {os.path.realpath(other) for other in others}


class PyGMCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PyGMC."""

    VERSION = 1
    MINOR_VERSION = 1

    _discovered_port: str

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick the counter's serial port."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                identity = await self._async_probe(user_input[CONF_PORT])
            except GMCError as err:
                errors["base"] = err.translation_key
                placeholders = err.translation_placeholders
            except Exception:
                # Unsure of every exception pygmc/pyserial can throw. Log it, tell the
                # user to check the logs, don't crash the flow.
                LOGGER.exception("Unexpected error probing %s", user_input[CONF_PORT])
                errors["base"] = "unknown"
            else:
                return await self._async_create_entry(identity)

        return self.async_show_form(
            step_id="user",
            data_schema=await self._async_port_schema(user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    @override
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """
        Handle a plugged in USB serial adapter of the kind GMC counters use.

        Nothing is written to the port until the user confirms. The CH340 chip is
        generic... the thing behind it may not be a GMC at all. Sending it GMC
        commands uninvited is rude (and who knows what it does to an Arduino).
        """
        port = await self.hass.async_add_executor_job(
            usb.get_serial_by_id, discovery_info.device
        )
        if await self.hass.async_add_executor_job(
            _ports_match, port, self._configured_ports()
        ):
            return self.async_abort(reason="already_configured")
        # Only the port is known until the counter is probed - no serial number yet.
        # Side effect: if the user ignores this discovery, later discoveries on the
        # same port are ignored too.
        await self.async_set_unique_id(
            f"{discovery_info.vid}:{discovery_info.pid}_{port}"
        )
        self._abort_if_unique_id_configured()
        self._discovered_port = port
        self.context["title_placeholders"] = {"port": port}
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Probe the discovered port once the user agrees."""
        errors: dict[str, str] = {}
        placeholders = {"port": self._discovered_port}
        if user_input is not None:
            try:
                identity = await self._async_probe(self._discovered_port)
            except NoResponseError:
                # Not a GMC. Some other CH340 gadget.
                return self.async_abort(
                    reason="not_gmc_device", description_placeholders=placeholders
                )
            except UnsupportedModelError as err:
                return self.async_abort(
                    reason="unsupported_model",
                    description_placeholders=err.translation_placeholders,
                )
            except GMCError as err:
                errors["base"] = err.translation_key
                placeholders = err.translation_placeholders
            except Exception:
                LOGGER.exception("Unexpected error probing %s", self._discovered_port)
                errors["base"] = "unknown"
            else:
                return await self._async_create_entry(identity)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="usb_confirm",
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user move the counter to another serial port."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                # Known baudrate first. If the user picked the port the running entry
                # is on, cycling through every baudrate would mess with its line
                # settings.
                identity = await self._async_probe(
                    user_input[CONF_PORT], entry.data[CONF_BAUDRATE]
                )
            except GMCError as err:
                errors["base"] = err.translation_key
                placeholders = err.translation_placeholders
            except Exception:
                LOGGER.exception("Unexpected error probing %s", user_input[CONF_PORT])
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(identity.serial_number)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PORT: identity.port,
                        CONF_BAUDRATE: identity.baudrate,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=await self._async_port_schema(
                user_input or {CONF_PORT: entry.data[CONF_PORT]}, entry.entry_id
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_probe(self, port: str, baudrate: int | None = None) -> GMCIdentity:
        # strip() - a copy-pasted path with a trailing space is not a missing port
        return await self.hass.async_add_executor_job(probe, port.strip(), baudrate)

    async def _async_create_entry(self, identity: GMCIdentity) -> ConfigFlowResult:
        await self.async_set_unique_id(identity.serial_number)
        # Already configured counter showed up on another port? Follow it there.
        self._abort_if_unique_id_configured(
            updates={CONF_PORT: identity.port, CONF_BAUDRATE: identity.baudrate}
        )
        return self.async_create_entry(
            title=identity.model,
            data={CONF_PORT: identity.port, CONF_BAUDRATE: identity.baudrate},
        )

    def _configured_ports(self, exclude_entry_id: str | None = None) -> set[str]:
        return {
            entry.data[CONF_PORT]
            for entry in self._async_current_entries(include_ignore=False)
            if entry.entry_id != exclude_entry_id
        }

    async def _async_port_schema(
        self, suggested: dict[str, Any] | None, exclude_entry_id: str | None = None
    ) -> vol.Schema:
        ports = await self.hass.async_add_executor_job(
            _list_ports, self._configured_ports(exclude_entry_id)
        )
        if suggested is None and ports:
            # Pre-select the most likely GMC (sorted first by _list_ports)
            suggested = {CONF_PORT: ports[0]["value"]}
        schema = vol.Schema(
            {
                vol.Required(CONF_PORT): SelectSelector(
                    SelectSelectorConfig(
                        options=ports,
                        # Let the user type a path we didn't list
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.add_suggested_values_to_schema(schema, suggested)
