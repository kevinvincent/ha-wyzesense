""" 

wyzesense integration

"""

import logging
import voluptuous as vol

from homeassistant.const import CONF_FILENAME, CONF_DEVICE, \
	EVENT_HOMEASSISTANT_STOP, STATE_ON, STATE_OFF, ATTR_BATTERY_LEVEL, \
	ATTR_STATE, ATTR_DEVICE_CLASS, DEVICE_CLASS_SIGNAL_STRENGTH, \
	DEVICE_CLASS_TIMESTAMP

from homeassistant.components.binary_sensor import PLATFORM_SCHEMA, \
	BinarySensorDevice, DEVICE_CLASS_MOTION, DEVICE_CLASS_DOOR

import homeassistant.helpers.config_validation as cv

DOMAIN = "wyzesense"

ATTR_MAC = "mac"
ATTR_AVAILABLE = "available"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_DEVICE): cv.string
})

SERVICE_SCAN = 'scan'
SERVICE_REMOVE = 'remove'

SERVICE_SCAN_SCHEMA = vol.Schema({})

SERVICE_REMOVE_SCHEMA = vol.Schema({
    vol.Required(ATTR_MAC): cv.string
})

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_entites, discovery_info=None):
    import wyzesense

    _LOGGER.debug("Attempting to open connection to hub at " + config[CONF_DEVICE])

    entities = {}

    def on_event(ws, event):
        if event.BatteryLevel != 0 and event.SignalStrength != 0 :
            data = {
                ATTR_AVAILABLE: True,
                ATTR_MAC: event.MAC,
                ATTR_STATE: 1 if event.State == "open" or event.State == "active" else 0,
                ATTR_DEVICE_CLASS: DEVICE_CLASS_MOTION if event.Type == "motion" else DEVICE_CLASS_DOOR ,
                DEVICE_CLASS_TIMESTAMP: event.Timestamp.isoformat(),
                DEVICE_CLASS_SIGNAL_STRENGTH: event.SignalStrength,
                ATTR_BATTERY_LEVEL: event.BatteryLevel
            }

            _LOGGER.debug(data)

            if not event.MAC in entities:
                new_entity = WyzeSensor(data)
                entities[event.MAC] = new_entity
                add_entites([new_entity])
            else:
                entities[event.MAC]._data = data
                entities[event.MAC].schedule_update_ha_state()

    ws = wyzesense.Open(config[CONF_DEVICE], on_event)

    # Get bound sensors
    result = ws.List()
    _LOGGER.debug("%d Sensors Paired" % len(result))

    for mac in result:
        _LOGGER.debug("Registering Sensor Entity: %s" % mac)

        data = {
            ATTR_AVAILABLE: False,
            ATTR_MAC: mac,
            ATTR_STATE: 0,
            ATTR_DEVICE_CLASS: DEVICE_CLASS_MOTION
        }

        if not mac in entities:
            new_entity = WyzeSensor(data)
            entities[mac] = new_entity
            add_entites([new_entity])

    # Configure Destructor
    def on_shutdown(event):
        _LOGGER.debug("Closing connection to hub")
        ws.Stop()

    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, on_shutdown)

    # Configure Service
    def on_scan(call):
        ws.Scan()

    def on_remove(call):
        mac = call.data.get(ATTR_MAC).upper()
        ws.Delete(mac)
        toDelete = entities[mac]
        hass.add_job(toDelete.async_remove)
        del entities[mac]
        _LOGGER.debug("Removed Sensor Entity: %s" % mac)

    hass.services.register(DOMAIN, SERVICE_SCAN, on_scan, SERVICE_SCAN_SCHEMA)
    hass.services.register(DOMAIN, SERVICE_REMOVE, on_remove, SERVICE_REMOVE_SCHEMA)


class WyzeSensor(BinarySensorDevice):
    """Class to hold Hue Sensor basic info."""

    def __init__(self, data):
        """Initialize the sensor object."""
        _LOGGER.debug(data)
        self._data = data 

    @property
    def assumed_state(self):
        return not self._data[ATTR_AVAILABLE]
    
    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def unique_id(self):
        return self._data[ATTR_MAC]

    @property
    def is_on(self):
        """Return the state of the sensor."""
        return self._data[ATTR_STATE]

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return self._data[ATTR_DEVICE_CLASS] if self._data[ATTR_AVAILABLE] else None

    @property
    def device_state_attributes(self):
        """Attributes."""
        return {
            DEVICE_CLASS_SIGNAL_STRENGTH: self._data[DEVICE_CLASS_SIGNAL_STRENGTH],
            DEVICE_CLASS_TIMESTAMP: self._data[DEVICE_CLASS_TIMESTAMP],
            ATTR_BATTERY_LEVEL: self._data[ATTR_BATTERY_LEVEL],
            ATTR_MAC: self._data[ATTR_MAC]
        } if self._data[ATTR_AVAILABLE] else {ATTR_MAC: self._data[ATTR_MAC]}
