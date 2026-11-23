"""Blocking access to GMC counters through pygmc.

Everything here performs file or serial I/O and must run in an executor. This is
the only module that talks to pygmc: it turns pygmc's mix of exception types into
the GMCError hierarchy below, whose translation keys the config flow and the
coordinator share.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import struct
from typing import Any, ClassVar, Final

from pygmc import Discovery
from pygmc.devices import BaseDevice, auto_get_device_from_discovery_details

from .const import LOGGER

# Read timeout while probing, instead of pygmc's 3 seconds. pygmc tries every baud
# rate in turn with two reads each, so this keeps probing a switched off counter or
# the wrong port short. A GMC answers within milliseconds.
PROBE_TIMEOUT: Final = 1.0

# Readings only some protocols support, mapped to the pygmc method reading them.
OPTIONAL_READINGS: Final = {
    "cps": "get_cps",
    "battery_voltage": "get_voltage",
    "temperature": "get_temp",
}

# pygmc hands short or garbled replies straight to its parsers, so a bad reply
# surfaces as one of these rather than as a serial error.
_PARSE_ERRORS: Final = (ArithmeticError, LookupError, ValueError, struct.error)


class GMCError(Exception):
    """A GMC counter could not be reached or understood.

    The translation key names both the config flow error and the exception
    message shown when the entry is unavailable.
    """

    translation_key: ClassVar[str] = "communication_error"

    def __init__(self, port: str, **placeholders: str) -> None:
        """Initialize with the port and any extra translation placeholders."""
        self.translation_placeholders = {"port": port, **placeholders}
        super().__init__(f"{self.translation_key}: {self.translation_placeholders}")


class PortNotFoundError(GMCError):
    """The serial port does not exist; the counter is probably unplugged."""

    translation_key = "port_not_found"


class PortPermissionError(GMCError):
    """The serial port exists but Home Assistant may not open it."""

    translation_key = "permission_denied"


class NoResponseError(GMCError):
    """Nothing on the port answered like a GMC counter."""

    translation_key = "no_response"


class CommunicationError(GMCError):
    """The counter stopped answering or sent a malformed reply."""

    translation_key = "communication_error"


class UnsupportedModelError(GMCError):
    """The counter is a model pygmc does not know."""

    translation_key = "unsupported_model"


class DeviceMismatchError(GMCError):
    """A different counter than the expected one answered on the port."""

    translation_key = "device_mismatch"


@dataclass(frozen=True, slots=True, kw_only=True)
class GMCIdentity:
    """What a GMC counter reports about itself."""

    port: str
    baudrate: int
    serial_number: str
    model: str
    firmware: str


def _discover(port: str, baudrate: int | None) -> Any:
    """Return pygmc's details of the counter on a port, trying a baud rate first."""
    # pygmc only logs errors opening a port, which would leave nothing to tell an
    # unplugged counter from a permission problem.
    if not Path(port).exists():
        raise PortNotFoundError(port)
    if not os.access(port, os.R_OK | os.W_OK):
        raise PortPermissionError(port)
    for rate in (baudrate, None) if baudrate else (None,):
        try:
            found = Discovery(port=port, baudrate=rate, timeout=PROBE_TIMEOUT)
        except OSError as err:
            raise CommunicationError(port, error=str(err)) from err
        if devices := found.get_all_devices():
            return devices[0]
    raise NoResponseError(port)


def _identify(port: str, details: Any) -> tuple[GMCIdentity, Callable[..., BaseDevice]]:
    """Return a discovered counter's identity and the pygmc class for its model."""
    device_class: Callable[..., BaseDevice] | None = (
        auto_get_device_from_discovery_details(details)
    )
    if device_class is None:
        raise UnsupportedModelError(port, version=details.version)
    identity = GMCIdentity(
        port=details.port,
        baudrate=details.baudrate,
        serial_number=details.serial_number,
        # pygmc leaves model and revision empty for versions it cannot split.
        model=details.model or details.version,
        firmware=details.revision,
    )
    return identity, device_class


def probe(port: str, baudrate: int | None = None) -> GMCIdentity:
    """Identify the GMC counter on a port.

    The given baud rate is tried first, then every rate pygmc knows.
    """
    identity, _ = _identify(port, _discover(port, baudrate))
    return identity


def open_device(port: str, baudrate: int, serial_number: str | None) -> GMCDevice:
    """Connect to the counter with the given serial number."""
    identity, device_class = _identify(port, _discover(port, baudrate))
    if identity.serial_number != serial_number:
        raise DeviceMismatchError(port, serial_number=identity.serial_number)
    try:
        device = device_class(port=port, baudrate=identity.baudrate)
    except OSError as err:
        raise CommunicationError(port, error=str(err)) from err
    try:
        config: dict[str, Any] = device.get_config()
    except (OSError, *_PARSE_ERRORS) as err:
        device.connection.close_connection()
        raise CommunicationError(port, error=str(err)) from err
    calibration = {
        key: value
        for key, value in config.items()
        if key.lower().startswith("calibration")
    }
    return GMCDevice(identity, device, calibration)


class GMCDevice:
    """An open connection to a GMC counter."""

    def __init__(
        self, identity: GMCIdentity, device: BaseDevice, calibration: dict[str, Any]
    ) -> None:
        """Wrap a connected pygmc device."""
        self.identity = identity
        # CPM to dose rate calibration points; they explain the reported dose rate.
        self.calibration = calibration
        self._device = device

    def read(self) -> dict[str, float | None]:
        """Read every value the counter supports."""
        try:
            cpm: int = self._device.get_cpm()
            data: dict[str, float | None] = {
                "cpm": cpm,
                # Derived from cpm with the counter's own calibration points.
                "dose_rate": self._read_secondary(self._device.get_usv_h, cpm),
            }
            for key, method in OPTIONAL_READINGS.items():
                if hasattr(self._device, method):
                    data[key] = self._read_secondary(getattr(self._device, method))
        except (OSError, *_PARSE_ERRORS) as err:
            raise CommunicationError(self.identity.port, error=str(err)) from err
        return data

    def _read_secondary(self, read: Callable[..., Any], *args: Any) -> float | None:
        """Read a secondary value; a bad reply only blanks that value."""
        try:
            value: float = read(*args)
        except _PARSE_ERRORS as err:
            LOGGER.debug("Ignoring unreadable %s reply: %r", read.__name__, err)
            # A late reply would otherwise be taken as the next command's answer.
            self._device.connection.reset_buffers()
            return None
        return value

    def close(self) -> None:
        """Close the serial connection."""
        self._device.connection.close_connection()
