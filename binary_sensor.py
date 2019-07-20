""" 

wyzesense integration

"""

import voluptuous as vol
import os
import time
import struct
import logging
import threading
import select
import queue
import datetime
import argparse
import asyncio

from homeassistant.const import CONF_FILENAME, CONF_DEVICE, EVENT_HOMEASSISTANT_STOP, STATE_ON, STATE_OFF, ATTR_BATTERY_LEVEL, ATTR_STATE, ATTR_DEVICE_CLASS, DEVICE_CLASS_SIGNAL_STRENGTH, DEVICE_CLASS_TIMESTAMP
from homeassistant.components.binary_sensor import PLATFORM_SCHEMA, BinarySensorDevice, DEVICE_CLASS_MOTION, DEVICE_CLASS_DOOR
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


def str_to_hex(s):
    if s:
        return " ".join(["{:02x}".format(x) for x in s])
    else:
        return "<None>"

TYPE_SYNC   = 0x43
TYPE_ASYNC  = 0x53

def MAKE_CMD(type, cmd):
    return (type << 8) | cmd

class Packet(object):
    _CMD_TIMEOUT = 5

    # Sync packets:
    # Commands initiated from host side
    CMD_GET_ENR               = MAKE_CMD(TYPE_SYNC, 0x02)
    CMD_GET_MAC               = MAKE_CMD(TYPE_SYNC, 0x04)
    CMD_GET_KEY               = MAKE_CMD(TYPE_SYNC, 0x06)
    CMD_INQUIRY               = MAKE_CMD(TYPE_SYNC, 0x27)

    # Async packets:
    ASYNC_ACK                 = MAKE_CMD(TYPE_ASYNC, 0xFF)

    # Commands initiated from dongle side
    CMD_FINISH_AUTH           = MAKE_CMD(TYPE_ASYNC, 0x14)
    CMD_GET_DONGLE_VERSION    = MAKE_CMD(TYPE_ASYNC, 0x16)
    CMD_EANBLE_SCAN           = MAKE_CMD(TYPE_ASYNC, 0x1C)
    CMD_GET_SENSOR_R1         = MAKE_CMD(TYPE_ASYNC, 0x21)
    CMD_VERIFY_SENSOR         = MAKE_CMD(TYPE_ASYNC, 0x23)
    CMD_DEL_SENSOR            = MAKE_CMD(TYPE_ASYNC, 0x25)
    CMD_GET_SENSOR_COUNT      = MAKE_CMD(TYPE_ASYNC, 0x2E)
    CMD_GET_SENSOR_LIST       = MAKE_CMD(TYPE_ASYNC, 0x30)

    # Notifications initiated from dongle side
    NOTIFY_SENSOR_ALARM       = MAKE_CMD(TYPE_ASYNC, 0x19)
    NOTIFY_SENSOR_SCAN        = MAKE_CMD(TYPE_ASYNC, 0x20)
    NOITFY_SYNC_TIME          = MAKE_CMD(TYPE_ASYNC, 0x32)
    NOTIFY_EVENT_LOG          = MAKE_CMD(TYPE_ASYNC, 0x35)

    def __init__(self, cmd, payload = b""):
        self._cmd = cmd
        if self._cmd == self.ASYNC_ACK:
            assert isinstance(payload, int)
        else:
            assert isinstance(payload, bytes)
        self._payload = payload

    def __str__(self):
        if self._cmd == self.ASYNC_ACK:
            return "Packet: Cmd=%04X, Payload=ACK(%04X)" % (self._cmd, self._payload)
        else:
            return "Packet: Cmd=%04X, Payload=%s" % (self._cmd, str_to_hex(self._payload))

    @property
    def Length(self):
        if self._cmd == self.ASYNC_ACK:
            return 7
        else:
            return len(self._payload) + 7

    @property
    def Cmd(self):
        return self._cmd
    
    @property
    def Payload(self):
        return self._payload

    def Send(self, fd):
        pkt = struct.pack(">HB", 0xAA55, self._cmd >> 8)
        if self._cmd == self.ASYNC_ACK:
            pkt += struct.pack("BB", (self._payload & 0xFF), self._cmd & 0xFF)
        else:
            pkt += struct.pack("BB", len(self._payload) + 3, self._cmd & 0xFF)
            if self._payload:
                pkt += self._payload
        checksum = sum([c for c in pkt]) & 0xFFFF
        pkt += struct.pack(">H", checksum)
        _LOGGER.debug("Sending: %s", str_to_hex(pkt))
        ss = os.write(fd, pkt)
        assert ss == len(pkt)

    @classmethod
    def Parse(cls, s):
        if len(s) < 5:
            _LOGGER.error("Invalid packet: %s", str_to_hex(s))
            _LOGGER.error("Invalid packet length: %d", len(s))
            return None

        magic, cmd_type, b2, cmd_id = struct.unpack_from(">HBBB", s)
        if magic != 0x55AA and magic != 0xAA55:
            _LOGGER.error("Invalid packet: %s", str_to_hex(s))
            _LOGGER.error("Invalid packet magic: %4X", magic)
            return None

        cmd = MAKE_CMD(cmd_type, cmd_id)
        if cmd == cls.ASYNC_ACK:
            assert len(s) >= 7
            s = s[:7]
            payload = MAKE_CMD(cmd_type, b2)
        else:
            assert len(s) >= b2 + 4
            s = s[: b2 + 4]
            payload = s[5:-2]

        cs_remote = (s[-2] << 8) | s[-1]
        cs_local = sum([x for x in s[:-2]])
        if cs_remote != cs_local:
            _LOGGER.error("Invalid packet: %s", str_to_hex(s))
            _LOGGER.error("Mismatched checksum, remote=%04X, local=%04X", cs_remote, cs_local)
            return None

        return cls(cmd, payload)

    @classmethod
    def GetVersion(cls):
        return cls(cls.CMD_GET_DONGLE_VERSION)
    
    @classmethod
    def Inquiry(cls):
        return cls(cls.CMD_INQUIRY)
    
    @classmethod
    def GetEnr(cls, r):
        return cls(cls.CMD_GET_ENR, r)

    @classmethod
    def GetMAC(cls):
        return cls(cls.CMD_GET_MAC)
        
    @classmethod
    def GetKey(cls):
        return cls(cls.CMD_GET_KEY)

    @classmethod
    def EnableScan(cls, start):
        assert isinstance(start, bool)
        return cls(cls.CMD_EANBLE_SCAN, b"\x01" if start else b"\x00")

    @classmethod
    def GetSensorCount(cls):
        return cls(cls.CMD_GET_SENSOR_COUNT)

    @classmethod
    def GetSensorList(cls, count):
        assert count <= 0xFF
        return cls(cls.CMD_GET_SENSOR_LIST, struct.pack("B", count))

    @classmethod
    def FinishAuth(cls):
        return cls(cls.CMD_FINISH_AUTH, b"\xFF")

    @classmethod
    def DelSensor(cls, mac):
        assert len(mac) == 8
        assert isinstance(mac, bytes)
        return cls(cls.CMD_DEL_SENSOR, mac)
    
    @classmethod
    def GetSensorR1(cls, mac, r):
        assert len(r) == 16
        assert isinstance(r, bytes)
        assert len(mac) == 8
        assert isinstance(mac, bytes)
        return cls(cls.CMD_GET_SENSOR_R1, mac + r)

    @classmethod
    def VerifySensor(cls, mac):
        assert len(mac) == 8
        assert isinstance(mac, bytes)
        return cls(cls.CMD_VERIFY_SENSOR, mac + b"\xFF\x04")

    @classmethod
    def SyncTimeAck(cls):
        return cls(cls.NOITFY_SYNC_TIME + 1, struct.pack(">Q", int(time.time() * 1000)))

    @classmethod
    def AsyncAck(cls, cmd):
        assert (cmd >> 0x8) == TYPE_ASYNC
        return cls(cls.ASYNC_ACK, cmd)

