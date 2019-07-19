# wyzesense component

## Installation
Download this repository as a ZIP (green button, top right), unzip, and place `wyzesense` under `/config/custom_components/`

Plug in the wyzesense hub (the usb device) into an open port on your device.

## Configuration
Add the following to your configuration file

```yaml
binary_sensor:
  - platform: wyzesense
    device: "/dev/hidraw0"
```
Most likely your device will be mounted to `/dev/hidraw0`. If you know it is mounted somewhere else then add the appropriate device.

Restart HA and sensors you have already bound to the hub should up with `assumed_state: true`. The first time the component hears from the sensor the state and the rest of the fields such as battery, signal strength, etc. will be shown.

## Services
### `wyzesense.scan`
* Scans for new sensors for 30s. Press the button on the side of a sensor with a pin until the red led flashes three times. It will now be bound and show up in your entities.

### `wyzesense.remove`
* Removes a sensor
* Make sure you provide the correct MAC address of the sensor (which is the string of numbers and possibly letters that looks like `213787AF`). Ensure that all letters in it are CAPITAL.
