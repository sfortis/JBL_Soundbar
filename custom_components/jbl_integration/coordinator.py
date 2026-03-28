"""Coordinator for JBL integration."""
import asyncio
import aiohttp
from aiohttp import web
import json
import logging
import urllib3
import ssl
import certifi
import socket
from datetime import timedelta
from xml.etree import ElementTree as ET
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_UUID, CONF_ADDRESS, CONF_SCAN_INTERVAL
from homeassistant.exceptions import ConfigEntryNotReady
from .const import DOMAIN

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LOGGER = logging.getLogger(__name__)

HTTPS_HEADERS = {"Accept-Encoding": "gzip"}


class Coordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    # UPnP tag -> coordinator data key mapping
    _UPNP_TAG_MAP = {
        "TransportState": "transport_state",
        "CurrentTrackDuration": "track_duration",
        "CurrentMediaDuration": "track_duration",
    }

    def __init__(self, address, scan_interval, hass=None, entry=None):
        """Initialize the coordinator."""
        self.address = address
        self.pollingRate = scan_interval
        self.data = {}
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        self.sslcontext = ssl_context

        self._upnp_poll_count = 0

        if hass is not None and entry is not None:
            self._entry = entry
            self.hass = hass
            super().__init__(
                hass,
                _LOGGER,
                name="JBL Sensor",
                update_method=self._async_update_data,
                update_interval=timedelta(seconds=int(scan_interval)),
            )

    # --- Generic HTTP helpers ---

    async def _https_get(self, command):
        """Send a GET command to the HTTPS API and return parsed JSON."""
        url = f"https://{self.address}/httpapi.asp?command={command}"
        async with aiohttp.ClientSession() as session:
            try:
                async with asyncio.timeout(10):
                    async with session.get(url, headers=HTTPS_HEADERS, ssl=self.sslcontext) as response:
                        if response.status == 200:
                            response_text = await response.text()
                            _LOGGER.debug("%s Response: %s", command, response_text)
                            return json.loads(response_text)
                        else:
                            _LOGGER.error("Failed to get %s: %s", command, response.status)
                            return {}
            except Exception as e:
                _LOGGER.error("Error getting %s: %s", command, str(e))
                return {}

    async def _https_post(self, payload):
        """Send a POST command to the HTTPS API."""
        url = f"https://{self.address}/httpapi.asp"
        async with aiohttp.ClientSession() as session:
            try:
                async with asyncio.timeout(10):
                    async with session.post(url, headers=HTTPS_HEADERS, data=payload, ssl=self.sslcontext) as response:
                        if response.status != 200:
                            _LOGGER.error("Failed to post command: %s", response.status)
            except Exception as e:
                _LOGGER.error("Error posting command: %s", str(e))

    async def _soap_request(self, port, control_url, action, service, payload_xml):
        """Send a SOAP request and return the response text."""
        url = f"http://{self.address}:{port}{control_url}"
        headers = {
            "Content-type": 'text/xml;charset="utf-8"',
            "Soapaction": f'"{service}#{action}"',
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with asyncio.timeout(10):
                    async with session.post(url, headers=headers, data=payload_xml) as response:
                        if response.status == 200:
                            return await response.text()
                        else:
                            _LOGGER.error("SOAP %s failed: %s", action, response.status)
                            return None
            except Exception as e:
                _LOGGER.error("SOAP %s error: %s", action, str(e))
                return None

    # --- UPnP event subscription ---

    async def _setup_upnp_listener(self):
        """Set up a small HTTP server to receive UPnP event callbacks."""
        self._upnp_runner = None
        self._upnp_sid = None
        self._upnp_port = None

        try:
            local_ip = await self.hass.async_add_executor_job(self._get_local_ip)

            app = web.Application()
            app.router.add_route("NOTIFY", "/upnp/callback", self._handle_upnp_event)
            self._upnp_runner = web.AppRunner(app)
            await self._upnp_runner.setup()
            site = web.TCPSite(self._upnp_runner, "0.0.0.0", 0)
            await site.start()
            sockets = site._server.sockets
            self._upnp_port = sockets[0].getsockname()[1] if sockets else 0
            self._callback_url = f"http://{local_ip}:{self._upnp_port}/upnp/callback"
            _LOGGER.debug("UPnP event listener started on port %s", self._upnp_port)

            await self._subscribe_upnp_events()
        except Exception as e:
            _LOGGER.warning("Failed to start UPnP event listener: %s", str(e))
            await self._stop_upnp_listener()

    def _get_local_ip(self):
        """Get the local IP address that can reach the soundbar."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((self.address, 59152))
            return s.getsockname()[0]
        finally:
            s.close()

    async def _subscribe_upnp_events(self):
        """Subscribe to AVTransport UPnP events."""
        url = f"http://{self.address}:59152/upnp/event/rendertransport1"
        headers = {
            "CALLBACK": f"<{self._callback_url}>",
            "NT": "upnp:event",
            "TIMEOUT": "Second-300",
        }
        try:
            async with aiohttp.ClientSession() as session:
              async with asyncio.timeout(5):
                async with session.request("SUBSCRIBE", url, headers=headers) as resp:
                    if resp.status == 200:
                        self._upnp_sid = resp.headers.get("SID")
                        self._upnp_poll_count = 0
                        _LOGGER.debug("UPnP subscribed, SID: %s", self._upnp_sid)
                    else:
                        _LOGGER.warning("UPnP subscribe failed: %s", resp.status)
        except Exception as e:
            _LOGGER.warning("UPnP subscribe error: %s", str(e))

    async def _renew_upnp_subscription(self):
        """Renew the UPnP subscription using the existing SID."""
        if not self._upnp_sid:
            await self._subscribe_upnp_events()
            return
        url = f"http://{self.address}:59152/upnp/event/rendertransport1"
        headers = {
            "SID": self._upnp_sid,
            "TIMEOUT": "Second-300",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with asyncio.timeout(5):
                    async with session.request("SUBSCRIBE", url, headers=headers) as resp:
                        if resp.status == 200:
                            self._upnp_poll_count = 0
                            _LOGGER.debug("UPnP subscription renewed")
                        else:
                            _LOGGER.warning("UPnP renew failed: %s, resubscribing", resp.status)
                            self._upnp_sid = None
                            await self._subscribe_upnp_events()
        except Exception as e:
            _LOGGER.warning("UPnP renew error: %s, resubscribing", str(e))
            self._upnp_sid = None
            await self._subscribe_upnp_events()

    async def _handle_upnp_event(self, request):
        """Handle incoming UPnP NOTIFY events."""
        import re

        event_sid = request.headers.get("SID", "")
        if self._upnp_sid and event_sid != self._upnp_sid:
            return web.Response(status=412, text="SID mismatch")

        try:
            body = await request.text()
            _LOGGER.debug("UPnP event received: %s", body[:500])

            # Use regex to extract values from the XML/HTML-encoded body
            # This avoids XML namespace parsing issues entirely
            updated = False
            for upnp_tag, data_key in self._UPNP_TAG_MAP.items():
                match = re.search(
                    rf'&lt;{upnp_tag}\s+val=&quot;([^&]*)&quot;', body
                )
                if not match:
                    match = re.search(
                        rf'<{upnp_tag}\s+val="([^"]*)"', body
                    )
                if match:
                    val = match.group(1)
                    if val != self.data.get(data_key):
                        self.data[data_key] = val
                        updated = True
                        _LOGGER.debug("UPnP instant update: %s = %s", data_key, val)

            if updated:
                self.async_set_updated_data(dict(self.data))

        except Exception as e:
            _LOGGER.warning("Error handling UPnP event: %s", str(e))

        return web.Response(text="OK")

    async def _stop_upnp_listener(self):
        """Stop the UPnP event listener and unsubscribe."""
        if self._upnp_sid:
            try:
                url = f"http://{self.address}:59152/upnp/event/rendertransport1"
                headers = {"SID": self._upnp_sid}
                async with aiohttp.ClientSession() as session:
                    async with asyncio.timeout(3):
                        await session.request("UNSUBSCRIBE", url, headers=headers)
            except Exception:
                pass
            self._upnp_sid = None

        if self._upnp_runner:
            await self._upnp_runner.cleanup()
            self._upnp_runner = None
            _LOGGER.debug("UPnP event listener stopped")

    # --- Device setup ---

    async def _SetupDeviceInfo(self):
        """Set up SSL certs and fetch device info."""
        cert_path = self.hass.config.path("custom_components/jbl_integration/Cert.pem")
        key_path = self.hass.config.path("custom_components/jbl_integration/Key.pem")
        await self.hass.async_add_executor_job(
            self.sslcontext.load_cert_chain, cert_path, key_path
        )

        device_info = await self.getDeviceInfo()
        device_Type = await self.getDeviceType()

        mac_address = device_info.get("wlan0_mac", "unknown_mac")
        uuid = device_info.get("uuid", "unknown_uuid")
        device_name = device_info.get("name", "Unknow_name")
        serial_number = device_info.get("serial_number", "unknown_serial")
        firmware_version = device_info.get("firmware", "unknown_firmware")
        model = device_Type.get("hm_product_name", "unknown_product")
        hw_version = device_Type.get("hardware", "unknown_hardware")

        self._device_info = {
            "identifiers": {
                (DOMAIN, self._entry.entry_id),
                (DOMAIN, mac_address, uuid),
                (DOMAIN, str(uuid).replace("-", "")),
                (DOMAIN, self.address),
            },
            "name": device_name,
            "manufacturer": "HARMAN International Industries",
            "model": model,
            "hw_version": hw_version,
            "sw_version": firmware_version,
            "serial_number": serial_number,
        }
        try:
            self._newFirmware = int(firmware_version.split('.')[0]) > 24 or int(firmware_version.split('.')[2]) > 31
            _LOGGER.debug("JBL one 3.0 Detected" if self._newFirmware else "Older firmware then JBL one 3.0")
        except Exception:
            self._newFirmware = False

        await self._detect_capabilities()

    async def _detect_capabilities(self):
        """Probe the device to determine which features are supported."""
        # Map: capability name -> (API command, required response key)
        # If the command returns "unknown command" or the key is missing, the feature is not supported
        probes = {
            "atmos": ("getAtmosLevel", "atmos_level"),
            "rear_speakers": ("getRearSpeakerStatus", "rears"),
            "smart_mode": ("getSmartMode", "status"),
            "night_mode": ("getPersonalListeningMode", "status"),
            "pure_voice": ("getPureVoiceState", "purevoice_state"),
            "calibration": ("getCalibrationStatus", None),
            "hdmi": ("getHdmiStatus", None),
            "bass_boost": ("getBassBoostStatus", None),
        }
        self._capabilities = {}
        for cap, (command, required_key) in probes.items():
            response = await self._https_get(command)
            if not response:
                self._capabilities[cap] = False
            elif required_key:
                self._capabilities[cap] = required_key in response
            else:
                self._capabilities[cap] = True
            _LOGGER.debug("Capability %s: %s", cap, self._capabilities[cap])

    def has_capability(self, capability):
        """Check if the device supports a specific capability."""
        return self._capabilities.get(capability, False)

    @property
    def device_info(self):
        """Return device information about this entity."""
        return self._device_info

    @property
    def newFirmware(self):
        """Return if the JBL is part of the JBL one 3.0 software."""
        return self._newFirmware

    # --- Commands ---

    async def _send_command(self, command):
        """Send a key press command to the device."""
        payload = f'command=sendAppController&payload={{"key_pressed": "{command}"}}'
        await self._https_post(payload)

    # --- Polling ---

    async def _async_update_data(self):
        # Renew UPnP subscription every ~40 polls (~200 sec with 5 sec interval)
        self._upnp_poll_count += 1
        if self._upnp_poll_count >= 40:
            await self._renew_upnp_subscription()

        combined_data = {
            **await self.requestInfo(),
            **await self._getEQData(),
            **await self.getNightMode(),
            **await self.getRearSpeaker(),
            **await self.getSmartMode(),
            **await self.getPureVoice(),
            **await self.getSleepTimer(),
            **await self.getNetworkInfo(),
        }

        if self.data is None:
            self.data = {}

        self.data.update(combined_data)
        return combined_data

    # --- Device info fetchers ---

    async def getDeviceInfo(self):
        """Fetch device info from the HTTPS API."""
        response_json = await self._https_get("getDeviceInfo")
        return response_json.get("device_info", response_json)

    async def getDeviceType(self):
        """Fetch device type via UPnP SOAP."""
        payload_xml = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
        <s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
        <s:Body>
        <u:GetControlDeviceInfo xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">
        <InstanceID>0</InstanceID></u:GetControlDeviceInfo>
        </s:Body></s:Envelope>"""

        response_text = await self._soap_request(
            59152, "/upnp/control/rendercontrol1",
            "GetControlDeviceInfo", "urn:schemas-upnp-org:service:RenderingControl:1",
            payload_xml,
        )
        if response_text is None:
            raise ConfigEntryNotReady(f"Cannot connect to {self.address}")

        namespace = {
            's': 'http://schemas.xmlsoap.org/soap/envelope/',
            'u': 'urn:schemas-upnp-org:service:RenderingControl:1',
        }
        root = ET.fromstring(response_text)
        status_element = root.find('.//u:GetControlDeviceInfoResponse/Status', namespace)
        if status_element is not None:
            return json.loads(status_element.text)
        return {}

    async def requestInfo(self):
        """Fetch transport info via UPnP SOAP."""
        payload_xml = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
        <s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
          <s:Body>
            <u:GetInfoEx xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
              <InstanceID>0</InstanceID>
            </u:GetInfoEx>
          </s:Body>
        </s:Envelope>"""

        response_text = await self._soap_request(
            59152, "/upnp/control/rendertransport1",
            "GetInfoEx", "urn:schemas-upnp-org:service:AVTransport:1",
            payload_xml,
        )
        if response_text is None:
            return {}

        namespaces = {
            's': 'http://schemas.xmlsoap.org/soap/envelope/',
            'u': 'urn:schemas-upnp-org:service:AVTransport:1',
        }
        try:
            root = ET.fromstring(response_text)
            prefix = './/u:GetInfoExResponse/'
            return {
                "play_medium": root.find(f'{prefix}PlayMedium', namespaces).text,
                "volume_level": root.find(f'{prefix}CurrentVolume', namespaces).text,
                "track": root.find(f'{prefix}TrackURI', namespaces).text,
                "transport_state": root.find(f'{prefix}CurrentTransportState', namespaces).text,
                "transport_status": root.find(f'{prefix}CurrentTransportStatus', namespaces).text,
                "track_duration": root.find(f'{prefix}TrackDuration', namespaces).text,
                "mute": root.find(f'{prefix}CurrentMute', namespaces).text,
                "channel": root.find(f'{prefix}CurrentChannel', namespaces).text,
                "slaves": root.find(f'{prefix}SlaveFlag', namespaces).text,
            }
        except AttributeError:
            _LOGGER.error("Could not find necessary data in the response")
            return {}

    async def setVolume(self, value: float):
        """Set volume via UPnP SOAP."""
        payload_xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
        <s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
        <s:Body><u:SetVolume xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">
        <InstanceID>0</InstanceID><Channel>Single</Channel>
        <DesiredVolume>{round(value)}</DesiredVolume>
        </u:SetVolume></s:Body></s:Envelope>"""

        await self._soap_request(
            59152, "/upnp/control/rendercontrol1",
            "SetVolume", "urn:schemas-upnp-org:service:RenderingControl:1",
            payload_xml,
        )

    # --- EQ (single call for both bands and presets) ---

    async def _getEQData(self):
        """Fetch EQ data: bands from active preset + preset list (single API call for new firmware)."""
        if self.newFirmware:
            response_json = await self._https_get("getEQList")
            if not response_json or "eq_list" not in response_json:
                return {}

            eq_list = response_json["eq_list"]
            active_id = str(response_json.get("active_eq_id", "0"))

            # Build preset map and data
            preset_map = {}
            preset_data = {}
            active_preset = None
            active_name = None
            for item in eq_list:
                eq_id = str(item.get("eq_id", ""))
                eq_name = item.get("eq_name", f"Preset {eq_id}")
                preset_map[eq_id] = eq_name
                preset_data[eq_id] = item
                if eq_id == active_id:
                    active_preset = item
                    active_name = eq_name

            if active_preset is None and eq_list:
                active_preset = eq_list[0]
                active_name = active_preset.get("eq_name", "Unknown")

            result = {
                "eq_preset_map": preset_map,
                "eq_preset_data": preset_data,
                "eq_active_preset": active_name,
                "eq_active_id": active_id,
            }

            if active_preset:
                gain = active_preset["eq_payload"]["gain"]
                result.update({
                    "125Hz": gain[0],
                    "250Hz": gain[1],
                    "500Hz": gain[2],
                    "1000Hz": gain[3],
                    "2000Hz": gain[4],
                    "4000Hz": gain[5],
                    "8000Hz": gain[6],
                })

            return result
        else:
            response_json = await self._https_get("getEQ")
            if not response_json or "eq_setting" not in response_json:
                return {}
            gain = response_json["eq_setting"]["eq_payload"]["gain"]
            return {
                "EQ_1_Low": gain[0],
                "EQ_2_Mid": gain[1],
                "EQ_3_High": gain[2],
            }

    async def setEQ(self, value: float, frequency):
        """Set EQ value for a specific frequency band."""
        if self.newFirmware:
            eqList = {
                "125Hz": self.data.get("125Hz", 0),
                "250Hz": self.data.get("250Hz", 0),
                "500Hz": self.data.get("500Hz", 0),
                "1000Hz": self.data.get("1000Hz", 0),
                "2000Hz": self.data.get("2000Hz", 0),
                "4000Hz": self.data.get("4000Hz", 0),
                "8000Hz": self.data.get("8000Hz", 0),
            }
            eqList[frequency] = value
            payload_data = json.dumps({
                "active_eq_id": "0",
                "band": 7,
                "eq_payload": {
                    "fs": [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0],
                    "gain": [eqList["125Hz"], eqList["250Hz"], eqList["500Hz"],
                             eqList["1000Hz"], eqList["2000Hz"], eqList["4000Hz"], eqList["8000Hz"]],
                },
            })
            await self._https_post(f"command=setActiveEQ&payload={payload_data}")
        else:
            bass = str(round(value, 1)) if frequency == "EQ_1_Low" else str(self.data.get("EQ_1_Low"))
            mid = str(round(value, 1)) if frequency == "EQ_2_Mid" else str(self.data.get("EQ_2_Mid"))
            high = str(round(value, 1)) if frequency == "EQ_3_High" else str(self.data.get("EQ_3_High"))
            payload_data = json.dumps({
                "eq_id": "1",
                "eq_name": "Custom",
                "eq_payload": {
                    "fs": [150.0, 1000.0, 6000.0],
                    "gain": [float(bass), float(mid), float(high)],
                    "q": [0.7070000171661377, 0.5, 0.7070000171661377],
                    "type": [17.0, 11.0, 16.0],
                },
                "eq_status": "on",
            })
            await self._https_post(f"command=setEQ&payload={payload_data}")

    async def setActiveEQPreset(self, eq_id: str):
        """Set the active EQ preset by its ID."""
        preset_data = self.data.get("eq_preset_data", {})
        preset = preset_data.get(eq_id)
        if not preset:
            _LOGGER.error("EQ preset data not found for id: %s", eq_id)
            return

        payload_data = json.dumps({
            "active_eq_id": eq_id,
            "band": preset.get("band", 7),
            "eq_payload": preset.get("eq_payload", {}),
        })
        _LOGGER.debug("Setting EQ preset: %s", payload_data)
        await self._https_post(f"command=setActiveEQ&payload={payload_data}")

    # --- Mode getters/setters ---

    async def getNightMode(self):
        response = await self._https_get("getPersonalListeningMode")
        if "status" in response:
            return {"NightMode": response["status"]}
        return {}

    async def setNightMode(self, value: bool):
        strvalue = "on" if value else "off"
        await self._https_post(f'command=setPersonalListeningMode&payload={{"status":"{strvalue}"}}')

    async def getRearSpeaker(self):
        response = await self._https_get("getRearSpeakerStatus")
        if "rears" in response:
            return {"Rears": response["rears"]}
        return {}

    async def getSmartMode(self):
        response = await self._https_get("getSmartMode")
        if "status" in response:
            return {"SmartMode": response["status"]}
        return {}

    async def getPureVoice(self):
        response = await self._https_get("getPureVoiceState")
        if "purevoice_state" in response:
            return {"PureVoice": "on" if response["purevoice_state"] == "1" else "off"}
        return {}

    async def setPureVoice(self, value: bool):
        strvalue = "1" if value else "0"
        await self._https_post(f'command=setPureVoiceState&payload={{"purevoice_state":"{strvalue}"}}')

    # --- Sleep Timer ---

    async def getSleepTimer(self):
        response = await self._https_get("getSleepTimer")
        if "sleep_timer" in response:
            return {
                "sleep_timer": int(response.get("sleep_timer", 0)),
                "sleep_remain": int(response.get("remain_time", 0)),
            }
        return {}

    async def setSleepTimer(self, minutes: int):
        await self._https_post(f'command=setSleepTimer&payload={{"sleep_timer":"{minutes}"}}')

    # --- Network / Group info ---

    async def getNetworkInfo(self):
        response = await self._https_get("getStatusEx")
        if not response:
            return {}
        result = {}
        if "RSSI" in response:
            result["wifi_rssi"] = int(response["RSSI"])
        if "internet" in response:
            result["internet"] = response["internet"] == "1" or response["internet"] == 1
        if "essid" in response:
            try:
                result["wifi_ssid"] = bytes.fromhex(response["essid"]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                result["wifi_ssid"] = response["essid"]
        if "WifiChannel" in response:
            result["wifi_channel"] = int(response.get("WifiChannel", 0))
        # Group mode from getStatusEx or getGroupInfo
        if "hm_dev_mode" in response:
            result["group_mode"] = response["hm_dev_mode"]
        return result
