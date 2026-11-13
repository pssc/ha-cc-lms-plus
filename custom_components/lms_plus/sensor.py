"""Sensors backed by the selected Squeezebox player."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

BITRATE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*kbps\b", re.IGNORECASE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the bitrate sensor for a selected player."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LmsBitrateSensor(
                data["coordinator"],
                data["player_entity"],
                entry.entry_id,
                data["device_info"],
            ),
            LmsQueuePositionSensor(
                data["coordinator"],
                data["player_entity"],
                entry.entry_id,
                data["device_info"],
            ),
        ]
    )


class LmsBitrateSensor(SensorEntity):
    """Report the current track's LMS bitrate in kilobits per second."""

    _attr_has_entity_name = True
    _attr_name = "Currently playing track bitrate"
    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = "kbps"
    _attr_suggested_display_precision = 0

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
        self._attr_unique_id = f"{entry_id}_bitrate"
        self._attr_device_info = device_info
        self._remove_update_listener: Callable[[], None] | None = None

    @property
    def native_value(self) -> float | None:
        """Return LMS's bitrate as a number, or None if it is unavailable."""
        track = self._current_track()
        if not track:
            return None
        status = getattr(self._player, "_status", {})
        # LMS 9.2 exposes output-stream technical data in the main status
        # response; older LMS versions only provide the library-track value.
        bitrate = status.get("bitrate") if isinstance(status, dict) else None
        if not isinstance(bitrate, str):
            bitrate = track.get("bitrate")
        if isinstance(bitrate, (int, float)):
            return bitrate
        if not isinstance(bitrate, str):
            return None
        match = BITRATE_PATTERN.match(bitrate)
        if not match:
            return None
        value = float(match.group(1))
        return int(value) if value.is_integer() else value

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        """Identify the source player and which bitrate LMS reported."""
        status = getattr(self._player, "_status", {})
        status_has_bitrate = isinstance(status, dict) and isinstance(
            status.get("bitrate"), (str, int, float)
        )
        result = {
            "player_entity_id": self._player_entity,
            "bitrate_basis": "output_stream"
            if status_has_bitrate
            else "track_metadata",
        }
        if isinstance(status, dict) and isinstance(status.get("is_transcoded"), bool):
            result["is_transcoded"] = status["is_transcoded"]
        return result

    def _current_track(self) -> dict[str, Any] | None:
        """Return the current playlist item from pysqueezebox.Player."""
        try:
            track = self._player.current_track
        except (IndexError, KeyError, TypeError):
            return None
        return track if isinstance(track, dict) else None

    async def async_added_to_hass(self) -> None:
        """Keep the sensor current with Squeezebox coordinator updates."""
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            self.async_write_ha_state()

        self._remove_update_listener = self._coordinator.async_add_listener(
            _handle_player_update
        )
        self.async_on_remove(self._remove_update_listener)


class _LmsPlayerSensor(SensorEntity):
    """Base class for sensors attached to a Squeezebox player."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: Any,
        player_entity: str,
        entry_id: str,
        device_info: dict,
        unique_suffix: str,
    ) -> None:
        self._coordinator = coordinator
        self._player = coordinator.player
        self._player_entity = player_entity
        self._attr_unique_id = f"{entry_id}_{unique_suffix}"
        self._attr_device_info = device_info

    def _current_track(self) -> dict[str, Any] | None:
        try:
            track = self._player.current_track
        except (IndexError, KeyError, TypeError):
            return None
        return track if isinstance(track, dict) else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self._coordinator.async_add_listener(_handle_player_update)
        )


class LmsQueuePositionSensor(_LmsPlayerSensor):
    """Report the current one-based position in the live LMS queue."""

    _attr_name = "Queue position"
    _attr_icon = "mdi:playlist-play"

    def __init__(
        self,
        coordinator: Any,
        player_entity: str,
        entry_id: str,
        device_info: dict,
    ) -> None:
        super().__init__(
            coordinator, player_entity, entry_id, device_info, "queue_position"
        )

    @property
    def native_value(self) -> int | None:
        status = getattr(self._player, "_status", {})
        if not isinstance(status, dict):
            return None
        try:
            playlist_tracks = int(status.get("playlist_tracks", 0))
        except (TypeError, ValueError):
            return None
        if playlist_tracks <= 0:
            return None
        try:
            index = int(status.get("playlist_cur_index", -1))
        except (TypeError, ValueError):
            return None
        return index + 1 if index >= 0 else None
