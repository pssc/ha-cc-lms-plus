"""Binary sensors backed by the selected Squeezebox player."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the lossless sensor for a selected player."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities(
        [
            LmsLosslessSensor(
                coordinator,
                data["player_entity"],
                entry.entry_id,
                data["device_info"],
            ),
            LmsStreamingSensor(
                coordinator,
                data["player_entity"],
                entry.entry_id,
                data["device_info"],
            ),
            LmsEndOfQueueSensor(coordinator, entry.entry_id, data["device_info"]),
        ]
    )


class LmsEndOfQueueSensor(BinarySensorEntity):
    """Report whether the selected queue item is the final item in LMS."""

    _attr_has_entity_name = True
    _attr_name = "At end of queue"
    _attr_icon = "mdi:playlist-check"

    def __init__(self, coordinator: Any, entry_id: str, device_info: dict) -> None:
        self._coordinator = coordinator
        self._player = coordinator.player
        self._attr_unique_id = f"{entry_id}_at_end_of_queue"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        """Return true on the final queued item, unknown when data is absent."""
        status = getattr(self._player, "_status", {})
        if not isinstance(status, dict):
            return None
        try:
            track_count = int(status.get("playlist_tracks", 0))
            current_index = int(status.get("playlist_cur_index", -1))
        except (TypeError, ValueError):
            return None
        if track_count <= 0 or current_index < 0:
            return None
        return current_index == track_count - 1

    async def async_added_to_hass(self) -> None:
        """Update when the Squeezebox player coordinator reports a change."""
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._coordinator.async_add_listener(_handle_player_update)
        )


class LmsLosslessSensor(BinarySensorEntity):
    """Report whether the player's current track is lossless."""

    _attr_has_entity_name = True
    _attr_name = "Currently playing track is lossless"
    _attr_icon = "mdi:waveform"

    def __init__(
        self,
        coordinator: Any,
        player_entity: str,
        entry_id: str,
        device_info: dict,
    ) -> None:
        """Initialize the sensor with the Squeezebox integration's Player."""
        self._coordinator = coordinator
        self._player = coordinator.player
        self._player_entity = player_entity
        self._attr_unique_id = f"{entry_id}_lossless"
        self._attr_device_info = device_info
        self._remove_update_listener: Callable[[], None] | None = None

    @property
    def is_on(self) -> bool | None:
        """Return LMS's lossless flag, or None when LMS did not provide it."""
        track = self._current_track()
        if not track:
            return None
        lossless = track.get("lossless")
        if lossless in (1, "1", True):
            return True
        if lossless in (0, "0", False):
            return False
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose LMS's reported format and the source player entity."""
        result = {"player_entity_id": self._player_entity}
        track = self._current_track()
        if not track:
            return result
        if value := _format_from_track(track):
            result["media_format"] = value
        if (lossless := track.get("lossless")) in (0, 1, "0", "1", False, True):
            result["lms_lossless"] = str(lossless)
        return result

    def _current_track(self) -> dict[str, Any] | None:
        """Return the current track reported by PySqueezebox.Player."""
        try:
            track = self._player.current_track
        except (IndexError, KeyError, TypeError):
            return None
        return track if isinstance(track, dict) else None

    def _current_playlist_track(self) -> dict[str, Any] | None:
        """Return the queue item marked current by the playlist To-do list."""
        status = getattr(self._player, "_status", {})
        if isinstance(status, dict):
            playlist = status.get("playlist_loop")
            if isinstance(playlist, list):
                try:
                    current_index = int(status.get("playlist_cur_index", -1))
                except (TypeError, ValueError):
                    current_index = -1
                for index, item in enumerate(playlist):
                    if not isinstance(item, dict):
                        continue
                    try:
                        item_index = int(item.get("playlist index", index))
                    except (TypeError, ValueError):
                        item_index = index
                    if item_index == current_index:
                        return item

                # A valid queue without a current index has no current track.
                if playlist:
                    return None
        return None

    async def async_added_to_hass(self) -> None:
        """Listen to the Squeezebox coordinator's player update notifications."""
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            self.async_write_ha_state()

        self._remove_update_listener = self._coordinator.async_add_listener(
            _handle_player_update
        )
        self.async_on_remove(self._remove_update_listener)


class LmsStreamingSensor(LmsLosslessSensor):
    """Report whether LMS identifies the current queue item as a stream."""

    _attr_name = "Currently playing a stream"
    _attr_icon = "mdi:radio"

    def __init__(
        self,
        coordinator: Any,
        player_entity: str,
        entry_id: str,
        device_info: dict,
    ) -> None:
        super().__init__(coordinator, player_entity, entry_id, device_info)
        self._attr_unique_id = f"{entry_id}_streaming"

    @property
    def is_on(self) -> bool | None:
        """Report remote status from the active track or, when off, queue item."""
        player_state = self.hass.states.get(self._player_entity)
        if player_state is None:
            return None
        track = (
            self._current_playlist_track()
            if player_state.state == STATE_OFF
            else self._current_track()
        )
        if not track:
            return None
        remote = track.get("remote")
        if _is_local_tmp_url(track.get("url")):
            return False
        if remote in (1, "1", True):
            return True
        if remote in (0, "0", False):
            return False
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Identify the player without copying its existing media attributes."""
        return {"player_entity_id": self._player_entity}


def _format_from_track(track: dict[str, Any]) -> str | None:
    """Get the format/codec from pysqueezebox current-track metadata."""
    for key in (
        "type",
        "format",
        "codec",
        "audio_codec",
        "content_type",
        "mime_type",
    ):
        value = track.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_local_tmp_url(value: Any) -> bool:
    """Recognize local file paths LMS exposes through its temporary URL scheme."""
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "tmp" and parsed.path.startswith("/")
