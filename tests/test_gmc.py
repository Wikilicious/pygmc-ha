"""Tests for the pygmc wrapper's edge cases."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.pygmc.gmc import (
    CommunicationError,
    brltty_claims_gmc,
    open_device,
    read_timeout,
)

from .fake_gmc import SERIAL_NUMBER, FakeBus

BRLTTY_RULE = 'ENV{PRODUCT}=="1a86/7523/*", ENV{BRLTTY_BRAILLE_DRIVER}="bm"\n'


def test_brltty_override_masks_rule(udev_rule_dirs: tuple[Path, Path]) -> None:
    """A rule masked through /dev/null in /etc no longer claims the counter."""
    etc, usr = udev_rule_dirs
    (usr / "85-brltty.rules").write_text(BRLTTY_RULE)
    assert brltty_claims_gmc()

    (etc / "85-brltty.rules").symlink_to("/dev/null")
    assert not brltty_claims_gmc()


def test_brltty_unreadable_rule(udev_rule_dirs: tuple[Path, Path]) -> None:
    """Rule files that cannot be read are skipped."""
    (udev_rule_dirs[1] / "85-brltty.rules").mkdir()
    assert not brltty_claims_gmc()


def test_read_timeout() -> None:
    """Slow baud rates get time to transfer the 512 byte config."""
    assert read_timeout(115200) == pytest.approx(1.044, abs=1e-3)
    assert read_timeout(1200) == pytest.approx(5.267, abs=1e-3)


def test_open_device_serial_error(bus: FakeBus, port: str) -> None:
    """Serial errors opening the counter are communication errors."""
    with patch(
        "custom_components.pygmc.gmc.auto_get_device_from_discovery_details"
    ) as get_class:
        get_class.return_value.side_effect = OSError("busy")
        with pytest.raises(CommunicationError):
            open_device(port, 115200, SERIAL_NUMBER)
    assert not bus.open_connections
