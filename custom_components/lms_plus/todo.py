"""Playlist To-do list backed by a Squeezebox player."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PLAYLIST_DESCRIPTION_FORMAT,
    CONF_PLAYLIST_SUMMARY_FORMAT,
    DEFAULT_PLAYLIST_DESCRIPTION_FORMAT,
    DEFAULT_PLAYLIST_SUMMARY_FORMAT,
    DOMAIN,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the playlist To-do list for a selected player."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LmsPlaylistTodoList(
                data["coordinator"],
                data["player_entity"],
                entry.entry_id,
                data["device_info"],
                entry.options,
            )
        ]
    )


class LmsPlaylistTodoList(TodoListEntity):
    """Expose and control the player's live playlist through a To-do list."""

    _attr_has_entity_name = True
    _attr_name = "Playlist"
    _attr_icon = "mdi:playlist-music"
    _attr_supported_features = (
        TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator: Any,
        player_entity: str,
        entry_id: str,
        device_info: dict,
        options: dict,
    ) -> None:
        """Initialize the entity with the existing Squeezebox player."""
        self._coordinator = coordinator
        self._player = coordinator.player
        self._player_entity = player_entity
        self._attr_unique_id = f"{entry_id}_playlist"
        self._attr_device_info = device_info
        self._summary_format = options.get(
            CONF_PLAYLIST_SUMMARY_FORMAT, DEFAULT_PLAYLIST_SUMMARY_FORMAT
        )
        self._description_format = options.get(
            CONF_PLAYLIST_DESCRIPTION_FORMAT, DEFAULT_PLAYLIST_DESCRIPTION_FORMAT
        )
        self._metadata_by_index: dict[int, dict[str, Any]] = {}
        self._playlist_signature: tuple[tuple[Any, Any], ...] | None = None
        self._rating_refresh_task: asyncio.Task[None] | None = None
        self._rating_refresh_pending = False
        self._last_rating_refresh = 0.0
        self._current_track_marker = self._get_current_track_marker()
        self._attr_todo_items: list[TodoItem] | None = None
        self._remove_update_listener: Callable[[], None] | None = None
        self._refresh_items()

    @property
    def available(self) -> bool:
        """Only report an available list when the source player is available."""
        return self._coordinator.last_update_success

    @property
    def entity_picture(self) -> str | None:
        """Use the player's current artwork as the playlist's entity picture."""
        player_state = self.hass.states.get(self._player_entity)
        if player_state is None:
            return None
        picture = player_state.attributes.get("entity_picture")
        return picture if isinstance(picture, str) else None

    def _refresh_items(self) -> bool:
        """Build ordered To-do items from the player's current LMS status."""
        status = getattr(self._player, "_status", {})
        if not isinstance(status, dict):
            self._reset_playlist_metadata(None)
            changed = self._attr_todo_items is not None
            self._attr_todo_items = None
            return changed

        playlist = status.get("playlist_loop")
        if not isinstance(playlist, list):
            self._reset_playlist_metadata(None)
            changed = self._attr_todo_items is not None
            self._attr_todo_items = None
            return changed

        self._reset_playlist_metadata(_playlist_signature(playlist))

        try:
            current_index = int(status.get("playlist_cur_index", -1))
        except (TypeError, ValueError):
            current_index = -1
        playback_mode = status.get("mode")

        items: list[TodoItem] = []
        for index, track in enumerate(playlist):
            if not isinstance(track, dict):
                continue

            title = _track_text(track, "title") or "Unknown track"
            try:
                track_index = int(track.get("playlist index", index))
            except (TypeError, ValueError):
                track_index = index

            is_current = track_index == current_index
            fields = _track_format_fields(
                track,
                title,
                track_index,
                self._metadata_by_index.get(track_index, {}),
            )
            summary = _render_format(self._summary_format, fields)
            if not summary:
                summary = title
            if is_current and playback_mode == "play":
                summary = f"▶ {summary} (now playing)"
            elif is_current and playback_mode == "pause":
                summary = f"⏸ {summary} (paused)"
            elif is_current:
                summary = f"■ {summary} (current track)"

            description = _render_format(self._description_format, fields)
            artwork = _track_text(track, "artwork_url")
            if artwork:
                description = (
                    f"{description}\nArtwork: {artwork}"
                    if description
                    else f"Artwork: {artwork}"
                )

            items.append(
                TodoItem(
                    uid=_playlist_item_uid(track, track_index),
                    summary=summary,
                    status=TodoItemStatus.NEEDS_ACTION,
                    description=description,
                )
            )

        if items == self._attr_todo_items:
            return False
        self._attr_todo_items = items
        return True

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Play a playlist track when its To-do item is marked complete."""
        if item.uid is None:
            raise HomeAssistantError("The playlist item has no identifier")

        current_item = next(
            (
                todo_item
                for todo_item in self._attr_todo_items or []
                if todo_item.uid == item.uid
            ),
            None,
        )
        if current_item is None:
            raise HomeAssistantError("The playlist changed; refresh and retry")
        if (
            item.summary != current_item.summary
            or item.description != current_item.description
        ):
            raise HomeAssistantError("Playlist item text cannot be edited")
        if item.status != TodoItemStatus.COMPLETED:
            return

        if not await self._player.async_update():
            raise HomeAssistantError("Could not refresh the LMS playlist")
        track_index = _find_playlist_index(item.uid, self._current_playlist())
        if self._player.current_index == track_index:
            succeeded = self._player.mode == "play" or await self._player.async_play()
        else:
            succeeded = await self._player.async_index(track_index)
        if not succeeded:
            raise HomeAssistantError("LMS could not play the selected playlist item")
        await self._refresh_player_playlist()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Remove the selected items from the LMS playlist."""
        if not await self._player.async_update():
            raise HomeAssistantError("Could not refresh the LMS playlist")
        playlist = self._current_playlist()
        indexes = sorted(
            {_find_playlist_index(uid, playlist) for uid in uids}, reverse=True
        )
        command = getattr(self._player, "async_command", None)
        if not callable(command):
            raise HomeAssistantError("The LMS player command API is unavailable")

        for index in indexes:
            if not await command("playlist", "delete", str(index)):
                await self._refresh_player_playlist()
                raise HomeAssistantError("LMS could not remove the playlist item")
        if indexes:
            await self._refresh_player_playlist()

    async def async_move_todo_item(
        self, uid: str, previous_uid: str | None = None
    ) -> None:
        """Move a To-do item to the corresponding position in the LMS playlist."""
        if not await self._player.async_update():
            raise HomeAssistantError("Could not refresh the LMS playlist")
        playlist = self._current_playlist()
        from_index = _find_playlist_index(uid, playlist)
        previous_index = (
            _find_playlist_index(previous_uid, playlist)
            if previous_uid is not None
            else None
        )

        if previous_index is None:
            to_index = 0
        elif from_index < previous_index:
            to_index = previous_index
        else:
            to_index = previous_index + 1
        if from_index == to_index:
            return

        command = getattr(self._player, "async_command", None)
        if not callable(command):
            raise HomeAssistantError("The LMS player command API is unavailable")
        if not await command("playlist", "move", str(from_index), str(to_index)):
            await self._refresh_player_playlist()
            raise HomeAssistantError("LMS could not reorder the playlist")
        await self._refresh_player_playlist()

    def _current_playlist(self) -> list[dict[str, Any]]:
        """Return the latest playlist snapshot from the selected player."""
        status = getattr(self._player, "_status", {})
        playlist = status.get("playlist_loop") if isinstance(status, dict) else None
        if not isinstance(playlist, list):
            raise HomeAssistantError("The LMS playlist is not available")
        return playlist

    async def _refresh_player_playlist(self) -> None:
        """Refresh the existing Squeezebox coordinator after a queue change."""
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self) -> None:
        """Keep the list synchronized with the Squeezebox coordinator."""
        await super().async_added_to_hass()

        self._schedule_rating_refresh(force=True)

        @callback
        def _handle_player_update() -> None:
            current_track_marker = self._get_current_track_marker()
            track_changed = current_track_marker != self._current_track_marker
            self._current_track_marker = current_track_marker
            if self._refresh_items():
                self.async_write_ha_state()
            self._schedule_rating_refresh(force=track_changed)

        @callback
        def _handle_rating_update(event: Any) -> None:
            if event.data.get("player_entity") == self._player_entity:
                self._schedule_rating_refresh(force=True)

        self._remove_update_listener = self._coordinator.async_add_listener(
            _handle_player_update
        )
        self.async_on_remove(self._remove_update_listener)
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_track_rating_updated", _handle_rating_update
            )
        )
        self.async_on_remove(self._cancel_rating_refresh)

    @callback
    def _cancel_rating_refresh(self) -> None:
        """Cancel a pending LMS query when the entity is removed."""
        self._rating_refresh_pending = False
        if self._rating_refresh_task and not self._rating_refresh_task.done():
            self._rating_refresh_task.cancel()

    @callback
    def _schedule_rating_refresh(self, *, force: bool = False) -> None:
        """Refresh ratings on track changes and throttle routine updates."""
        if self._rating_refresh_task and not self._rating_refresh_task.done():
            if force:
                self._rating_refresh_pending = True
            return
        if not force and time.monotonic() - self._last_rating_refresh < 60:
            return
        self._last_rating_refresh = time.monotonic()
        self._rating_refresh_task = self.hass.async_create_task(
            self._async_refresh_ratings()
        )
        self._rating_refresh_task.add_done_callback(self._rating_refresh_done)

    @callback
    def _rating_refresh_done(self, task: asyncio.Task[None]) -> None:
        """Run a forced refresh queued during an in-flight LMS request."""
        if self._rating_refresh_task is task:
            self._rating_refresh_task = None
        if self._rating_refresh_pending:
            self._rating_refresh_pending = False
            self._schedule_rating_refresh(force=True)

    def _get_current_track_marker(self) -> tuple[int, Any]:
        """Return a stable marker for the current playlist row."""
        status = getattr(self._player, "_status", {})
        if not isinstance(status, dict):
            return (-1, None)
        try:
            current_index = int(status.get("playlist_cur_index", -1))
        except (TypeError, ValueError):
            current_index = -1
        playlist = status.get("playlist_loop")
        if isinstance(playlist, list):
            for index, track in enumerate(playlist):
                if not isinstance(track, dict):
                    continue
                try:
                    track_index = int(track.get("playlist index", index))
                except (TypeError, ValueError):
                    track_index = index
                if track_index == current_index:
                    return (current_index, track.get("id", track.get("title")))
        return (current_index, None)

    def _reset_playlist_metadata(
        self, signature: tuple[tuple[Any, Any], ...] | None
    ) -> None:
        """Discard supplemental LMS tags when the queue contents change."""
        if signature == self._playlist_signature:
            return
        self._playlist_signature = signature
        self._metadata_by_index = {}
        self._last_rating_refresh = 0

    async def _async_refresh_ratings(self) -> None:
        """Ask LMS for its R tag and apply ratings to matching playlist rows."""
        query = getattr(self._player, "async_query", None)
        if not callable(query):
            return

        requested_signature = self._playlist_signature
        try:
            result = await query("status", "0", "100", "tags:Rty")
        except Exception:  # noqa: BLE001 - supplemental tags must not hide the queue
            return
        if not isinstance(result, dict):
            return

        playlist = result.get("playlist_loop")
        if (
            not isinstance(playlist, list)
            or requested_signature != self._playlist_signature
        ):
            return

        metadata_by_index: dict[int, dict[str, Any]] = {}
        for index, track in enumerate(playlist):
            if not isinstance(track, dict):
                continue
            try:
                track_index = int(track.get("playlist index", index))
            except (TypeError, ValueError):
                track_index = index
            metadata_by_index[track_index] = {
                field: track[field]
                for field in ("id", "rating", "tracknum", "year")
                if track.get(field) not in (None, "")
            }

        self.hass.bus.async_fire(
            f"{DOMAIN}_playlist_metadata_updated",
            {
                "player_entity": self._player_entity,
                "metadata_by_index": metadata_by_index,
            },
        )

        if metadata_by_index == self._metadata_by_index:
            return
        self._metadata_by_index = metadata_by_index
        if self._refresh_items():
            self.async_write_ha_state()


