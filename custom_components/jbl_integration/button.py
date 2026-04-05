"""Button platform for JBL integration."""
import async_timeout
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import Coordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the JBL button platform."""

    
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Always available buttons
    entities = [
        JBLButton(coordinator,entry,"power","Power","mdi:power"),
        JBLButton(coordinator,entry,"mute","Mute","mdi:volume-off"),
        JBLButton(coordinator,entry,"volumeUp","Increase Volume","mdi:volume-plus"),
        JBLButton(coordinator,entry,"volumeDown","Lower Volume","mdi:volume-minus"),
        JBLButton(coordinator,entry,"musicPlayPause","Play Pause","mdi:play-pause"),
        JBLButton(coordinator,entry,"smart_triger","Moment","mdi:heart-box"),
        JBLButton(coordinator,entry,"bluetooth","Bluetooth","mdi:bluetooth"),
    ]

    # Capability-dependent buttons
    cap_buttons = {
        "calibration": ("calibration", "Calibration", "mdi:calculator-variant"),
        "rear_speakers": ("keyRear", "Rear", "mdi:numeric-2-box-multiple"),
        "bass_boost": ("bassboost", "Bass", "mdi:equalizer"),
        "atmos": ("keyAtmosLevel", "Atmos", "mdi:equalizer"),
        "hdmi": ("source-hdmi-switch", "HDMI", "mdi:video-input-hdmi"),
        "smart_mode": ("surround", "Smart Mode", "mdi:surround-sound"),
    }
    # Only add source-tv if HDMI is supported (soundbar models)
    if coordinator.has_capability("hdmi"):
        cap_buttons["hdmi_tv"] = ("source-tv", "TV", "mdi:television-box")

    for cap, (cmd, name, icon) in cap_buttons.items():
        cap_key = cap if cap != "hdmi_tv" else "hdmi"
        if coordinator.has_capability(cap_key):
            entities.append(JBLButton(coordinator, entry, cmd, name, icon))

    async_add_entities(entities)

class JBLButton(ButtonEntity):
    """Base class for a JBL button."""

    def __init__(self, coordinator, entry, actionstring, name, icon):
        """Initialize the sensor."""
        self.coordinator:Coordinator = coordinator
        self._entry = entry
        self.entityName = name
        self.entityicon = icon
        self.actionstring = actionstring
        self.entity_id = f"button.{self.coordinator.device_info.get("name", "jbl_integration").replace(' ', '_').lower()}_{self.entityName.replace(' ', '_').lower()}"
        

    @property
    def name(self):
        """Return the name of the sensor."""
        return self.entityName

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return whether the entity should be enabled when first added to the entity registry."""
        return False  # Disable the sensor by default

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        return self.entityicon

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"jbl_800_Button{self.entityName.replace(' ', '')}_{self._entry.entry_id}"

    @property
    def device_info(self):
        """Return device information about this entity."""
        return self.coordinator.device_info

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator._send_command(self.actionstring)
