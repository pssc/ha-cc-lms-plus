"""Server-level controls for moving players between LMS servers."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .config_flow import (
    _all_player_entities,
    _async_confirm_move_and_update_device,
    _async_move_player_to_server,
    _player_option_label,
)
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one player selector and one move confirmation button."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LmsMovePlayerSelect(hass, entry.entry_id, data),
            LmsMovePlayerButton(hass, entry.entry_id, data),
        ]
    )


class LmsMovePlayerSelect(SelectEntity):
    """Choose a player currently assigned to another configured LMS."""

    _attr_has_entity_name = True
    _attr_name = "Player to move here"
    _attr_icon = "mdi:account-switch"

    def __init__(self, hass: HomeAssistant, entry_id: str, data: dict) -> None:
        self.hass = hass
        self._destination_entry_id = data["squeezebox_entry_id"]
        self._device_info = data["device_info"]
        self._data = data
        self._attr_unique_id = f"{entry_id}_player_to_move"
        self._attr_device_info = self._device_info

    @property
    def options(self) -> list[str]:
        """List player names and their current server for selection."""
        return [
            _player_option_label(self.hass, entity, self._destination_entry_id)
            for entity in _all_player_entities(self.hass)
            if entity.config_entry_id != self._destination_entry_id
        ]

    @property
    def current_option(self) -> str | None:
        """Return the selected player, if it remains available."""
        selected = self._data.get("selected_player")
        entity = er.async_get(self.hass).async_get(selected) if selected else None
        if entity is None or entity.config_entry_id == self._destination_entry_id:
            return None
        return _player_option_label(self.hass, entity, self._destination_entry_id)

    async def async_select_option(self, option: str) -> None:
        """Select a player; the separate button is the move confirmation."""
        entity = next(
            (
                item
                for item in _all_player_entities(self.hass)
                if item.config_entry_id != self._destination_entry_id
                and _player_option_label(self.hass, item, self._destination_entry_id)
                == option
            ),
            None,
        )
        if entity is None:
            raise HomeAssistantError("That player is no longer available to move")
        self._data["selected_player"] = entity.entity_id
        self.async_write_ha_state()
        move_button = self._data.get("move_button")
        if move_button is not None:
            move_button.async_write_ha_state()


class LmsMovePlayerButton(ButtonEntity):
    """Confirm and perform moving the selected player to this LMS."""

    _attr_has_entity_name = True
    _attr_name = "Confirm move (group may move too)"
    _attr_icon = "mdi:account-arrow-right"

    def __init__(self, hass: HomeAssistant, entry_id: str, data: dict) -> None:
        self.hass = hass
        self._destination_entry_id = data["squeezebox_entry_id"]
        self._device_info = data["device_info"]
        self._attr_unique_id = f"{entry_id}_confirm_player_move"
        self._attr_device_info = self._device_info
        self._data = data
        self._data["move_button"] = self

    @property
    def available(self) -> bool:
        """Enable confirmation only when the player selector has a choice."""
        selected = self._data.get("selected_player")
        return bool(selected and selected in self._move_options())

    def _move_options(self) -> list[str]:
        """Return entity IDs eligible for moving to the destination server."""
        return [
            entity.entity_id
            for entity in _all_player_entities(self.hass)
            if entity.config_entry_id != self._destination_entry_id
        ]

    async def async_press(self) -> None:
        """Request the move, verify both LMS servers, and transfer the device."""
        # The selector and button are separate HA entities; resolve the current
        # selected value from the selector instance in this entry's platform.
        selected = self._data.get("selected_player")
        if selected is None or selected not in self._move_options():
            raise HomeAssistantError("Choose a player that can be moved first")

        entity = er.async_get(self.hass).async_get(selected)
        if entity is None or entity.config_entry_id == self._destination_entry_id:
            raise HomeAssistantError("The selected player is no longer on another LMS")
        source_entry_id = entity.config_entry_id

        if not await _async_move_player_to_server(
            self.hass, self._destination_entry_id, selected, source_entry_id
        ):
            raise HomeAssistantError("The destination LMS could not move the player")

        # LMS discovery can take time. Retry the same live status verification
        # used by the setup flow before changing the Squeezebox device registry.
        for attempt in range(24):
            if await _async_confirm_move_and_update_device(
                self.hass, selected, source_entry_id, self._destination_entry_id
            ):
                break
            if attempt == 23:
                raise HomeAssistantError(
                    "LMS has not confirmed the move yet. Check both servers and retry."
                )
            await asyncio.sleep(5)

        self._data["selected_player"] = None
        self.async_write_ha_state()


class LmsLibraryRescanButton(ButtonEntity):
    """Start a normal music library scan on the existing LMS connection."""

    _attr_has_entity_name = True
    _attr_name = "Scan music library"
    _attr_icon = "mdi:database-refresh"

    def __init__(self, entry_id: str, data: dict) -> None:
        self._server = data["server"]
        # Preserve the scan button's unique ID across the label change.
        self._attr_unique_id = f"{entry_id}_rescan_music_library"
        self._attr_device_info = data["device_info"]

    async def async_press(self) -> None:
        """Ask LMS to check its music library for changes."""
        try:
            response = await self._server.async_query("rescan")
        except Exception as err:
            raise HomeAssistantError("Could not start the LMS library scan") from err
        if response is None:
            raise HomeAssistantError("LMS did not acknowledge the library scan")


class LmsArmLibraryWipeButton(ButtonEntity):
    """Arm the destructive library wipe confirmation for a short period."""

    _attr_has_entity_name = True
    _attr_name = "Arm destructive library rebuild"
    _attr_icon = "mdi:database-lock"

    def __init__(self, hass: HomeAssistant, entry_id: str, data: dict) -> None:
        self.hass = hass
        self._data = data
        self._attr_unique_id = f"{entry_id}_arm_library_wipe"
        self._attr_device_info = data["device_info"]
        self._cancel_arm: Any | None = None
        self._data["arm_wipe_button"] = self

    async def async_press(self) -> None:
        """Arm one library wipe for 60 seconds."""
        self._data["library_wipe_armed_until"] = time.monotonic() + 60
        if self._cancel_arm:
            self._cancel_arm()
        self._cancel_arm = async_call_later(self.hass, 60, self._async_disarm)
        confirm = self._data.get("library_wipe_button")
        if confirm:
            confirm.async_write_ha_state()

    @callback
    def _async_disarm(self, _now: Any = None) -> None:
        self._data["library_wipe_armed_until"] = 0
        self._cancel_arm = None
        confirm = self._data.get("library_wipe_button")
        if confirm:
            confirm.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the arming timeout when the entry is unloaded."""
        if self._cancel_arm:
            self._cancel_arm()
        await super().async_will_remove_from_hass()