class WyzeSense(object):
    _CMD_TIMEOUT = 2

    class CmdContext():
        def __init__(self, **kwargs):
            for key in kwargs:
                setattr(self, key, kwargs[key])

    def _OnSensorAlarm(self, pkt):
        ts, alarm_type, mac = struct.unpack_from(">QB8s", pkt.Payload)
        tm = datetime.datetime.fromtimestamp(ts/1000.0)
        _LOGGER.info(
            "ALARM: time=%s, mac=%s, type=%02X, data=%s",
            tm.isoformat(), mac, alarm_type, str_to_hex(pkt.Payload[17:]))

    def _OnSensorFound(self, pkt):
        pass
    
    def _OnSyncTime(self, pkt):
        self._SendPacket(Packet.SyncTimeAck())

    def _OnEventLog(self, pkt):
        assert len(pkt.Payload) >= 9
        ts, msg_len = struct.unpack_from(">QB", pkt.Payload)
        # assert msg_len + 8 == len(pkt.Payload)
        tm = datetime.datetime.fromtimestamp(ts/1000.0)
        msg = pkt.Payload[9:]
        _LOGGER.info("LOG: time=%s, data=%s", tm.isoformat(), str_to_hex(msg))

    def __init__(self, fd):
        self.__lock = threading.Lock()
        self.__fd = fd
        self.__exit_event = threading.Event()
        self.__thread = threading.Thread(target = self._Worker)

        self.__handlers = {
            Packet.NOITFY_SYNC_TIME: self._OnSyncTime,
            Packet.NOTIFY_SENSOR_ALARM:  self._OnSensorAlarm,
            Packet.NOTIFY_EVENT_LOG: self._OnEventLog,
            "NOTIFY_SENSOR_FOUND": self._OnSensorFound
        }

        self.__thread.start()

    def _ReadRawHID(self):
        try:
            s = os.read(self.__fd, 0x40)
        except OSError:
            time.sleep(0.1)
            return b""

        if not s:
            _LOGGER.info("Nothing read")
            return b""

        length = s[0]
        assert length > 0
        if length > 0x3F:
            length = 0x3F

        #_LOGGER.debug("Raw HID packet: %s", str_to_hex(s))
        assert len(s) >= length + 1
        return s[1: 1 + length]

    def _SetHandler(self, cmd, handler):
        with self.__lock:
            oldHandler = self.__handlers.pop(cmd, None)
            if handler:
                self.__handlers[cmd] = handler
        return oldHandler

    def _SendPacket(self, pkt):
        _LOGGER.debug("===> Sending: %s", str(pkt))
        pkt.Send(self.__fd)

    def _DefaultHandler(self, pkt):
        pass

    def _HandlePacket(self, pkt):
        _LOGGER.debug("<=== Received: %s", str(pkt))
        with self.__lock:
            handler = self.__handlers.get(pkt.Cmd, self._DefaultHandler)
        
        if (pkt.Cmd >> 8) == TYPE_ASYNC and pkt.Cmd != Packet.ASYNC_ACK:
            #_LOGGER.info("Sending ACK packet for cmd %04X", pkt.Cmd)
            self._SendPacket(Packet.AsyncAck(pkt.Cmd))
        handler(pkt)

    def _Worker(self):
        s = b""
        while True:
            if self.__exit_event.isSet():
                break

            #if s:
            #    _LOGGER.info("Incoming buffer: %s", str_to_hex(s))
            start = s.find(b"\x55\xAA")
            if start == -1:
                s = self._ReadRawHID()
                continue

            s = s[start:]
            _LOGGER.debug("Trying to parse: %s", str_to_hex(s))
            pkt = Packet.Parse(s)
            if not pkt:
                s = s[2:]
                continue

            _LOGGER.debug("Received: %s", str_to_hex(s[:pkt.Length]))
            s = s[pkt.Length:]
            self._HandlePacket(pkt)

    def _DoCommand(self, pkt, handler, timeout=_CMD_TIMEOUT):
        e = threading.Event()
        oldHandler = self._SetHandler(pkt.Cmd + 1, lambda pkt: handler(pkt, e))
        self._SendPacket(pkt)
        result = e.wait(timeout)
        self._SetHandler(pkt.Cmd + 1, oldHandler)

        return result

    def _DoSimpleCommand(self, pkt, timeout=_CMD_TIMEOUT):
        ctx = self.CmdContext(result = None)

        def cmd_handler(pkt, e):
            ctx.result = pkt
            e.set()

        self._DoCommand(pkt, cmd_handler, timeout)
        return ctx.result

    def _Inquiry(self):
        _LOGGER.debug("Start Inquiry...")
        resp = self._DoSimpleCommand(Packet.Inquiry())
        if not resp:
            _LOGGER.debug("Inquiry timed out...")
            return None

        assert len(resp.Payload) == 1
        result = resp.Payload[0]
        _LOGGER.debug("Inquiry returns %d", result)
        return result

    def _GetEnr(self, r):
        _LOGGER.debug("Start GetEnr...")
        assert len(r) == 4
        assert all(isinstance(x,  int) for x in r)
        r_string = struct.pack("<LLLL", *r)

        resp = self._DoSimpleCommand(Packet.GetEnr(r_string))
        if not resp:
            _LOGGER.debug("GetEnr timed out...")
            return None

        assert len(resp.Payload) == 16
        _LOGGER.debug("GetEnr returns %s", str_to_hex(resp.Payload))
        return resp.Payload

    def _GetMac(self):
        _LOGGER.debug("Start GetMAC...")
        resp = self._DoSimpleCommand(Packet.GetMAC())
        if not resp:
            _LOGGER.debug("GetMac timed out...")
            return None

        assert len(resp.Payload) == 8
        _LOGGER.debug("GetMAC returns %s", resp.Payload)
        return resp.Payload
    
    def _GetKey(self):
        _LOGGER.debug("Start GetKey...")
        resp = self._DoSimpleCommand(Packet.GetKey())
        if not resp:
            _LOGGER.debug("GetKey timed out...")
            return None

        assert len(resp.Payload) == 16
        _LOGGER.debug("GetKey returns %s", resp.Payload)
        return resp.Payload
    
    def _GetVersion(self):
        _LOGGER.debug("Start GetVersion...")
        resp = self._DoSimpleCommand(Packet.GetVersion())
        if not resp:
            _LOGGER.debug("GetVersion timed out...")
            return None

        _LOGGER.debug("GetVersion returns %s", resp.Payload)
        return resp.Payload

    def _GetSensors(self):
        _LOGGER.debug("Start GetSensors...")

        resp = self._DoSimpleCommand(Packet.GetSensorCount())
        if not resp:
            _LOGGER.debug("GetSensorCount timed out...")
            return None

        assert len(resp.Payload) == 1
        count = resp.Payload[0]

        ctx = self.CmdContext(count=count, index=0, sensors=[])
        if count > 0:
            _LOGGER.debug("%d sensors reported, waiting for each one to report...", count)
            def cmd_handler(pkt, e):
                assert len(pkt.Payload) == 8
                _LOGGER.debug("Sensor %d/%d, MAC:%s", ctx.index + 1, ctx.count, pkt.Payload)
                self.__handlers.get("NOTIFY_SENSOR_FOUND")(pkt)

                ctx.sensors.append(pkt.Payload)
                ctx.index += 1
                if ctx.index == ctx.count:
                    e.set()

            if not self._DoCommand(Packet.GetSensorList(count), cmd_handler, timeout=self._CMD_TIMEOUT * count):
                _LOGGER.debug("GetSensorList timed out...")
                return None
        else:
            _LOGGER.debug("No sensors bond yet...")
        return ctx.sensors

    def _FinishAuth(self):
        resp = self._DoSimpleCommand(Packet.FinishAuth())
        if not resp:
            _LOGGER.debug("FinishAuth timed out...")
            return False
        
        return True

    def Start(self):
        res = self._Inquiry()
        if not res:
            _LOGGER.debug("Inquiry failed")
            return False

        self.ENR = self._GetEnr([0x30303030] * 4)
        if not self.ENR:
            _LOGGER.debug("GetEnr failed")
            return False
        
        self.MAC = self._GetMac()
        if not self.MAC:
            _LOGGER.debug("GetMAC failed")
            return False
        _LOGGER.debug("Dongle MAC is [%s]", self.MAC)

        res = self._GetVersion()
        if not res:
            _LOGGER.debug("GetVersion failed")
            return False
        self.Version = res
        _LOGGER.debug("Dongle version: %s", self.Version)

        res = self._FinishAuth()
        if not res:
            _LOGGER.debug("FinishAuth failed")
            return False
        
        sensors = self._GetSensors()
