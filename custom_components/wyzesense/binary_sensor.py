""" 

wyzesense integration
v0.0.9

"""

from .wyzesense_custom import *
import logging
import voluptuous as vol
import json
import os.path

from os import path
from retry import retry
import subprocess

from homeassistant.const import CONF_FILENAME, CONF_DEVICE, \
    EVENT_HOMEASSISTANT_STOP, STATE_ON, STATE_OFF, ATTR_BATTERY_LEVEL, \
    ATTR_STATE, ATTR_DEVICE_CLASS, DEVICE_CLASS_TIMESTAMP

try:
    from homeassistant.components.binary_sensor import PLATFORM_SCHEMA, BinarySensorEntity, DEVICE_CLASS_MOTION, DEVICE_CLASS_DOOR
except ImportError:
    from homeassistant.components.binary_sensor import BinarySensorDevice as BinarySensorEntity, PLATFORM_SCHEMA, DEVICE_CLASS_MOTION, DEVICE_CLASS_DOOR

from homeassistant.helpers.restore_state import RestoreEntity

import homeassistant.helpers.config_validation as cv

DOMAIN = "wyzesense"

STORAGE = ".storage/wyzesense.json"

ATTR_MAC = "mac"
ATTR_RSSI = "rssi"
ATTR_AVAILABLE = "available"
CONF_INITIAL_STATE = "initial_state"
CONF_INVERT_STATE = "invert_state"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_DEVICE, default = "auto"): cv.string, 
    vol.Optional(CONF_INITIAL_STATE, default={}): vol.Schema({cv.string : vol.In(["on","off"])}),
    vol.Optional(CONF_INVERT_STATE, default=[]): vol.All(cv.ensure_list, [cv.string]),
})

SERVICE_SCAN = 'scan'
SERVICE_REMOVE = 'remove'

SERVICE_SCAN_SCHEMA = vol.Schema({})

SERVICE_REMOVE_SCHEMA = vol.Schema({
    vol.Required(ATTR_MAC): cv.string
})

_LOGGER = logging.getLogger(__name__)

def getStorage(hass):
    if not path.exists(hass.config.path(STORAGE)):
        return []
    with open(hass.config.path(STORAGE),'r') as f:
        return json.load(f)

def setStorage(hass,data):
    with open(hass.config.path(STORAGE),'w') as f:
        json.dump(data, f)

def findDongle():
    df = subprocess.check_output(["ls", "-la", "/sys/class/hidraw"]).decode('utf-8').lower()
    for l in df.split('\n'):
        if ("e024" in l and "1a86" in l):
            for w in l.split(' '):
                if ("hidraw" in w):
                    return "/dev/%s" % w

