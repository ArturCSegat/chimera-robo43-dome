# chimera-dome-lna

Chimera dome plugin for the LNA 43 cm observatory. Controls a classic rotating dome via a custom serial controller and doubles as a calibration lamp controller.

## Installation

```bash
pip install -e .
```

## Configuration

```yaml
dome:
    type: DomeRobo43
    name: main
    device: /dev/ttyUSB0
    model: "LNA custom dome"
    mode: Track
    telescope: /Telescope/0
    park_position: 108.0
    park_on_shutdown: true
    close_on_shutdown: true
    az_resolution: 2
    slew_timeout: 120
    serial_timeout_s: 10
```

