"""
Talk to GMC counters through pygmc.

Everything in here is blocking serial/file I/O... run it in an executor.

This is the only module that imports pygmc. pygmc lets a grab bag of exception types
escape (OSError, struct.error, IndexError, whatever the parser trips on) so they all
get funneled into GMCError below. The config flow and the coordinator share the
translation keys hanging off those errors.
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

# pygmc Discovery defaults to a 3 second timeout and tries every baudrate in turn.
# Point it at a switched off counter (or the wrong port) and you'll be waiting a while.
# A GMC answers within milliseconds - 1 second is plenty.
PROBE_TIMEOUT: Final = 1.0

# Not every protocol has these. key=sensor key, value=pygmc method.
# RFC1201 (300, 320, SE) - battery_voltage, temperature
# RFC1801 (500, 600)     - cps, battery_voltage
# Spec404 (800)          - cps
# No need to maintain a model list here, hasattr() on the pygmc device figures it out.
OPTIONAL_READINGS: Final = {
    "cps": "get_cps",
    "battery_voltage": "get_voltage",
    "temperature": "get_temp",
}

# pygmc hands short/garbled replies straight to struct.unpack & friends.
# i.e. a bad reply shows up as one of these... not as a serial error.
_PARSE_ERRORS: Final = (ArithmeticError, LookupError, ValueError, struct.error)


class GMCError(Exception):
    """
    A GMC counter could not be reached or understood.

    translation_key is used twice: as the config flow error and as the exception
    message shown when the entry is unavailable.
    """

    translation_key: ClassVar[str] = "communication_error"

    def __init__(self, port: str, **placeholders: str) -> None:
        """Keep the port and any extra translation placeholders."""
        self.translation_placeholders = {"port": port, **placeholders}
        super().__init__(f"{self.translation_key}: {self.translation_placeholders}")


class PortNotFoundError(GMCError):
    """Serial port doesn't exist. Most likely unplugged."""

    translation_key = "port_not_found"


class PortPermissionError(GMCError):
    """Serial port exists but Home Assistant isn't allowed to open it."""

    translation_key = "permission_denied"


class NoResponseError(GMCError):
    """Nothing on the port answered like a GMC."""

    translation_key = "no_response"


class CommunicationError(GMCError):
    """Counter stopped answering or sent back garbage."""

    translation_key = "communication_error"


class UnsupportedModelError(GMCError):
    """A model pygmc doesn't know (yet)."""

    translation_key = "unsupported_model"


class DeviceMismatchError(GMCError):
    """A different counter than expected answered on the port."""

    translation_key = "device_mismatch"


@dataclass(frozen=True, slots=True, kw_only=True)
class GMCIdentity:
    """What a GMC says about itself."""

    port: str
    baudrate: int
    serial_number: str
    model: str
    firmware: str


def _discover(port: str, baudrate: int | None) -> Any:
    """
    Get pygmc Discovery details of the counter on a port.

    Parameters
    ----------
    port: str
        Dev device e.g. /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
    baudrate: int | None
        Tried first, if known. Then every baudrate pygmc knows.

    Returns
    -------
    pygmc DeviceDetails (namedtuple)

    """
    # pygmc only logs errors opening a port... which leaves nothing to tell an
    # unplugged counter apart from a permission problem. Check both ourselves.
    if not Path(port).exists():
        raise PortNotFoundError(port)
    if not os.access(port, os.R_OK | os.W_OK):
        raise PortPermissionError(port)

    # The baudrate can be changed on the device, so a known baudrate is a good first
    # guess - not a guarantee. None=pygmc tries them all.
    baudrates_to_try = (baudrate, None) if baudrate else (None,)
    for rate in baudrates_to_try:
        try:
            found = Discovery(port=port, baudrate=rate, timeout=PROBE_TIMEOUT)
        except OSError as err:
            raise CommunicationError(port, error=str(err)) from err
        if devices := found.get_all_devices():
            return devices[0]
    raise NoResponseError(port)


def _identify(port: str, details: Any) -> tuple[GMCIdentity, Callable[..., BaseDevice]]:
    """Get a discovered counter's identity and the pygmc class for its model."""
    device_class: Callable[..., BaseDevice] | None = (
        auto_get_device_from_discovery_details(details)
    )
    if device_class is None:
        raise UnsupportedModelError(port, version=details.version)
    identity = GMCIdentity(
        port=details.port,
        baudrate=details.baudrate,
        serial_number=details.serial_number,
        # pygmc leaves model & revision empty when the version regex doesn't match.
        # Full version string is better than nothing.
        model=details.model or details.version,
        firmware=details.revision,
    )
    return identity, device_class


