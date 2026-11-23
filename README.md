# PyGMC for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/Wikilicious/pygmc-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/Wikilicious/pygmc-ha/actions/workflows/validate.yml)
[![Test](https://github.com/Wikilicious/pygmc-ha/actions/workflows/test.yml/badge.svg)](https://github.com/Wikilicious/pygmc-ha/actions/workflows/test.yml)

Monitor background radiation in Home Assistant with a GQ Electronics GMC Geiger counter plugged in over USB. The integration reads counts per minute and the dose rate from the counter every minute, entirely locally, using the [pygmc](https://github.com/Wikilicious/pygmc) library.

## Supported devices

Every model pygmc supports:

| Series | Models |
|---|---|
| GMC-300 | GMC-300, GMC-300S, GMC-300E+ |
| GMC-320 | GMC-320, GMC-320S, GMC-320+, GMC-320+V5 |
| GMC-500 | GMC-500, GMC-500+ |
| GMC-600 | GMC-600, GMC-600+ |
| GMC-800 | GMC-800 |
| GMC-SE | GMC-SE |

A counter pygmc does not recognize is refused during setup with the version it reports. Please [open a pygmc issue](https://github.com/Wikilicious/pygmc/issues) with that version.

## Sensors

Each counter becomes one device with the sensors its model supports:

| Sensor | Unit | Models | Enabled by default |
|---|---|---|---|
| Counts per minute | CPM | All | Yes |
| Dose rate | μSv/h | All | Yes |
| Counts per second | CPS | 500, 600, 800 series | No |
| Battery voltage (diagnostic) | V | 300, 320, 500, 600 series, SE | Yes |
| Temperature (diagnostic) | °C | 300, 320 series, SE | No |

- **Dose rate** is calculated from counts per minute with the calibration points stored in your counter, so it matches the counter's display.
- **Counts per second** is disabled by default: a one-second count sampled once a minute is mostly noise.
- **Temperature** is disabled by default because the sensor needs a few readings to settle. Enable either from the entity settings.

## Installation

### HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wikilicious&repository=pygmc-ha&category=integration)

1. In HACS, open the menu and select **Custom repositories**. Add `https://github.com/Wikilicious/pygmc-ha` with the type **Integration**.
2. Search for **PyGMC**, download it, and restart Home Assistant.

Home Assistant 2025.2 or newer is required.

### Manual

Copy `custom_components/pygmc` from the [latest release](https://github.com/Wikilicious/pygmc-ha/releases/latest) into the `custom_components` folder of your Home Assistant configuration, then restart Home Assistant.

## Setup

Plug the counter into the machine running Home Assistant and switch it on. Then:

- **If Home Assistant offers a discovered "PyGMC" device**, select **Add**. GMC counters use a common USB serial adapter (CH340) that other hardware uses too, so Home Assistant only talks to the port once you confirm.
- **Otherwise**, go to **Settings** > **Devices & services** > **Add integration** > **PyGMC**.

The only setting is the **Serial port**. Pick it from the list or type its path. Prefer a `/dev/serial/by-id/...` path over `/dev/ttyUSB0`, which can change when devices are plugged in a different order. The baud rate is detected automatically.

To move the counter to another port later, select **Reconfigure** on its entry. A counter found on a new port during setup or discovery also updates its existing entry.

### Giving Home Assistant access to the counter

| Installation | What to do |
|---|---|
| Home Assistant OS, Supervised | Nothing; USB serial ports are available automatically. |
| Container | Pass the port into the container, for example `--device /dev/serial/by-id/usb-1a86_USB2.0-Serial-if00-port0`, or with `devices:` in Docker Compose. |
| Core | Add the user running Home Assistant to the `dialout` group, for example `sudo usermod -aG dialout homeassistant`, then restart Home Assistant. |
| Virtual machine | Pass the USB device through to the VM, for example in Proxmox under **Hardware** > **Add** > **USB Device**. |

## Data updates

The counter is polled once a minute. Counts per minute is itself a rolling 60-second count, so each poll is a fresh, independent sample, and the long-term statistics of every sensor are recorded.

To poll more often, disable polling for the entry (on its menu, **System options**) and refresh the sensors from an automation:

```yaml
automation:
  - alias: Poll the Geiger counter every 10 seconds
    triggers:
      - trigger: time_pattern
        seconds: /10
    actions:
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.gmc_320_counts_per_minute
```

Refreshing one sensor reads the counter once and updates all of its sensors.

## Examples

Notify when radiation stays above a threshold. The typical background level is around 0.1 μSv/h, depending on location:

```yaml
automation:
  - alias: Radiation above normal
    triggers:
      - trigger: numeric_state
        entity_id: sensor.gmc_320_dose_rate
        above: 0.5
        for:
          minutes: 5
    actions:
      - action: notify.notify
        data:
          title: Radiation alert
          message: >-
            The dose rate has been {{ states('sensor.gmc_320_dose_rate') }} μSv/h
            for 5 minutes.
```

Other ideas:

- Show the dose rate history on a dashboard with a **Statistics graph** card, using the mean of each day.
- Warn before the battery of a portable counter runs out, using **Battery voltage**.

## Known limitations

- **One program per port.** While Home Assistant has the counter open, GQ Data Viewer, pygmc scripts and other programs cannot share it, and the other way around.
- **Several CH340 adapters share one by-id path.** These adapters have no USB serial number, so two of them, for example a counter and another device, compete for the same `/dev/serial/by-id/` link. The integration checks the counter's serial number on every connection and raises a repair issue if the wrong device answers. Use a `/dev/serial/by-path/` path in that case.
- **Counter settings are read at connection time.** After changing the calibration on the counter, reload the entry so the dose rate uses the new points.
- **Not exposed:** history logs stored on the counter, the separate high and low dose tubes of the GMC-500+, and the Wi-Fi and GMCmap upload settings of the 500 and 600 series.

## Troubleshooting

When the counter cannot be read, its sensors become unavailable and the entry shows why. Problems you need to fix also appear under **Settings** > **Repairs**.

| Message | What to do |
|---|---|
| Serial port does not exist | Check the cable and that the counter is switched on. On Container and VM installations, pass the port through as described above. |
| brltty took over the counter's USB adapter | Ubuntu's brltty package mistakes the counter's adapter for a braille display. Remove it with `sudo apt remove brltty`, or comment out its `1a86/7523` rule in `/usr/lib/udev/rules.d/85-brltty.rules`, then plug the counter in again. |
| Home Assistant is not allowed to open the port | On Core installations, add the Home Assistant user to the `dialout` group as described above. |
| The counter does not answer | Check that the counter is on and that no other program is using the port. |
| A different counter answers | Plug in the right counter, or select **Reconfigure** on the entry and choose the counter's new port. |

The integration keeps retrying and recovers by itself once the problem is fixed; repairs then clear themselves.

When reporting a problem, include:

- **Debug logs.** On the entry's menu, select **Enable debug logging**, reproduce the problem, then disable it to download the log.
- **Diagnostics.** On the entry's menu, select **Download diagnostics**. The file contains the counter's model, firmware and calibration, and redacts its serial number.

## Removal

1. Go to **Settings** > **Devices & services**, select **PyGMC**, and on the entry's menu select **Delete**.
2. If you installed with HACS, remove **PyGMC** in HACS and restart Home Assistant.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev   # Python 3.14 virtual environment with Home Assistant and the test harness
uv run pytest         # tests, with 100% coverage required
uv run ruff check && uv run ruff format --check
uv run mypy           # strict type checking
scripts/develop       # Home Assistant at http://localhost:8123 with the integration loaded
```

- **Tests need no hardware.** `tests/fake_gmc.py` emulates counters at the serial level, so pygmc's discovery, model detection and calibration code runs unmodified.
- **CI also tests the oldest supported Home Assistant release**, matching `homeassistant` in `hacs.json`.
- **On WSL2**, attach the counter to Linux with [usbipd-win](https://github.com/dorssel/usbipd-win) to try it with `scripts/develop`.

To release, publish a GitHub release with a tag like `v1.2.0`. A workflow sets that version in the manifest and attaches the `pygmc.zip` that HACS installs.

## License

[MIT](LICENSE)
