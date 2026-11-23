"""In-memory GMC counters speaking the serial protocol pygmc implements.

Tests patch serial.Serial with FakeSerial, so pygmc's own discovery, model
detection, config parsing and calibration math run unmodified. Ports are empty
files in a temporary directory: deleting one unplugs the counter behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import struct
from typing import Any, ClassVar

import serial
from serial.tools.list_ports_common import ListPortInfo

SERIAL_NUMBER = "f488ec2b6b5c35"
OTHER_SERIAL_NUMBER = "0123456789abcd"

# Calibration points (CPM, μSv/h) stored in the counter's config.
CALIBRATION = ((100, 0.65), (30000, 195.0), (60000, 390.0))
# Config offsets of the (CPM, μSv/h) fields of each calibration point.
_CALIBRATION_OFFSETS = ((8, 10), (14, 16), (20, 22))


@dataclass
class FakeGMC:
    """A counter: what it reports and how it answers commands."""

    version: str
    cpm_width: int
    config_size: int
    float_format: str
    voltage_reply: bytes
    temperature_reply: bytes
    cps: int
    serial_number: str = SERIAL_NUMBER
    baudrate: int = 115200
    cpm: int = 18
    powered: bool = True
    # Commands the counter answers with nothing, as if the reply was lost.
    silent_commands: set[bytes] = field(default_factory=set)

    def config(self) -> bytes:
        """Return the <GETCFG>> reply holding the calibration points."""
        config = bytearray(self.config_size)
        for (cpm_at, usv_at), (cpm, usv) in zip(
            _CALIBRATION_OFFSETS, CALIBRATION, strict=True
        ):
            config[cpm_at : cpm_at + 2] = struct.pack(">H", cpm)
            config[usv_at : usv_at + 4] = struct.pack(self.float_format, usv)
        return bytes(config)

    def reply(self, command: bytes) -> bytes:
        """Return the counter's answer to a command."""
        if not self.powered or command in self.silent_commands:
            return b""
        replies = {
            b"<GETSERIAL>>": lambda: bytes.fromhex(self.serial_number),
            b"<GETVER>>": self.version.encode,
            b"<GETCPM>>": lambda: self.cpm.to_bytes(self.cpm_width, "big"),
            b"<GETCPS>>": lambda: self.cps.to_bytes(4, "big"),
            b"<GETCFG>>": self.config,
            b"<GETVOLT>>": lambda: self.voltage_reply,
            b"<GETTEMP>>": lambda: self.temperature_reply,
        }
        # Anything else, like <HEARTBEAT0>>, gets no reply.
        return replies.get(command, bytes)()


def gmc_320(**changes: Any) -> FakeGMC:
    """Return a GMC-320, speaking protocol RFC1201."""
    counter = FakeGMC(
        version="GMC-320Re 4.26",
        cpm_width=2,
        config_size=256,
        float_format="<f",
        voltage_reply=bytes([42]),
        temperature_reply=bytes([21, 5, 0, 0xAA]),
        cps=0,
    )
    return replace(counter, **changes)


def gmc_500_plus(**changes: Any) -> FakeGMC:
    """Return a GMC-500+, speaking protocol RFC1801."""
    counter = FakeGMC(
        version="GMC-500+Re 2.22",
        cpm_width=4,
        config_size=512,
        float_format=">f",
        voltage_reply=b"4.8v\x00",
        temperature_reply=b"",
        cps=1,
    )
    return replace(counter, **changes)


class FakeSerial:
    """Stands in for serial.Serial, wired to the counters plugged into a FakeBus."""

    bus: ClassVar[FakeBus]

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> None:
        """Open a port, failing like pyserial if it does not exist."""
        if not os.path.exists(port):
            raise serial.SerialException(2, f"could not open port {port}")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        self.out_waiting = 0
        self._rx = bytearray()
        self.bus.connections.append(self)

    @property
    def in_waiting(self) -> int:
        """Return the number of bytes waiting to be read."""
        return len(self._rx)

    def write(self, data: bytes) -> int:
        """Send a command to the counter on the port, if any."""
        if not os.path.exists(self.port):
            raise serial.SerialException("write failed: [Errno 5] Input/output error")
        counter = self.bus.counters.get(os.path.realpath(self.port))
        # At the wrong baud rate a command arrives as garbage and goes unanswered.
        if counter is not None and counter.baudrate == self.baudrate:
            self._rx += counter.reply(bytes(data))
        return len(data)

    def flush(self) -> None:
        """Wait for writes to finish."""

    def read(self, size: int = 1) -> bytes:
        """Read up to size bytes."""
        data = bytes(self._rx[:size])
        del self._rx[:size]
        return data

    def read_all(self) -> bytes:
        """Read everything waiting."""
        return self.read(len(self._rx))

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        """Read size bytes, or what arrived before the timeout."""
        return self.read(len(self._rx) if size is None else size)

    def reset_input_buffer(self) -> None:
        """Drop unread input."""
        self._rx.clear()

    def reset_output_buffer(self) -> None:
        """Drop unsent output."""

    def close(self) -> None:
        """Close the port."""
        self.is_open = False


@dataclass
class FakeBus:
    """Serial ports, optionally with a counter behind them."""

    directory: Path
    counters: dict[str, FakeGMC] = field(default_factory=dict)
    connections: list[FakeSerial] = field(default_factory=list)
    _usb_ids: dict[str, tuple[int, int]] = field(default_factory=dict)

    def plug(
        self,
        counter: FakeGMC | None = None,
        name: str = "ttyUSB0",
        usb_id: tuple[int, int] = (0x1A86, 0x7523),
    ) -> str:
        """Add a port with a counter, or with hardware that never answers."""
        path = self.directory / name
        path.touch()
        port = os.path.realpath(path)
        if counter is not None:
            self.counters[port] = counter
        self._usb_ids[port] = usb_id
        return port

    def unplug(self, port: str) -> None:
        """Remove a port, as when its USB cable is pulled."""
        Path(port).unlink()

    def deny_access(self, port: str) -> None:
        """Make a port unreadable, as for a user outside the dialout group."""
        Path(port).chmod(0)

    def allow_access(self, port: str) -> None:
        """Make a port readable again."""
        Path(port).chmod(0o660)

    def comports(self, include_links: bool = False) -> list[ListPortInfo]:
        """List present ports the way pyserial does."""
        ports = []
        for port, (vid, pid) in self._usb_ids.items():
            if not os.path.exists(port):
                continue
            info = ListPortInfo(port, skip_link_detection=True)
            info.vid, info.pid, info.description = vid, pid, "USB2.0-Serial"
            ports.append(info)
        return ports

    @property
    def open_connections(self) -> list[FakeSerial]:
        """Return the connections not closed yet."""
        return [connection for connection in self.connections if connection.is_open]
