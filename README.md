# wyzesense component

## Installation
1. Download this repository as a ZIP (green button, top right)
2. Unzip the archive
3. Rename to just `wyzesense`
4. Place `wyzesense` under `/config/custom_components/`

Plug in the wyzesense hub (the usb device) into an open port on your device.

## Configuration
Add the following to your configuration file

```yaml
binary_sensor:
  - platform: wyzesense
    device: "/dev/hidraw0"
```
Most likely your device will be mounted to `/dev/hidraw0`. If you know it is mounted somewhere else then add the appropriate device.

Restart HA and the sensors you have already bound to the hub should show up with `assumed_state: true`. The first time the component hears from the sensor, the state and the rest of the fields such as battery, signal strength, etc. will be shown.

## Services
### `wyzesense.scan`
* Scans for new sensors for 30s. Press the button on the side of a sensor with a pin until the red led flashes three times. It will now be bound and show up in your entities.

### `wyzesense.remove`
* Removes a sensor
* Make sure you provide the correct MAC address of the sensor (which is the string of numbers and possibly letters that looks like `777A4656`).

## Running into issues?
1. Setup your logger to print debug messages for this component using:
```yaml
logger:
  default: info
  logs:
    custom_components.wyzesense: debug
```
2. Restart HA
3. Verify you're still having the issue
4. File an issue in this Github Repository containing your HA log (Developer section > Information > Load Full Home Assistant Log)
  * The log file can also be found at `/config/home-assistant.log`

