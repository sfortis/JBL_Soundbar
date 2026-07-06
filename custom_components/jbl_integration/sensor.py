"""Sensor platform for JBL integration."""
import aiohttp
import async_timeout
import logging
from datetime import timedelta
from homeassistant.helpers.entity import Entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .const import DOMAIN
from .coordinator import Coordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the JBL sensor platform."""

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    
    entityArray = [
        # Playback state sensors (also exposed by media_player, but kept for automations)
        JBLSensor(coordinator, entry, "play_medium", "Play Medium", "mdi:soundbar"),
        JBLSensor(coordinator, entry, "transport_state", "Transport State", "mdi:state-machine"),
        JBLSensor(coordinator, entry, "transport_status", "Transport Status", "mdi:information"),
        # Network and timer sensors
        JBLWiFiSignalSensor(coordinator, entry),
        JBLSensor(coordinator, entry, "wifi_ssid", "WiFi SSID", "mdi:wifi-settings"),
        JBLSensor(coordinator, entry, "group_mode", "Group Mode", "mdi:speaker-multiple"),
        JBLSensor(coordinator, entry, "sleep_remain", "Sleep Timer Remaining", "mdi:timer-sand"),
    ]

    async_add_entities(entityArray)

    
class JBLSensor(Entity):
    """Representation of a sensor to get JBL PlayMedium."""

    def __init__(self, coordinator, entry, infoString, name, icon):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._entry = entry
        self.entityName = name
        self.entityicon = icon
        self.entity_id = f"sensor.{self.coordinator.device_info.get('name', 'jbl_integration').replace(' ', '_').lower()}_{self.entityName.replace(' ', '_').lower()}"
        self.infoString = infoString

    @property
    def name(self):
        """Return the name of the sensor."""
        return self.entityName

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return whether the entity should be enabled when first added to the entity registry."""
        return False  # Disable the sensor by default

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(self.infoString)

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        return self.entityicon

    @property
    def enabled(self):
        return False

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"jbl_800_{self.infoString.replace('_', '')}_{self._entry.entry_id}"

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def device_info(self):
        """Return device information about this entity."""
        return self.coordinator.device_info


    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    async def async_update(self):
        """Update the sensor."""
        await self.coordinator.async_request_refresh()

class JBLWiFiSignalSensor(SensorEntity):
    """WiFi signal strength sensor with proper device class for graphs."""

    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self._entry = entry
        self.entity_id = f"sensor.{self.coordinator.device_info.get('name', 'jbl_integration').replace(' ', '_').lower()}_wifi_signal"

    @property
    def name(self):
        return "WiFi Signal"

    @property
    def unique_id(self):
        return f"jbl_wifi_signal_{self._entry.entry_id}"

    @property
    def device_class(self):
        return SensorDeviceClass.SIGNAL_STRENGTH

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self):
        return SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    @property
    def native_value(self):
        return self.coordinator.data.get("wifi_rssi")

    @property
    def icon(self):
        return "mdi:wifi"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def should_poll(self):
        return False

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    async def async_update(self):
        await self.coordinator.async_request_refresh()
