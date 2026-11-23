"""Blocking access to GMC counters through pygmc.

Everything here performs file or serial I/O and must run in an executor. This is
the only module that talks to pygmc: it turns pygmc's mix of exception types into
the GMCError hierarchy below, whose translation keys the config flow and the
coordinator share.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import importlib.metadata
import logging
import os
from pathlib import Path
import re
import struct
import threading
from typing import Any, ClassVar, Final

from pygmc import Discovery
from pygmc.devices import BaseDevice, auto_get_device_from_discovery_details

_LOGGER = logging.getLogger(__name__)

PYGMC_VERSION: Final = importlib.metadata.version("pygmc")

# Read timeout while probing. A GMC answers within milliseconds and probe replies
# are a few bytes, so one second suffices at any baud rate while keeping a probe of
# the wrong port short: pygmc tries every known baud rate in turn.
PROBE_TIMEOUT: Final = 1.0

# The longest reply read from an open counter is the 512 byte <GETCFG>> of newer
# models, about 5120 bits on the wire.
_LONGEST_REPLY_BITS: Final = 512 * 10

# Readings only some protocols support, mapped to the pygmc method reading them.
OPTIONAL_READINGS: Final = {
    "cps": "get_cps",
    "battery_voltage": "get_voltage",
    "temperature": "get_temp",
}

# pygmc hands short or garbled replies straight to its parsers, so a bad reply
# surfaces as one of these rather than as a serial error.
_PARSE_ERRORS: Final = (ArithmeticError, LookupError, ValueError, struct.error)

# udev rule directories, highest precedence first. A file in an earlier directory
# replaces a file of the same name in a later one.
_UDEV_RULE_DIRS: Final = (
    Path("/etc/udev/rules.d"),
    Path("/run/udev/rules.d"),
    Path("/usr/lib/udev/rules.d"),
    Path("/lib/udev/rules.d"),
)
_BRLTTY_CH340_RULE: Final = re.compile(r'^\s*ENV\{PRODUCT\}=="1a86/7523/', re.MULTILINE)


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


class BrlttyError(GMCError):
    """The port is missing because brltty took over the counter's USB adapter."""

    translation_key = "brltty"


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
    # Raw version string, e.g. "GMC-320Re 4.26", split by pygmc into model and firmware.
    version: str
    model: str
    firmware: str


_port_locks: dict[str, threading.Lock] = {}
_port_locks_guard = threading.Lock()


def _port_lock(port: str) -> threading.Lock:
    """Return the lock serializing I/O on a port, whichever path names it."""
    key = os.path.realpath(port)
    with _port_locks_guard:
        return _port_locks.setdefault(key, threading.Lock())


def brltty_claims_gmc() -> bool:
    """Return whether a brltty udev rule takes over the counter's USB adapter.

    Ubuntu's brltty package treats every CH340 as a braille display and detaches
    it from the serial driver, so the counter's port disappears when plugged in.
    """
    effective: dict[str, Path] = {}
    for directory in reversed(_UDEV_RULE_DIRS):
        for rule_file in directory.glob("*brltty*.rules"):
            effective[rule_file.name] = rule_file
    for rule_file in effective.values():
        try:
            rules = rule_file.read_text(errors="replace")
        except OSError:
            continue
        if _BRLTTY_CH340_RULE.search(rules):
            return True
    return False


def _check_port(port: str) -> None:
    """Fail early, with a specific error, when a port cannot be opened.

    pygmc logs and swallows errors opening a port, which would otherwise leave
    nothing to tell an unplugged counter from a permission problem.
    """
    if not os.path.exists(port):
        if brltty_claims_gmc():
            raise BrlttyError(port)
        raise PortNotFoundError(port)
    if not os.access(port, os.R_OK | os.W_OK):
        raise PortPermissionError(port)


def _discover(port: str, baudrate: int | None) -> Any:
    """Return pygmc's details of the counter on a port, trying a baud rate first."""
    _check_port(port)
    with _port_lock(port):
        for rate in (baudrate, None) if baudrate else (None,):
            try:
                found = Discovery(port=port, baudrate=rate, timeout=PROBE_TIMEOUT)
            except OSError as err:
                raise CommunicationError(port, error=str(err)) from err
            if devices := found.get_all_devices():
                return devices[0]
    raise NoResponseError(port)


def _identity(details: Any) -> GMCIdentity:
    return GMCIdentity(
        port=details.port,
        baudrate=details.baudrate,
        serial_number=details.serial_number,
        version=details.version,
        model=details.model or details.version,
        firmware=details.revision,
    )


def _device_class(port: str, details: Any) -> Callable[..., BaseDevice]:
    device_class: Callable[..., BaseDevice] | None = (
        auto_get_device_from_discovery_details(details)
    )
    if device_class is None:
        raise UnsupportedModelError(port, version=details.version)
    return device_class


def probe(port: str, baudrate: int | None = None) -> GMCIdentity:
    """Identify the GMC counter on a port.

    The given baud rate is tried first, then every rate pygmc knows.
    """
    details = _discover(port, baudrate)
    _device_class(port, details)
    return _identity(details)


def read_timeout(baudrate: int) -> float:
    """Return a read timeout long enough for the longest reply at a baud rate."""
    return 1 + _LONGEST_REPLY_BITS / baudrate


def open_device(port: str, baudrate: int, serial_number: str | None) -> GMCDevice:
    """Connect to the counter with the given serial number."""
    details = _discover(port, baudrate)
    if details.serial_number != serial_number:
        raise DeviceMismatchError(port, serial_number=details.serial_number)
    device_class = _device_class(port, details)
    identity = _identity(details)
    with _port_lock(port):
        try:
            device = device_class(
                port=port,
                baudrate=identity.baudrate,
                timeout=read_timeout(identity.baudrate),
            )
        except OSError as err:
            raise CommunicationError(port, error=str(err)) from err
        try:
            config: dict[str, Any] = device.get_config()
        except (OSError, *_PARSE_ERRORS) as err:
            device.connection.close_connection()
            raise CommunicationError(port, error=repr(err)) from err
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
        self._lock = _port_lock(identity.port)
        self._optional = {
            key: method
            for key, method in OPTIONAL_READINGS.items()
            if hasattr(device, method)
        }

    def read(self) -> dict[str, float | None]:
        """Read every value the counter supports."""
        port = self.identity.port
        with self._lock:
            try:
                return self._read()
            except OSError as err:
                raise CommunicationError(port, error=str(err)) from err
            except _PARSE_ERRORS as err:
                raise CommunicationError(port, error=repr(err)) from err

    def _read(self) -> dict[str, float | None]:
        cpm: int = self._device.get_cpm()
        data: dict[str, float | None] = {
            "cpm": cpm,
            # Derived from cpm with the counter's own calibration points.
            "dose_rate": self._read_secondary(self._device.get_usv_h, cpm),
        }
        for key, method in self._optional.items():
            data[key] = self._read_secondary(getattr(self._device, method))
        return data

    def _read_secondary(self, read: Callable[..., Any], *args: Any) -> float | None:
        """Read a secondary value; a bad reply only blanks that value."""
        try:
            value: float = read(*args)
        except _PARSE_ERRORS as err:
            _LOGGER.debug("Ignoring unreadable %s reply: %r", read.__name__, err)
            # A late reply would otherwise be taken as the next command's answer.
            self._device.connection.reset_buffers()
            return None
        return value

    def close(self) -> None:
        """Close the serial connection."""
        with self._lock:
            self._device.connection.close_connection()
