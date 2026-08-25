"""Button platform for JBL integration."""
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import Coordinator
from .entity import build_entity_id

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the JBL button platform."""

    
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # The "Moment" button is unique to Authentics speakers (heart icon, plays a
    # configured favorite). All playback / volume / source actions are exposed
    # through the media_player entity instead.
    entities = [
        JBLButton(coordinator, entry, "smart_triger", "Moment", "mdi:heart-box"),
    ]

    if coordinator.has_capability("power"):
        entities.insert(0, JBLButton(coordinator, entry, "power", "Power", "mdi:power"))

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
        self.entity_id = build_entity_id(
            "button",
            self.coordinator.device_info.get("name", "jbl_integration"),
            self.entityName,
        )
        

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
