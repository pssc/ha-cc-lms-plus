"""Controls for the selected Squeezebox player."""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .server_controls import LmsMovePlayerSelect
from .util import lms_response_value

_LOGGER = logging.getLogger(__name__)

RATING_OPTIONS = ["No rating", "★", "★★", "★★★", "★★★★", "★★★★★"]
RATING_VALUES = {option: index * 20 for index, option in enumerate(RATING_OPTIONS)}

CROSSFADE_OPTIONS = {
    "Crossfade between tracks": "0",
    "Fade in at the start of each track": "1",
    "Fade out at the end of each track": "2",
    "Fade out, then in between tracks": "3",
    "No transition fading": "4",
}
REPLAYGAIN_OPTIONS = {
    "Off (leave playback volume unadjusted)": "0",
    "Track gain (adjust each track independently)": "1",
    "Album gain (preserve loudness differences within an album)": "2",
    "By playback order (track gain for shuffle, album gain in album order)": "3",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up player settings or the server-level player selector."""
    data = hass.data[DOMAIN][entry.entry_id]
    if data.get("server_controls"):
        async_add_entities([LmsMovePlayerSelect(hass, entry.entry_id, data)])
        return

    entity_registry = er.async_get(hass)
    for suffix in ("repeat_mode", "shuffle_mode"):
        entity_id = entity_registry.async_get_entity_id(
            "select", DOMAIN, f"{entry.entry_id}_{suffix}"
        )
        if entity_id is None:
            continue
        registry_entry = entity_registry.async_get(entity_id)
        if registry_entry and registry_entry.config_entry_id == entry.entry_id:
            # These controls are now provided by the built-in media player.
            entity_registry.async_remove(entity_id)

    async_add_entities(
        [
            LmsCurrentTrackRatingSelect(
                data["coordinator"],
                data["player_entity"],
                entry.entry_id,
                data["device_info"],
            ),
            LmsPlayerPreferenceSelect(
                data["coordinator"],
                entry.entry_id,
                data["device_info"],
                name="Crossfade",
                icon="mdi:transition",
                preference="transitionType",
                options=CROSSFADE_OPTIONS,
            ),
            LmsPlayerPreferenceSelect(
                data["coordinator"],
                entry.entry_id,
                data["device_info"],
                name="ReplayGain",
                icon="mdi:volume-equal",
                preference="replayGainMode",
                options=REPLAYGAIN_OPTIONS,
            ),
        ]
    )


class LmsPlayerPreferenceSelect(SelectEntity):
    """Read and set an LMS player preference over the existing connection."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: Any,
        entry_id: str,
        device_info: dict,
        *,
        name: str,
        icon: str,
        preference: str,
        options: dict[str, str],
    ) -> None:
        self._coordinator = coordinator
        self._player = coordinator.player
        self._preference = preference
        self._option_values = options
        self._attr_name = name
        self._attr_icon = icon
        self._attr_options = list(options)
        self._attr_unique_id = f"{entry_id}_{preference}"
        self._attr_device_info = device_info
        self._value: str | None = None
        self._last_read = 0.0

    @property
    def available(self) -> bool:
        return self._coordinator.last_update_success and self._value is not None

    @property
    def current_option(self) -> str | None:
        return next(
            (
                option
                for option, value in self._option_values.items()
                if value == self._value
            ),
            None,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            # Refresh occasionally so changes made in the LMS web interface
            # are reflected without querying the server on every coordinator
            # update.
            if monotonic() - self._last_read >= 60:
                self.hass.async_create_task(self._async_read_value())

        self.async_on_remove(
            self._coordinator.async_add_listener(_handle_player_update)
        )
        await self._async_read_value()

    async def _async_read_value(self) -> None:
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            return
        self._last_read = monotonic()
        try:
            result = await query("playerpref", self._preference, "?")
        except Exception:  # noqa: BLE001 - retain the last good value on read errors
            return
        value = lms_response_value(result)
        if value is not None:
            self._value = str(value)
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            raise HomeAssistantError("The LMS player query API is unavailable")
        value = self._option_values[option]
        try:
            validation = await query("playerpref", "validate", self._preference, value)
            is_valid = lms_response_value(validation)
            if is_valid is None or str(is_valid) != "1":
                raise HomeAssistantError(
                    f"LMS rejected the selected {self.name} setting"
                )
            command_result = await query("playerpref", self._preference, value)
            query_result = await query("playerpref", self._preference, "?")
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Could not set the LMS {self.name} preference"
            ) from err
        confirmed = lms_response_value(query_result)
        command_value = lms_response_value(command_result)
        if (confirmed is not None and str(confirmed) != value) or (
            command_value is not None and str(command_value) != value
        ):
            raise HomeAssistantError(
                f"LMS did not confirm the selected {self.name} setting"
            )
        self._value = value
        self._last_read = monotonic()
        self.async_write_ha_state()