def _playlist_track_index(track: dict[str, Any], fallback_index: int) -> int:
    """Read an LMS playlist index, using its position as a fallback."""
    try:
        return int(track.get("playlist index", fallback_index))
    except (TypeError, ValueError):
        return fallback_index


def _playlist_item_uid(track: dict[str, Any], fallback_index: int) -> str:
    """Build the stable To-do identifier used to resolve an LMS playlist row."""
    return f"{_playlist_track_index(track, fallback_index)}:{track.get('id', '')}"


def _find_playlist_index(uid: str, playlist: list[dict[str, Any]]) -> int:
    """Resolve a To-do item ID against the current LMS playlist snapshot."""
    for position, track in enumerate(playlist):
        if isinstance(track, dict) and _playlist_item_uid(track, position) == uid:
            return _playlist_track_index(track, position)
    raise HomeAssistantError("The playlist changed; refresh and retry")


def _track_text(track: dict[str, Any], key: str) -> str | None:
    """Return a non-empty string field from an LMS track."""
    value = track.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first_track_value(track: dict[str, Any], *keys: str) -> Any:
    """Return the first populated value among equivalent LMS track keys."""
    for key in keys:
        value = track.get(key)
        if value is not None and value != "":
            return value
    return None


def _track_format_fields(
    track: dict[str, Any],
    title: str,
    track_index: int,
    fetched_metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Map the LMS track data to the supported playlist format fields."""
    duration = _first_track_value(track, "duration")
    try:
        duration_text = (
            _format_duration(float(duration)) if duration is not None else ""
        )
    except (TypeError, ValueError):
        duration_text = str(duration) if duration is not None else ""

    fetched_metadata = fetched_metadata or {}
    rating = _first_track_value(track, "rating") or fetched_metadata.get("rating")
    rating_text = _format_rating(rating)
    values = {
        "track_number": str(
            _first_track_value(track, "tracknum", "track_number", "tracknumber")
            or fetched_metadata.get("tracknum")
            or ""
        ),
        "trackname": title,
        "rating": rating_text,
        "artist": _track_text(track, "artist") or "",
        "album": _track_text(track, "album") or "",
        "year": str(
            _first_track_value(track, "year", "date")
            or fetched_metadata.get("year")
            or ""
        ),
        "bitrate": str(_first_track_value(track, "bitrate") or ""),
        "format": str(_first_track_value(track, "type", "format") or ""),
        "duration": duration_text,
    }
    return values


def _playlist_signature(
    playlist: list[dict[str, Any]],
) -> tuple[tuple[Any, Any], ...]:
    """Identify playlist contents so supplemental results cannot go stale."""
    return tuple(
        (track.get("playlist index", index), track.get("id"))
        for index, track in enumerate(playlist)
        if isinstance(track, dict)
    )


def _render_format(template: str, fields: dict[str, str]) -> str:
    """Render a format while omitting blank values and their empty lines."""
    try:
        rendered = template.format_map(
            {key: value or "" for key, value in fields.items()}
        )
    except (KeyError, ValueError):
        return ""
    lines = []
    for raw_line in rendered.splitlines():
        line = re.sub(r"\s+\(\s*\)", "", raw_line.strip())
        line = re.sub(r"^[.,:;|–—-]\s*", "", line)
        line = re.sub(r"\s+[.,:;|–—-]$", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    """Format seconds as minutes and seconds, or hours when necessary."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def _format_rating(value: Any) -> str:
    """Render LMS's numeric rating as stars when the scale is recognizable."""
    if value is None or value == "":
        return ""
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return str(value)
    if rating <= 0:
        return ""
    stars = round(rating / 20) if rating > 5 else round(rating)
    return "★" * min(5, max(1, stars))