def probe(port: str, baudrate: int | None = None) -> GMCIdentity:
    """
    Identify the GMC counter on a port.

    Parameters
    ----------
    port: str
        Dev device to probe.
    baudrate: int | None
        Tried first, then every baudrate pygmc knows.

    Returns
    -------
    GMCIdentity

    """
    identity, _ = _identify(port, _discover(port, baudrate))
    return identity


def open_device(port: str, baudrate: int, serial_number: str | None) -> GMCDevice:
    """
    Connect to the counter with the given serial number.

    Parameters
    ----------
    port: str
        Dev device the counter is plugged into.
    baudrate: int
        Last known baudrate. Tried first.
    serial_number: str | None
        The counter we expect. Anything else on the port raises DeviceMismatchError.

    Returns
    -------
    GMCDevice
        An open connection. Caller must close() it.

    """
    identity, device_class = _identify(port, _discover(port, baudrate))
    if identity.serial_number != serial_number:
        # Swapped counters... or two of the same USB-serial chip fighting over one
        # /dev/serial/by-id path. Either way, not our counter.
        raise DeviceMismatchError(port, serial_number=identity.serial_number)

    try:
        gc = device_class(port=port, baudrate=identity.baudrate)
    except OSError as err:
        raise CommunicationError(port, error=str(err)) from err

    try:
        config: dict[str, Any] = gc.get_config()
    except (OSError, *_PARSE_ERRORS) as err:
        # Don't leave the port open on the way out
        gc.connection.close_connection()
        raise CommunicationError(port, error=str(err)) from err

    # CPM to uSv/h calibration points. They explain the dose rate the counter reports.
    # Config key names differ per protocol (CalibrationCPM_0, Calibration_CPM_1, ...)
    calibration = {
        key: value
        for key, value in config.items()
        if key.lower().startswith("calibration")
    }
    return GMCDevice(identity, gc, calibration)


class GMCDevice:
    """An open connection to a GMC counter."""

    def __init__(
        self, identity: GMCIdentity, gc: BaseDevice, calibration: dict[str, Any]
    ) -> None:
        """
        Wrap a connected pygmc device.

        Parameters
        ----------
        identity: GMCIdentity
            What the counter said about itself when it was opened.
        gc: pygmc.devices.BaseDevice
            Connected pygmc geiger counter.
        calibration: dict
            CPM to uSv/h calibration points from the device config.

        """
        self.identity = identity
        self.calibration = calibration
        self._gc = gc

    def read(self) -> dict[str, float | None]:
        """
        Read every value the counter supports.

        CPM is the one that matters. If CPM fails, the whole read fails.
        Everything else is best effort... a bad reply just blanks that one value.

        Returns
        -------
        dict
            Sensor key to value. Value=None means the reply was unreadable this time.

        """
        try:
            cpm: int = self._gc.get_cpm()
            data: dict[str, float | None] = {
                "cpm": cpm,
                # Pass in cpm so pygmc converts it with the device calibration
                # instead of polling the device for a second, different, cpm.
                "dose_rate": self._read_secondary(self._gc.get_usv_h, cpm),
            }
            for key, method in OPTIONAL_READINGS.items():
                if hasattr(self._gc, method):
                    data[key] = self._read_secondary(getattr(self._gc, method))
        except (OSError, *_PARSE_ERRORS) as err:
            raise CommunicationError(self.identity.port, error=str(err)) from err
        return data

    def _read_secondary(self, read: Callable[..., Any], *args: Any) -> float | None:
        """Read a secondary value. A bad reply only blanks that value."""
        try:
            value: float = read(*args)
        except _PARSE_ERRORS as err:
            LOGGER.debug("Ignoring unreadable %s reply: %r", read.__name__, err)
            # MUST reset buffers... a late reply left in the buffer would be read as
            # the answer to the next command and throw off everything after it.
            self._gc.connection.reset_buffers()
            return None
        return value

    def close(self) -> None:
        """Close serial connection."""
        self._gc.connection.close_connection()