class LmsCurrentTrackRatingSelect(SelectEntity):
    """Show and set the LMS rating of the player's currently playing track."""

    _attr_has_entity_name = True
    _attr_name = "Current track rating"
    _attr_icon = "mdi:star"
    _attr_options = RATING_OPTIONS

    def __init__(
        self,
        coordinator: Any,
        player_entity: str,
        entry_id: str,
        device_info: dict,
    ) -> None:
        """Initialize the control with the existing Squeezebox player."""
        self._coordinator = coordinator
        self._player = coordinator.player
        self._player_entity = player_entity
        self._attr_unique_id = f"{entry_id}_current_track_rating"
        self._attr_device_info = device_info
        self._current_track_id: str | None = None
        self._rating: int | None = None
        self._metadata_by_index: dict[int, dict[str, Any]] = {}
        self._remove_update_listener: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Only offer the control while the selected player is available."""
        return self._coordinator.last_update_success and self._track_id() is not None

    @property
    def current_option(self) -> str | None:
        """Return the current LMS rating as a star choice."""
        track_id = self._track_id()
        if track_id is None:
            return None
        if track_id == self._current_track_id and self._rating is not None:
            rating = self._rating
        else:
            current_index = self._player.current_index
            metadata = self._metadata_by_index.get(current_index, {})
            if str(metadata.get("id", "")) != track_id:
                return None
            try:
                rating = int(float(metadata.get("rating", 0)))
            except (TypeError, ValueError):
                return None
        stars = min(5, max(0, round(rating / 20)))
        return RATING_OPTIONS[stars]

    def _track_id(self) -> str | None:
        """Return the database ID for the current local track, if available."""
        try:
            track = self._player.current_track
        except (IndexError, KeyError, TypeError):
            return None
        if not isinstance(track, dict) or track.get("id") in (None, ""):
            return None
        return str(track["id"])

    async def async_added_to_hass(self) -> None:
        """Read the current track rating and monitor track changes."""
        await super().async_added_to_hass()

        @callback
        def _handle_player_update() -> None:
            self.async_write_ha_state()

        @callback
        def _handle_metadata_update(event: Any) -> None:
            if event.data.get("player_entity") != self._player_entity:
                return
            raw_metadata = event.data.get("metadata_by_index", {})
            if not isinstance(raw_metadata, dict):
                return
            self._metadata_by_index = {
                int(index): value
                for index, value in raw_metadata.items()
                if str(index).lstrip("-").isdigit() and isinstance(value, dict)
            }
            self.async_write_ha_state()

        self._remove_update_listener = self._coordinator.async_add_listener(
            _handle_player_update
        )
        self.async_on_remove(self._remove_update_listener)
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_playlist_metadata_updated", _handle_metadata_update
            )
        )

    async def async_select_option(self, option: str) -> None:
        """Set the current local track's LMS rating using five-star steps."""
        track_id = self._track_id()
        if track_id is None:
            raise HomeAssistantError("There is no rateable current LMS track")

        rating = RATING_VALUES[option]
        # Use the existing player connection so LMS receives its player ID.
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            raise HomeAssistantError("The LMS player query API is unavailable")
        saved_rating: int | None = None
        try:
            result = await query(
                "ratingslight",
                "setratingpercent",
                f"track_id:{track_id}",
                f"rating:{rating}",
            )
            saved_rating = _rating_percentage(result)
        except Exception as err:  # noqa: BLE001 - try TrackStat if Ratings Light fails
            # Ratings Light is optional. Try TrackStat when its command is
            # unavailable or it cannot confirm the saved value.
            _LOGGER.debug("Ratings Light write failed; trying TrackStat: %s", err)

        if saved_rating != rating:
            try:
                result = await query("trackstat", "setrating", track_id, f"{rating}%")
                saved_rating = _rating_percentage(result)
            except Exception as err:
                raise HomeAssistantError(
                    "Could not set the rating with Ratings Light or TrackStat"
                ) from err
        if saved_rating != rating:
            raise HomeAssistantError(
                "Neither Ratings Light nor TrackStat confirmed the track rating. "
                "Check that at least one plugin is installed and enabled."
            )

        self.hass.bus.async_fire(
            f"{DOMAIN}_track_rating_updated",
            {"player_entity": self._player_entity, "track_id": track_id},
        )
        self._current_track_id = track_id
        self._rating = rating
        self.async_write_ha_state()


def _rating_percentage(result: Any) -> int | None:
    """Return the percentage rating confirmed by an LMS plugin response."""
    if not isinstance(result, dict):
        return None
    try:
        return int(float(result["ratingpercentage"]))
    except (KeyError, TypeError, ValueError):
        return None
