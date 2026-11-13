"""Constants for LMS Plus."""

DOMAIN = "lms_plus"
PLATFORMS = ["binary_sensor", "button", "number", "select", "sensor", "time", "todo"]
SERVER_PLATFORMS = ["button", "select"]
CONF_SERVER_CONTROLS = "server_controls"

CONF_PLAYER = "player"
CONF_SQUEEZEBOX_ENTRY = "squeezebox_entry"
CONF_PLAYLIST_SUMMARY_FORMAT = "playlist_summary_format"
CONF_PLAYLIST_DESCRIPTION_FORMAT = "playlist_description_format"

DEFAULT_PLAYLIST_SUMMARY_FORMAT = "{track_number}. {trackname} {rating}"
DEFAULT_PLAYLIST_DESCRIPTION_FORMAT = (
    "Artist: {artist}\n"
    "Album: {album} ({year})\n"
    "Bitrate: {bitrate}\n"
    "Format: {format}\n"
    "Duration: {duration}"
)
