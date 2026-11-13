"""Daily alarm time control for a Squeezebox player."""

from __future__ import annotations

from datetime import time as dt_time
from time import monotonic
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the player's daily alarm time control."""
    data = hass.data[DOMAIN][entry.entry_id]
    if data.get("server_controls"):
        return
    async_add_entities(
        [LmsDailyAlarmTime(data["coordinator"], entry.entry_id, data["device_info"])]
    )


class LmsDailyAlarmTime(TimeEntity):
    """Set the first daily alarm, creating an enabled daily alarm if needed."""

    _attr_has_entity_name = True
    _attr_name = "Daily alarm time"
    _attr_icon = "mdi:alarm"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: Any, entry_id: str, device_info: dict) -> None:
        self._coordinator = coordinator
        self._player = coordinator.player
        self._attr_unique_id = f"{entry_id}_daily_alarm_time"
        self._attr_device_info = device_info
        self._alarm_id: str | None = None
        self._value: dt_time | None = None
        self._last_read = 0.0

    @property
    def available(self) -> bool:
        """Keep the control available when no alarm exists so one can be added."""
        return self._coordinator.last_update_success

    @property
    def native_value(self) -> dt_time | None:
        """Return the configured daily alarm time."""
        return self._value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            if monotonic() - self._last_read >= 60:
                self.hass.async_create_task(self._async_read_alarm())

        self.async_on_remove(
            self._coordinator.async_add_listener(_handle_player_update)
        )
        await self._async_read_alarm()

    async def _async_read_alarm(self) -> None:
        """Read all alarms and select the earliest daily alarm for editing."""
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            return
        self._last_read = monotonic()
        try:
            response = await query("alarms", "0", "100", "filter:all")
        except Exception:  # noqa: BLE001 - keep the last alarm value on LMS read errors
            return

        rows = response.get("alarms_loop") if isinstance(response, dict) else None
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not isinstance(rows, list):
            return

        daily: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                seconds = int(row.get("time"))
            except (TypeError, ValueError):
                continue
            if 0 <= seconds < 86400 and row.get("id") is not None:
                daily.append((seconds, row))

        if not daily:
            self._alarm_id = None
            self._value = None
        else:
            seconds, alarm = min(
                daily,
                key=lambda item: (
                    str(item[1].get("enabled", "0")) not in ("1", "true", "True"),
                    item[0],
                ),
            )
            self._alarm_id = str(alarm["id"])
            self._value = dt_time(hour=seconds // 3600, minute=(seconds % 3600) // 60)
        self.async_write_ha_state()

    async def async_set_value(self, value: dt_time) -> None:
        """Update the earliest daily alarm or create an enabled daily alarm."""
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            raise HomeAssistantError("The LMS player query API is unavailable")

        seconds = value.hour * 3600 + value.minute * 60
        try:
            if self._alarm_id is None:
                await query("alarm", "add", f"time:{seconds}", "enabled:1")
            else:
                await query(
                    "alarm", "update", f"id:{self._alarm_id}", f"time:{seconds}"
                )
        except Exception as err:
            raise HomeAssistantError("Could not set the LMS daily alarm") from err

        await self._coordinator.async_request_refresh()
        self._last_read = 0
        await self._async_read_alarm()
        if self._value != value:
            raise HomeAssistantError("LMS did not confirm the selected alarm time")
