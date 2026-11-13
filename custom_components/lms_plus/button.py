"""Buttons for the selected Squeezebox player."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .server_controls import (
    LmsArmLibraryWipeButton,
    LmsLibraryRescanButton,
    LmsLibraryWipeButton,
    LmsMovePlayerButton,
    LmsPlaylistRescanButton,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up player or server-level buttons."""
    data = hass.data[DOMAIN][entry.entry_id]
    if data.get("server_controls"):
        # The normal scan no longer needs an arm button; remove the registry
        # entry from versions that exposed the former two-step scan flow.
        entity_registry = er.async_get(hass)
        obsolete_scan_arm = entity_registry.async_get_entity_id(
            "button", DOMAIN, f"{entry.entry_id}_arm_full_library_scan"
        )
        if obsolete_scan_arm is not None:
            entity_registry.async_remove(obsolete_scan_arm)
        async_add_entities(
            [
                LmsMovePlayerButton(hass, entry.entry_id, data),
                LmsLibraryRescanButton(entry.entry_id, data),
                LmsArmLibraryWipeButton(hass, entry.entry_id, data),
                LmsLibraryWipeButton(entry.entry_id, data),
                LmsPlaylistRescanButton(entry.entry_id, data),
            ]
        )
        return
    async_add_entities(
        [
            LmsSleepAtEndOfTrackButton(
                data["coordinator"], entry.entry_id, data["device_info"]
            )
        ]
    )


class LmsSleepAtEndOfTrackButton(ButtonEntity):
    """Start LMS's native sleep timer for the remainder of the current track."""

    _attr_has_entity_name = True
    _attr_name = "Sleep at end of track"
    _attr_icon = "mdi:music-note-off"

    def __init__(self, coordinator: Any, entry_id: str, device_info: dict) -> None:
        self._coordinator = coordinator
        self._player = coordinator.player
        self._attr_unique_id = f"{entry_id}_sleep_at_end_of_track"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return (
            self._coordinator.last_update_success
            and self._player.mode == "play"
            and bool(self._player.duration)
        )

    async def async_press(self) -> None:
        """Ask LMS to sleep when the current timed track finishes."""
        try:
            await self._player.async_update()
        except Exception as err:
            raise HomeAssistantError("Could not read the current LMS track") from err

        if self._player.mode != "play" or not self._player.duration:
            raise HomeAssistantError(
                "Sleep at end of track requires a playing track with a known duration"
            )

        query = getattr(self._player, "async_query", None)
        if not callable(query):
            raise HomeAssistantError("The LMS player query API is unavailable")
        try:
            await query("jiveendoftracksleep")
        except Exception as err:
            raise HomeAssistantError("Could not schedule LMS sleep") from err
