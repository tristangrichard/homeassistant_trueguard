"""
Trueguard / Alarm object
"""
import logging
import time

_LOGGER = logging.getLogger(__name__)

DEVICE_SMART_HOME = "SMARTHOME"

SENSOR_TYPES_TO_IGNORE = ["Remote Controller", "Remote Keypad", "Keypad"]
SUPPORTED_VERSIONS = ["WV-1716", "GATE-01","GATE-02", "GATE-03", DEVICE_SMART_HOME]
UNAUTHORIZED_MESSAGES = ["Unauthorized", "Access Denied"]
GATE03_STATES_MAPPING = {'FULL ARM':'ARM','HOME ARM 1':'HOME','HOME ARM 2':'HOME','HOME ARM 3':'HOME','DISARM':'DISARM'}
GATE04_STATES_MAPPING = { 'FRAKOBLET': 'DISARM', 'DELTILKOBLING 1': 'HOME', 'FULDSIKRING': 'ARM' }

class UnauthorizedError(Exception):
    """
    Unauthorized Error
    """
    def __init__(self, value):
        super(self.__class__, self).__init__(value)
        self.value = value
    def __str__(self):
        return repr(self.value)

class VersionError(Exception):
    """
    Version Error
    """
    def __init__(self, value):
        super(self.__class__, self).__init__(value)
        self.value = value
    def __str__(self):
        return repr(self.value)