#         for x in sensors:
#             _LOGGER.debug("Sensor found: %s", x)

    def Stop(self):
        self.__exit_event.set()
        os.close(self.__fd)
        self.__fd = None
        self.__thread.join()

    def SensorDiscover(self, timeout=60):
        _LOGGER.debug("Start Scan...")

        ctx = self.CmdContext(evt=threading.Event(), result=None)
        def scan_handler(pkt):
            assert len(pkt.Payload) == 11
            ctx.result = (pkt.Payload[1:9], pkt.Payload[9], pkt.Payload[10])
            ctx.evt.set()
        
        oldHandler = self._SetHandler(Packet.NOTIFY_SENSOR_SCAN, scan_handler)
        res = self._DoSimpleCommand(Packet.EnableScan(True))
        if not res:
            _LOGGER.debug("EnableScan timed out...")
            return None

        if ctx.evt.wait(timeout):
            s_mac, s_type, s_ver = ctx.result
            _LOGGER.debug("Sensor found: mac=[%s], type=%d, version=%d", s_mac, s_type, s_ver)
            res = self._DoSimpleCommand(Packet.GetSensorR1(s_mac, b'Ok5HPNQ4lf77u754'))
            if not res:
                _LOGGER.debug("GetSensorR1 timeout...")
        else:
            _LOGGER.debug("Sensor discovery timeout...")

        res = self._DoSimpleCommand(Packet.EnableScan(False))
        if not res:
            _LOGGER.debug("EnableScan timedout...")

        if ctx.result:
            s_mac, s_type, s_ver = ctx.result
            res = self._DoSimpleCommand(Packet.VerifySensor(s_mac))
            if not res:
                _LOGGER.debug("VerifySensor timeout...")

    def SensorDelete(self, mac):
        resp = self._DoSimpleCommand(Packet.DelSensor(mac))
        if not resp:
            _LOGGER.debug("CmdDelSensor timed out...")
            return False

        _LOGGER.debug("CmdDelSensor returns %s", str_to_hex(resp.Payload))
        assert len(resp.Payload) == 9
        ack_mac = resp.Payload[:8]
        ack_code = resp.Payload[8]
        if ack_mac != mac:
            _LOGGER.debug("CmdDelSensor: MAC mismatch, requested:%s, returned:%s", mac, ack_mac)
            return False

        if ack_code != 0xFF:
            _LOGGER.debug("CmdDelSensor: Unexpected ACK code: %02X", ack_code)
            return False

        _LOGGER.debug("CmdDelSensor: %s deleted", mac)
        return True

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):

    entities = {}

    fd = os.open(config[CONF_DEVICE], os.O_RDWR | os.O_NONBLOCK)

    try:
        gateway = WyzeSense(fd)

        def _sensor_found(pkt):
            mac = struct.unpack_from(">8s", pkt.Payload)[0]

            data = {
                ATTR_MAC: mac,
                ATTR_AVAILABLE: False,
                ATTR_STATE: 0,
                ATTR_DEVICE_CLASS: DEVICE_CLASS_MOTION
            }

            if not mac in entities:
                new_entity = WyzeSensor(data)
                entities[mac] = new_entity
                hass.async_add_job(async_add_entities, [entities[mac]])

        def _sensor_alarm(pkt):
            ts, alarm_type, mac, sensor_type, unk3, battery, unk, unk2, state, counter, signal_strength = struct.unpack_from(">QB8sBBBBBBHB", pkt.Payload)
            tm = datetime.datetime.fromtimestamp(ts/1000.0)
            _LOGGER.info(
                "ALARM: time=%s, mac=%s, alarm_type=%s, sensor_type=%s, state=%s, signal_strength=%s, battery=%s",
                tm.isoformat(), mac, alarm_type, sensor_type, state, signal_strength, battery)

            if alarm_type == 162 and (state == 0 or state == 1):
                data = {
                    ATTR_MAC: mac,
                    ATTR_AVAILABLE: True,
                    ATTR_STATE: state,
                    ATTR_DEVICE_CLASS: DEVICE_CLASS_MOTION if sensor_type == 2 else DEVICE_CLASS_DOOR,
                    DEVICE_CLASS_TIMESTAMP: tm.isoformat(),
                    DEVICE_CLASS_SIGNAL_STRENGTH: signal_strength,
                    ATTR_BATTERY_LEVEL: battery
                }

                if not mac in entities:
                    new_entity = WyzeSensor(data)
                    entities[mac] = new_entity
                    hass.async_add_job(async_add_entities, [entities[mac]])
                else:
                    entities[mac]._data = data
                    hass.async_add_job(entities[mac].async_schedule_update_ha_state)

        def _on_scan(call):
            gateway.SensorDiscover()

        def _on_remove(call):
            mac = call.data.get(ATTR_MAC).encode('utf-8')
            gateway.SensorDelete(mac)
            toDelete = entities[mac]
            hass.async_add_job(toDelete.async_remove)
            del entities[mac]

        gateway._SetHandler("NOTIFY_SENSOR_FOUND", _sensor_found)
        gateway._SetHandler(Packet.NOTIFY_SENSOR_ALARM, _sensor_alarm)
        gateway.Start()

        hass.services.async_register(DOMAIN, SERVICE_SCAN, _on_scan, SERVICE_SCAN_SCHEMA)
        hass.services.async_register(DOMAIN, SERVICE_REMOVE, _on_remove, SERVICE_REMOVE_SCHEMA)

    except Exception as e:
        _LOGGER.exception(e)
        return False

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
        """Return the name of the sensor."""
        return self._data[ATTR_MAC].decode('utf-8')

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._data[ATTR_MAC].decode('utf-8')

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
            ATTR_BATTERY_LEVEL: self._data[ATTR_BATTERY_LEVEL]
        } if self._data[ATTR_AVAILABLE] else {}

    