class LmsLibraryWipeButton(ButtonEntity):
    """Confirm wiping and rebuilding LMS's music library database."""

    _attr_has_entity_name = True
    _attr_name = "Confirm wipe and rebuild library"
    _attr_icon = "mdi:database-remove"

    def __init__(self, entry_id: str, data: dict) -> None:
        self._data = data
        self._server = data["server"]
        self._attr_unique_id = f"{entry_id}_wipe_music_library"
        self._attr_device_info = data["device_info"]
        self._data["library_wipe_button"] = self

    @property
    def available(self) -> bool:
        """Only allow a wipe after it has been explicitly armed."""
        return time.monotonic() < self._data.get("library_wipe_armed_until", 0)

    async def async_press(self) -> None:
        """Ask LMS to wipe and rebuild its music library database."""
        if not self.available:
            raise HomeAssistantError(
                "Arm the library wipe first; confirmation expires after 60 seconds"
            )
        self._data["library_wipe_armed_until"] = 0
        arm_button = self._data.get("arm_wipe_button")
        if arm_button:
            arm_button._async_disarm()
        try:
            response = await self._server.async_query("wipecache")
        except Exception as err:
            raise HomeAssistantError("Could not start the LMS library rebuild") from err
        if response is None:
            raise HomeAssistantError("LMS did not acknowledge the library rebuild")


class LmsPlaylistRescanButton(ButtonEntity):
    """Start a playlist-only scan on the existing LMS connection."""

    _attr_has_entity_name = True
    _attr_name = "Rescan playlists"
    _attr_icon = "mdi:playlist-refresh"

    def __init__(self, entry_id: str, data: dict) -> None:
        self._server = data["server"]
        self._attr_unique_id = f"{entry_id}_rescan_playlists"
        self._attr_device_info = data["device_info"]

    async def async_press(self) -> None:
        """Ask LMS to scan playlist files without a full library rescan."""
        try:
            response = await self._server.async_query("rescan", "playlists")
        except Exception as err:
            raise HomeAssistantError("Could not start the LMS playlist scan") from err
        if response is None:
            raise HomeAssistantError("LMS did not acknowledge the playlist scan")