def setup_platform(hass, config, add_entites, discovery_info=None):
    if config[CONF_DEVICE].lower() == 'auto': 
        config[CONF_DEVICE] = findDongle()
    _LOGGER.debug("WYZESENSE v0.0.9")
    _LOGGER.debug("Attempting to open connection to hub at " + config[CONF_DEVICE])

    forced_initial_states = config[CONF_INITIAL_STATE]
    invert_states = config[CONF_INVERT_STATE]

    entities = {}

    def on_event(ws, event):
        if event.Type == 'state':
            (sensor_type, sensor_state, sensor_battery, sensor_signal) = event.Data
            data = {
                ATTR_AVAILABLE: True,
                ATTR_MAC: event.MAC,
                ATTR_STATE: 1 if sensor_state == "open" or sensor_state == "active" else 0,
                ATTR_DEVICE_CLASS: DEVICE_CLASS_MOTION if sensor_type == "motion" else DEVICE_CLASS_DOOR ,
                DEVICE_CLASS_TIMESTAMP: event.Timestamp.isoformat(),
                ATTR_RSSI: sensor_signal * -1,
                ATTR_BATTERY_LEVEL: sensor_battery
            }

            _LOGGER.debug(data)

            if not event.MAC in entities:
                new_entity = WyzeSensor(data)
                entities[event.MAC] = new_entity
                if entities[event.MAC]._invert_state:
                    entities[event.MAC]._data[ATTR_STATE] = 1 if entities[event.MAC]._data[ATTR_STATE] == 0 else 0
                add_entites([new_entity])
        
                storage = getStorage(hass)
                if event.MAC not in storage:
                    storage.append(event.MAC)
                setStorage(hass, storage)
                
            else:
                entities[event.MAC]._data = data
                if entities[event.MAC]._invert_state:
                    entities[event.MAC]._data[ATTR_STATE] = 1 if entities[event.MAC]._data[ATTR_STATE] == 0 else 0
                entities[event.MAC].schedule_update_ha_state()

    @retry(TimeoutError, tries=10, delay=1, logger=_LOGGER)
    def beginConn():
        return Open(config[CONF_DEVICE], on_event)

    ws = beginConn()

    storage = getStorage(hass)

    _LOGGER.debug("%d Sensors Loaded from storage" % len(storage))

    for mac in storage:
        _LOGGER.debug("Registering Sensor Entity: %s" % mac)

        mac = mac.strip()

        if not len(mac) == 8:
            _LOGGER.debug("Ignoring %s, Invalid length for MAC" % mac)
            continue

        initial_state = forced_initial_states.get(mac)
        if mac in invert_states:
            invert = True
        else:
            invert = False

        data = {
            ATTR_AVAILABLE: False,
            ATTR_MAC: mac,
            ATTR_STATE: 0
        }

        if not mac in entities:
            new_entity = WyzeSensor(data, should_restore = True, override_restore_state = initial_state, invert_state = invert)
            entities[mac] = new_entity
            add_entites([new_entity])

    # Configure Destructor
    def on_shutdown(event):
        _LOGGER.debug("Closing connection to hub")
        ws.Stop()

    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, on_shutdown)

    # Configure Service
    def on_scan(call):
        result = ws.Scan()
        if result:
            notification = "Sensor found and added as: binary_sensor.wyzesense_%s (unless you have customized the entity ID prior).<br/>To add more sensors, call wyzesense.scan again.<br/><br/>More Info: type=%d, version=%d" % result
            hass.components.persistent_notification.create(notification, DOMAIN)
            _LOGGER.debug(notification)
        else:
            notification = "Scan completed with no sensor found."
            hass.components.persistent_notification.create(notification, DOMAIN)
            _LOGGER.debug(notification)

    def on_remove(call):
        mac = call.data.get(ATTR_MAC).upper()
        if entities.get(mac):
            ws.Delete(mac)
            toDelete = entities[mac]
            hass.add_job(toDelete.async_remove)
            del entities[mac]

            storage = getStorage(hass)
            storage.remove(mac)
            setStorage(hass, storage)

            notification = "Successfully removed sensor: %s" % mac
            hass.components.persistent_notification.create(notification, DOMAIN)
            _LOGGER.debug(notification)
        else:
            notification = "No sensor with mac %s found to remove." % mac
            hass.components.persistent_notification.create(notification, DOMAIN)
            _LOGGER.debug(notification)

    hass.services.register(DOMAIN, SERVICE_SCAN, on_scan, SERVICE_SCAN_SCHEMA)
    hass.services.register(DOMAIN, SERVICE_REMOVE, on_remove, SERVICE_REMOVE_SCHEMA)


class WyzeSensor(BinarySensorEntity, RestoreEntity):
    """Class to hold Hue Sensor basic info."""

    def __init__(self, data, should_restore = False, override_restore_state = None, invert_state = False):
        """Initialize the sensor object."""
        _LOGGER.debug(data)
        self._data = data 
        self._should_restore = should_restore
        self._override_restore_state = override_restore_state
        self._invert_state = invert_state

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        
        if self._should_restore:

            last_state = await self.async_get_last_state()
            
            if last_state is not None:
                actual_state = last_state.state

                if self._override_restore_state is not None:
                    actual_state = self._override_restore_state

                self._data = {
                    ATTR_STATE: 1 if actual_state == "on" else 0,
                    ATTR_AVAILABLE: False,
                    **last_state.attributes
                }

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
        attributes = self._data.copy()
        del attributes[ATTR_STATE]
        del attributes[ATTR_AVAILABLE]

        return attributes
