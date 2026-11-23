"""Constants for the PyGMC integration."""

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "pygmc"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "GQ Electronics"

CONF_BAUDRATE: Final = "baudrate"

# CPM on a GMC is a rolling 60 second count. Polling once a minute = one independent
# sample per window. Polling faster just re-reads mostly the same 60 seconds.
# Want more anyway? Disable polling on the entry & call homeassistant.update_entity.
SCAN_INTERVAL: Final = timedelta(minutes=1)

# CH340 USB-to-serial chip inside GMC counters - same defaults pygmc uses in
# get_gmc_usb_devices(). It's a generic chip... plenty of other hardware uses it.
# A vid/pid match alone never proves a GMC is plugged in.
GMC_USB_VID: Final = 0x1A86
GMC_USB_PID: Final = 0x7523

TROUBLESHOOTING_URL: Final = "https://github.com/Wikilicious/pygmc-ha#troubleshooting"
