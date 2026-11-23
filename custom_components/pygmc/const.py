"""Constants for the PyGMC integration."""

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "pygmc"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "GQ Electronics"

CONF_BAUDRATE: Final = "baudrate"

# GMC counters report CPM as a rolling 60 second count, so polling once a minute
# yields one independent sample per window. Users who want more can disable
# polling for the entry and call homeassistant.update_entity themselves.
SCAN_INTERVAL: Final = timedelta(minutes=1)

# USB IDs of the CH340 USB-to-serial chip inside GMC counters. Other hardware
# uses the same generic chip, so a match alone never proves a GMC is attached.
GMC_USB_VID: Final = 0x1A86
GMC_USB_PID: Final = 0x7523

TROUBLESHOOTING_URL: Final = "https://github.com/Wikilicious/pygmc-ha#troubleshooting"
