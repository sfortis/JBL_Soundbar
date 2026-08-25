"""Constants for the JBL integration."""
DOMAIN = "jbl_integration"

# Selectable input sources -> Linkplay switchmode value.
# First three are physical inputs available on all JBL One soundbars.
# The streaming modes set the device into a listening state for that protocol.
# Cast / Chromecast is intentionally omitted: it activates automatically when
# a sender device starts casting and cannot be entered manually via this API.
SOURCE_KEYS = {
    "Bluetooth": "bluetooth",
    "AUX": "line-in",
    "Network": "wifi",
    "DLNA": "dlna",
    "AirPlay": "airplay",
    "Spotify": "spotify",
}

# play_medium reported by device -> human-readable source name (display only).
# Order matters: more specific keys first since matching is substring-based.
SOURCE_DISPLAY_MAP = {
    "BLUETOOTH": "Bluetooth",
    "LINE-IN": "AUX",
    "AUXIN": "AUX",
    "AUX": "AUX",
    "AIRPLAY": "AirPlay",
    "SPOTIFY": "Spotify",
    "CAST": "Chromecast",
    "DLNA": "DLNA",
    "TIDAL": "Tidal",
    "DEEZER": "Deezer",
}


def source_display_name(play_medium: str | None) -> str | None:
    """Map a device-reported play_medium to a friendly source name.

    Returns None when the medium is unknown/empty. Unmatched streaming
    mediums (THIRD-DLNA, QPLAY etc.) fall back to "Network".
    """
    medium = (play_medium or "").upper()
    if not medium or medium == "UNKNOWN":
        return None
    for key, name in SOURCE_DISPLAY_MAP.items():
        if key in medium:
            return name
    return "Network"
