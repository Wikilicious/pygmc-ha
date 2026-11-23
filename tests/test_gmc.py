"""Tests for the pygmc wrapper's edge cases."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.pygmc.gmc import CommunicationError, open_device

from .fake_gmc import SERIAL_NUMBER, FakeBus


def test_open_device_serial_error(bus: FakeBus, port: str) -> None:
    """Serial errors opening the counter are communication errors."""
    with patch(
        "custom_components.pygmc.gmc.auto_get_device_from_discovery_details"
    ) as get_class:
        get_class.return_value.side_effect = OSError("busy")
        with pytest.raises(CommunicationError):
            open_device(port, 115200, SERIAL_NUMBER)
    assert not bus.open_connections
