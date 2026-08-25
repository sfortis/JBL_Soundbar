"""Media player platform for JBL integration."""
import asyncio
import logging

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SOURCE_KEYS, source_display_name
from .coordinator import Coordinator

_LOGGER = logging.getLogger(__name__)

# Map JBL transport states to HA media player states
_STATE_MAP = {
    "PLAYING": MediaPlayerState.PLAYING,
    "PAUSED_PLAYBACK": MediaPlayerState.PAUSED,
    "STOPPED": MediaPlayerState.IDLE,
    "TRANSITIONING": MediaPlayerState.BUFFERING,
    "NO_MEDIA_PRESENT": MediaPlayerState.IDLE,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the JBL media player platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([JBLMediaPlayer(entry, coordinator)])


class JBLMediaPlayer(MediaPlayerEntity):
    """Representation of the JBL soundbar as a media player."""

    _BASE_FEATURES = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, entry: ConfigEntry, coordinator: Coordinator):
        self._entry = entry
        self.coordinator = coordinator
        device_name = self.coordinator.device_info.get("name", "jbl_integration")
        self.entity_id = f"media_player.{device_name.replace(' ', '_').lower()}"

    @property
    def name(self):
        return None

    @property
    def has_entity_name(self):
        return True

    @property
    def unique_id(self):
        return f"jbl_media_player_{self._entry.entry_id}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def should_poll(self):
        return False

    @property
    def supported_features(self):
        """Return supported features."""
        features = self._BASE_FEATURES
        if self.coordinator.has_capability("power"):
            features |= (
                MediaPlayerEntityFeature.TURN_ON
                | MediaPlayerEntityFeature.TURN_OFF
            )
        return features

    @property
    def state(self):
        play_medium = self.coordinator.data.get("play_medium")
        if play_medium == "UNKNOWN" or play_medium is None:
            return MediaPlayerState.IDLE
        transport = self.coordinator.data.get("transport_state", "")
        return _STATE_MAP.get(transport, MediaPlayerState.IDLE)

    @property
    def volume_level(self):
        try:
            return int(self.coordinator.data.get("volume_level", 0)) / 100
        except (TypeError, ValueError):
            return None

    @property
    def is_volume_muted(self):
        return self.coordinator.data.get("mute") == "1"

    @property
    def media_title(self):
        title = self.coordinator.data.get("media_title")
        if title:
            return title
        # Fallback to URI if no metadata available
        return self.coordinator.data.get("track")

    @property
    def media_artist(self):
        return self.coordinator.data.get("media_artist")

    @property
    def media_album_name(self):
        return self.coordinator.data.get("media_album")

    @property
    def media_image_url(self):
        return self.coordinator.data.get("media_image_url")

    @property
    def media_image_remotely_accessible(self):
        return True

    @property
    def media_content_type(self):
        return "music"

    @property
    def media_duration(self):
        # Prefer DIDL metadata duration over UPnP TrackDuration (often 00:00:00)
        meta_duration = self.coordinator.data.get("media_track_duration")
        if meta_duration:
            return meta_duration
        return self._duration_to_seconds(self.coordinator.data.get("track_duration"))

    @property
    def media_position(self):
        # Computed in coordinator: curpos minus per-track offset captured at track change
        return self.coordinator.data.get("media_position")

    @property
    def media_position_updated_at(self):
        # Use the timestamp captured when the position was actually fetched, so
        # HA can extrapolate the progress bar smoothly between polls.
        return self.coordinator.data.get("_position_updated_at")

    @staticmethod
    def _duration_to_seconds(value):
        if not value or value == "00:00:00" or value == "NOT_IMPLEMENTED":
            return None
        try:
            parts = value.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        except (ValueError, AttributeError):
            pass
        return None

    @property
    def source(self):
        return source_display_name(self.coordinator.data.get("play_medium"))

    @property
    def source_list(self):
        return list(SOURCE_KEYS.keys())

    async def async_select_source(self, source: str):
        mode = SOURCE_KEYS.get(source)
        if not mode:
            _LOGGER.error("Unknown source: %s", source)
            return
        await self.coordinator.switchSource(mode)
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        return {
            "transport_status": self.coordinator.data.get("transport_status"),
            "channel": self.coordinator.data.get("channel"),
        }

    async def _apply_state(self, state: MediaPlayerState):
        """Apply optimistic state and schedule refresh."""
        if state == MediaPlayerState.PLAYING:
            self.coordinator.data["transport_state"] = "PLAYING"
        elif state == MediaPlayerState.PAUSED:
            self.coordinator.data["transport_state"] = "PAUSED_PLAYBACK"
        elif state == MediaPlayerState.IDLE:
            self.coordinator.data["transport_state"] = "STOPPED"
        self.async_write_ha_state()
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_media_play(self):
        # setPlayerCmd:play resumes from paused state; if stopped, falls back to Play SOAP
        if self.state == MediaPlayerState.PAUSED:
            await self.coordinator.setPlayerCmd("resume")
        else:
            await self.coordinator.setPlayerCmd("play")
        await self._apply_state(MediaPlayerState.PLAYING)

    async def async_media_pause(self):
        await self.coordinator.setPlayerCmd("pause")
        await self._apply_state(MediaPlayerState.PAUSED)

    async def async_media_stop(self):
        await self.coordinator.setPlayerCmd("stop")
        await self._apply_state(MediaPlayerState.IDLE)

    async def async_media_next_track(self):
        await self.coordinator.setPlayerCmd("next")
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_media_previous_track(self):
        await self.coordinator.setPlayerCmd("prev")
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_media_seek(self, position: float):
        await self.coordinator.seek(int(position))
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float):
        new_vol = round(volume * 100)
        await self.coordinator.setVolume(new_vol)
        self.coordinator.data["volume_level"] = str(new_vol)
        self.async_write_ha_state()
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_volume_up(self):
        await self.coordinator.setPlayerCmd("VolumeUp")
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_volume_down(self):
        await self.coordinator.setPlayerCmd("VolumeDown")
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool):
        await self.coordinator.setMute(mute)
        self.coordinator.data["mute"] = "1" if mute else "0"
        self.async_write_ha_state()
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self):
        await self.coordinator._send_command("power")
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self):
        await self.coordinator._send_command("power")
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def async_play_media(self, media_type: str, media_id: str, **kwargs):
        """Play a media URL on the soundbar via UPnP SetAVTransportURI + Play."""
        # Resolve media_source URLs (e.g. media-source://...)
        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = play_item.url
        media_id = async_process_play_media_url(self.hass, media_id)

        # Build minimal DIDL-Lite metadata
        title = kwargs.get("extra", {}).get("title", "Media") if isinstance(kwargs.get("extra"), dict) else "Media"
        metadata = (
            '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0" parentID="0" restricted="1">'
            f'<dc:title>{title}</dc:title>'
            '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
            f'<res protocolInfo="http-get:*:*:*">{media_id}</res>'
            '</item></DIDL-Lite>'
        )

        await self.coordinator.setAVTransportURI(media_id, metadata)
        await asyncio.sleep(0.5)
        await self.coordinator.setPlayerCmd("play")
        await self._apply_state(MediaPlayerState.PLAYING)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Allow browsing HA media sources to pick a track to play."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    async def async_update(self):
        await self.coordinator.async_request_refresh()