class EgardiaDevice(object):
    """
    Representation of an Trueguard Device
    """
    def __init__(self, host, port, username, password, state, version):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._status = state
        self._version = version.upper()
        self._sensors = {}
        self._last_sensor_refresh = 0.0
        self._sensor_refresh_interval_seconds = 1.0
        if self._version not in SUPPORTED_VERSIONS:
            raise VersionError('Trueguard device version '+self._version+' is unsupported.')
        else: 
            self._sensors = self.getsensors()
            self._last_sensor_refresh = time.time()
            self.update()

    def state(self):
        """Return _status"""
        return self._status

    def update(self):
        """Update the alarm status."""
        self._status = self.getstate()

    def statusunauthorized(self, text):
        """Check for unautorized messages in a given text."""
        for msg in UNAUTHORIZED_MESSAGES:
            if msg in text:
                return True
        return False

    def dorequestwithretry(self, mode, service, maxretries=1, payload=None):
        """Do a request and retry."""
        for i in range(maxretries+1):
            try:
                req = self.dorequest(mode, service, payload)
            except:
                raise
            statustext = req.text
            i = i + 1
            if not self.statusunauthorized(statustext):
                break
              
        if self.statusunauthorized(statustext):
            raise UnauthorizedError('Unable to login to system using the credentials provided')
        else:
            return statustext

    def getstate(self):
        """Get the status from the alarm panel"""
        import requests
        #Get status
        statustext = self.dorequestwithretry('get','panelCondGet')

        if self._version in ["GATE-01", "GATE-02"]:
            ind1 = statustext.find('mode_a1 : "')
            numcharstoskip = 11
        elif self._version in ["GATE-03", DEVICE_SMART_HOME]:
            ind1 = statustext.find('"mode_a1" : "')
            numcharstoskip = 13
        elif self._version == "WV-1716":
            ind1 = statustext.find('mode_st : "')
            numcharstoskip = 11
        statustext = statustext[ind1+numcharstoskip:]
        ind2 = statustext.find('"')
        status = statustext[:ind2]
        #Mapping GATE-03 states to supported values in HASS component
        if self._version == "GATE-03":
            status = GATE03_STATES_MAPPING.get(status.upper(), "UNKNOWN")
        if self._version == DEVICE_SMART_HOME:
            status = GATE04_STATES_MAPPING.get(status.upper(), "UNKNOWN")
        _LOGGER.debug("Trueguard alarm status: "+status)
        return status.upper()

    def getsensors(self):
        """Get the sensors and their states from the alarm panel"""
        import requests
        try:
            if self._version in ["WV-1716", "GATE-01"]:
                sensors = self.dorequestwithretry('get', 'sensorListGet')
            elif self._version in ["GATE-02", "GATE-03", DEVICE_SMART_HOME]:
                sensors = self.dorequestwithretry('get', 'deviceListGet')
            else:
                raise VersionError('Trueguard device version '+self._version+' is unsupported.')
        except:
            raise
       
        if self.statusunauthorized(sensors):
            raise UnauthorizedError('Unable to login to system using the credentials provided')
        elif 'is not defined' in sensors:
            raise VersionError('Unable to communicate with the device. Did you configure your version correctly?')
        else:
            sensord = self.parseJson(sensors)
            sensors = {}
            keyname = "no"
            typename = "type"
            if self._version in ["GATE-02", "GATE-03", DEVICE_SMART_HOME]:
                keyname = "id"
            if self._version == "GATE-03":
                typename = "type_f"
            if self._version in ["WV-1716", "GATE-02", "GATE-01"]:
                #Process GATE-01 and GATE-02 sensor json
                for sensor in sensord["senrows"]:
                    if sensor[typename] not in SENSOR_TYPES_TO_IGNORE:
                        #Change keyname from no to id to match GATE-02 and GATE-03
                        if self._version in ["WV-1716", "GATE-01"]:
                            newkeyname = "id"
                            sensor[newkeyname] = sensor.pop(keyname)
                            sensors[sensor[newkeyname]] = sensor
                        elif self._version in ["GATE-02"]:
                            sensors[sensor[keyname]] = sensor
                        #sensor[keyname]
                        #if sensor["type"] == "Door Contact":
                            #sensor["cond"]== "Open" || ""
                        #    k = 1
                        #if sensor["type"] == "IR Sensor":
                            #sensor[""]!= "" || ""
                        #    k = 2
            elif self._version in ["GATE-03", DEVICE_SMART_HOME]:
                #Process GATE-03 sensor json
                for sensor in sensord["senrows"]:
                    if sensor[typename] not in SENSOR_TYPES_TO_IGNORE:
                        #Change type_f key to type for GATE-03.
                        sensor["type"] = sensor.pop(typename)
                        sensors[sensor[keyname]]=sensor
            return sensors
        
    def _refresh_sensors_if_needed(self, force=False):
        """Refresh cached sensors if stale or forced."""
        now = time.time()
        if force or (now - self._last_sensor_refresh >= self._sensor_refresh_interval_seconds):
            self._sensors = self.getsensors()
            self._last_sensor_refresh = now

    def getsensor(self, sensorId):
        self._refresh_sensors_if_needed()
        if sensorId in self._sensors:
            return self._sensors[sensorId]
        else:
            return None
    def _parse_sensor_state(self, sensor):
        """Parse an on/off state from a sensor payload."""
        if sensor is None:
            return None

        status = str(sensor.get('status', '')).upper()
        cond = str(sensor.get('cond', ''))
        type_name = str(sensor.get('type_f', '')).strip().lower()
        raw_type = sensor.get('type')
        st_raw = sensor.get('st')

        try:
            st_value = int(st_raw)
        except (TypeError, ValueError):
            st_value = None

        try:
            sensor_type = int(raw_type)
        except (TypeError, ValueError):
            sensor_type = None

        if sensor_type is None and isinstance(raw_type, str):
            type_text = raw_type.strip().lower()
            if type_text in ['door contact', 'dørkontakt']:
                sensor_type = 4
            elif type_text in ['smoke alarm', 'røg alarm']:
                sensor_type = 11
            elif type_text in ['pir kamera', 'pir camera', 'ir', 'ir camera']:
                sensor_type = 27
            elif type_text in ['keypad', 'tastatur', 'remote keypad']:
                sensor_type = 37
            elif type_text in ['remote', 'remote controller', 'fjernbetjening']:
                sensor_type = 2
            elif type_text in ['sirene', 'siren']:
                sensor_type = 45

        if sensor_type is None and type_name:
            if type_name in ['door contact', 'dørkontakt']:
                sensor_type = 4
            elif type_name in ['smoke alarm', 'røg alarm']:
                sensor_type = 11
            elif type_name in ['pir kamera', 'pir camera', 'ir', 'ir camera']:
                sensor_type = 27
            elif type_name in ['keypad', 'tastatur', 'remote keypad']:
                sensor_type = 37
            elif type_name in ['remote', 'remote controller', 'fjernbetjening']:
                sensor_type = 2
            elif type_name in ['sirene', 'siren']:
                sensor_type = 45

        # Deterministic parsing for known types regardless of panel version.
        if sensor_type == 4:
            if status in ["DOOR OPEN", "LÅS OP", "OPEN"] or st_value == 3:
                return True
            if status in ["DOOR CLOSE", "LÅS", "CLOSED"] or st_value == 2:
                return False
            if st_value is not None:
                return st_value > 0
            return len(cond.strip()) > 0

        if sensor_type == 11:
            if st_value is not None:
                return st_value > 0
            if any(keyword in status for keyword in ["SMOKE", "RØG", "ALARM", "FIRE"]):
                return True
            return False

        if sensor_type == 27:
            if len(cond.strip()) > 0:
                return True
            if st_value is not None:
                return st_value > 0
            return any(keyword in status for keyword in ["MOTION", "TRIGGER", "ALARM"])

        if sensor_type in [2, 37, 45, 46]:
            if st_value is not None:
                return st_value > 0
            return False

        if self._version in ["WV-1716", "GATE-01", "GATE-02"]:
            if len(cond) > 0:
                # Return True when door is open or IR is triggered
                return True
            # Return False when door is closed or IR is not triggered
            return False

        if self._version in ["GATE-03", DEVICE_SMART_HOME]:
            # Door contact
            if sensor_type == 4:
                if status in ["DOOR OPEN", "LÅS OP", "OPEN"] or st_value == 3:
                    return True
                if status in ["DOOR CLOSE", "LÅS", "CLOSED"] or st_value == 2:
                    return False

            # Smoke alarm
            if sensor_type == 11:
                if st_value == 0:
                    return False
                if st_value is not None and st_value > 0:
                    return True
                if any(keyword in status for keyword in ["SMOKE", "RØG", "ALARM", "FIRE"]):
                    return True

            # PIR / camera motion
            if sensor_type == 27:
                if len(cond.strip()) > 0:
                    return True
                if st_value == 0:
                    return False
                if st_value is not None and st_value > 0:
                    return True
                if any(keyword in status for keyword in ["MOTION", "TRIGGER", "ALARM"]):
                    return True

            # Generic fallback for other SMARTHOME device types
            if st_value is not None:
                return st_value > 0
        if st_value is not None:
            return st_value > 0

        return None

    def getsensorstatefromsensor(self, sensor):
        """Get the current boolean state from a raw sensor payload."""
        return self._parse_sensor_state(sensor)

    def getsensorstate(self, sensorId):
        sensor = self.getsensor(sensorId)
        return self._parse_sensor_state(sensor)

    def dorequest(self, requesttype, action, payload=None):
        """Execute an request against the alarm panel"""
        import requests
        requesttype = requesttype.upper()
        _LOGGER.debug("Trueguard doRequest, type: "+requesttype+", url: "+self.buildurl()+action
                      +", payload: "+str(payload)+", auth=("+self._username+",****)")
        if requesttype == 'GET':
            return requests.get(self.buildurl()+action,
                                auth=(self._username, self._password), timeout=5)
        elif requesttype == 'POST':
            return requests.post(self.buildurl()+action, data=payload,
                                 auth=(self._username, self._password), timeout=5)
        else:
            return None

    def buildurl(self):
        """Build the url from host and port"""
        return 'http://'+self._host+':'+str(self._port)+'/action/'

    def alarm_disarm(self, code=None):
        """Send disarm command."""
        if self._version in ["GATE-01", "GATE-02"]:
            req = self.sendcondition(4)
        elif self._version in ["GATE-03", DEVICE_SMART_HOME]:
            req = self.sendcondition(0)
        elif self._version == "WV-1716":
            req = self.sendcondition(2)
        
        _LOGGER.info("Trueguard alarm disarming, result: "+req)

    def alarm_arm_home(self, code=None):
        """Send arm home command."""
        if self._version in ["WV-1716", "GATE-01", "GATE-02"]:
            req = self.sendcondition(1)
        elif self._version in ["GATE-03", DEVICE_SMART_HOME]:
            req = self.sendcondition(2)
        _LOGGER.info("Trueguard alarm arming home, result: "+req)

    def alarm_arm_away(self, code=None):
        """Send arm away command."""
        #ARM the alarm
        if self._version in ["WV-1716", "GATE-01", "GATE-02"]:
            req = self.sendcondition(0)
        elif self._version in ["GATE-03", DEVICE_SMART_HOME]:
            req = self.sendcondition(1)
        _LOGGER.info("Trueguard alarm arming away, result: "+req)

    def sendcondition(self, mode):
        "Change the condition of the panel"""
        import requests
        #Send payload to panelCondPost
        payload = {'area': '1', 'mode': mode}
        try:
            statustext = self.dorequestwithretry('POST', 'panelCondPost', 1, payload)
        except:
            raise
        ind1 = statustext.find('result : ')
        statustext = statustext[ind1+9:]
        ind2 = statustext.find(',')
        statustext = statustext[:ind2]
        return statustext

    def parseJson(self, crappy_json):
        import json
        import re
        crappy_json = crappy_json.replace("/*-secure-","")
        crappy_json = crappy_json.replace("*/","")
        crappy_json = crappy_json.replace('{	senrows : [','{"senrows":[')
        property_names_to_fix = ["no","type","type_f","area", "zone", "name", "attr", "cond", "cond_ok", "battery", "battery_ok", "tamp", "tamper", "tamper_ok", "bypass", "rssi", "status", "id","su"]
        for p in property_names_to_fix:
            crappy_json = crappy_json.replace(p+' :','"'+p+'":')
        data = json.loads(crappy_json, strict=False)
        return data
