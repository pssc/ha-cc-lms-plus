"""Numeric controls for the selected Squeezebox player."""

from __future__ import annotations

from time import monotonic
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .util import lms_response_value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the LMS sleep timer control for a selected player."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LmsSleepTimerNumber(
                data["coordinator"],
                entry.entry_id,
                data["device_info"],
            )
        ]
    )


class LmsSleepTimerNumber(NumberEntity):
    """Set the number of minutes before LMS powers off the player."""

    _attr_has_entity_name = True
    _attr_name = "Sleep timer"
    _attr_icon = "mdi:timer-sand"
    _attr_native_min_value = 0
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: Any, entry_id: str, device_info: dict) -> None:
        self._coordinator = coordinator
        self._player = coordinator.player
        self._attr_unique_id = f"{entry_id}_sleep_timer"
        self._attr_device_info = device_info
        self._remaining_seconds: float | None = None
        self._last_read = 0.0

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        if self._remaining_seconds is None:
            return None
        return round(self._remaining_seconds / 60)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            # LMS exposes the remaining sleep time in player status. Query at
            # most once per minute rather than on every coordinator update.
            if monotonic() - self._last_read >= 60:
                self.hass.async_create_task(self._async_read_timer())

        self.async_on_remove(
            self._coordinator.async_add_listener(_handle_player_update)
        )
        await self._async_read_timer()

    async def _async_read_timer(self) -> None:
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            return
        self._last_read = monotonic()
        try:
            result = await query("sleep", "?")
        except Exception:  # noqa: BLE001 - keep a failed read as an unknown value
            return
        raw_value = lms_response_value(result)
        try:
            self._remaining_seconds = max(0, float(raw_value))
        except (TypeError, ValueError):
            self._remaining_seconds = None
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            raise HomeAssistantError("The LMS player query API is unavailable")
        seconds = round(value * 60)
        try:
            await query("sleep", str(seconds))
        except Exception as err:
            raise HomeAssistantError("Could not set the LMS sleep timer") from err
        await self._async_read_timer()
