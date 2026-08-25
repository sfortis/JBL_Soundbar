"""Constants for the JBL integration."""
DOMAIN = "jbl_integration"

# Selectable input sources -> Linkplay switchmode value.
# Only the physical inputs are selectable. Verified on Authentics 200:
# switchmode wifi/dlna/airplay/spotify all return OK but land in mode 0
# (network standby, PlayMedium UNKNOWN); streaming sources activate only
# when a sender starts a stream, so offering them as options is misleading.
SOURCE_KEYS = {
    "Bluetooth": "bluetooth",
    "AUX": "line-in",
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
