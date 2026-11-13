"""Config flow for LMS Plus."""

from __future__ import annotations

import logging
from inspect import signature
from string import Formatter

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_PLAYER,
    CONF_PLAYLIST_DESCRIPTION_FORMAT,
    CONF_PLAYLIST_SUMMARY_FORMAT,
    CONF_SERVER_CONTROLS,
    CONF_SQUEEZEBOX_ENTRY,
    DEFAULT_PLAYLIST_DESCRIPTION_FORMAT,
    DEFAULT_PLAYLIST_SUMMARY_FORMAT,
    DOMAIN,
)

_PLAYLIST_FORMAT_FIELDS = {
    "track_number",
    "trackname",
    "rating",
    "artist",
    "album",
    "year",
    "bitrate",
    "format",
    "duration",
}

_LOGGER = logging.getLogger(__name__)


class LmsPlusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LMS Plus."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Select the destination Squeezebox server."""
        squeezebox_entries = _loaded_squeezebox_entries(self.hass)
        if not squeezebox_entries:
            return self.async_abort(reason="no_squeezebox_config")

        if user_input is not None:
            self._squeezebox_entry_id = user_input[CONF_SQUEEZEBOX_ENTRY]
            if user_input.get(CONF_SERVER_CONTROLS):
                return await self._create_server_entry()
            return await self.async_step_player()

        entries = {entry.entry_id: entry.title for entry in squeezebox_entries}
        schema = vol.Schema(
            {
                vol.Required(CONF_SQUEEZEBOX_ENTRY): vol.In(entries),
                vol.Optional(CONF_SERVER_CONTROLS, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _create_server_entry(self):
        """Create one set of server controls for a destination LMS."""
        await self.async_set_unique_id(f"server_{self._squeezebox_entry_id}")
        self._abort_if_unique_id_configured()
        server = self.hass.config_entries.async_get_entry(self._squeezebox_entry_id)
        return self.async_create_entry(
            title=f"{server.title if server else 'LMS'} — LMS Plus controls",
            data={
                CONF_SQUEEZEBOX_ENTRY: self._squeezebox_entry_id,
                CONF_SERVER_CONTROLS: True,
            },
        )

    async def async_step_player(self, user_input=None):
        """Select a player and offer to move it from another LMS server."""
        player_entities = _all_player_entities(self.hass)
        if not player_entities:
            return self.async_abort(reason="no_players")

        player_options = {
            entity.entity_id: _player_option_label(
                self.hass, entity, self._squeezebox_entry_id
            )
            for entity in player_entities
        }
        if user_input is not None:
            player = user_input[CONF_PLAYER]
            entity = next(
                (item for item in player_entities if item.entity_id == player), None
            )
            if entity is None:
                return self.async_abort(reason="player_not_found")
            if entity.config_entry_id != self._squeezebox_entry_id:
                self._pending_player = player
                self._source_squeezebox_entry_id = entity.config_entry_id
                return await self.async_step_move_player()
            return await self._create_player_entry(player)

        schema = vol.Schema({vol.Required(CONF_PLAYER): vol.In(player_options)})
        return self.async_show_form(step_id="player", data_schema=schema)

    async def async_step_move_player(self, user_input=None):
        """Confirm and request a move from the selected destination LMS."""
        if user_input is not None:
            if not user_input["confirm"]:
                return self.async_abort(reason="move_cancelled")
            if not await _async_move_player_to_server(
                self.hass,
                self._squeezebox_entry_id,
                self._pending_player,
                self._source_squeezebox_entry_id,
            ):
                return self.async_show_form(
                    step_id="move_player",
                    data_schema=_move_confirmation_schema(),
                    errors={"base": "move_failed"},
                )
            return await self.async_step_wait_for_player()

        return self.async_show_form(
            step_id="move_player", data_schema=_move_confirmation_schema()
        )

    async def async_step_wait_for_player(self, user_input=None):
        """Verify the LMS move, update the device, and wait for discovery."""
        if user_input is not None and not await _async_confirm_move_and_update_device(
            self.hass,
            self._pending_player,
            self._source_squeezebox_entry_id,
            self._squeezebox_entry_id,
        ):
            return self.async_show_form(
                step_id="wait_for_player",
                data_schema=vol.Schema({}),
                errors={"base": "move_not_verified"},
            )
        if _player_belongs_to_server(
            self.hass, self._pending_player, self._squeezebox_entry_id
        ):
            return await self._create_player_entry(self._pending_player)
        if user_input is not None:
            return self.async_show_form(
                step_id="wait_for_player",
                data_schema=vol.Schema({}),
                errors={"base": "player_not_discovered"},
            )
        return self.async_show_form(
            step_id="wait_for_player", data_schema=vol.Schema({})
        )

    async def _create_player_entry(self, player: str):
        """Create an entry after the Squeezebox integration owns this player."""
        if not _player_belongs_to_server(self.hass, player, self._squeezebox_entry_id):
            return self.async_abort(reason="player_not_on_server")
        await self.async_set_unique_id(player)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=_config_entry_title(self.hass, self._squeezebox_entry_id, player),
            data={
                CONF_SQUEEZEBOX_ENTRY: self._squeezebox_entry_id,
                CONF_PLAYER: player,
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the playlist formatting options flow."""
        return LmsPlusOptionsFlow()


class LmsPlusOptionsFlow(config_entries.OptionsFlow):
    """Select a Squeezebox player and configure playlist item formats."""

    async def async_step_init(self, user_input=None):
        """Select the Squeezebox server for this LMS Plus entry."""
        if self.config_entry.data.get(CONF_SERVER_CONTROLS):
            return self.async_abort(reason="server_controls_use_setup")
        squeezebox_entries = _squeezebox_entries_with_players(self.hass)
        if not squeezebox_entries:
            return self.async_abort(reason="no_squeezebox_config")

        if user_input is not None:
            self._squeezebox_entry_id = user_input[CONF_SQUEEZEBOX_ENTRY]
            return await self.async_step_player()

        current_player = self.config_entry.options.get(
            CONF_PLAYER, self.config_entry.data[CONF_PLAYER]
        )
        current_entity = er.async_get(self.hass).async_get(current_player)
        default_server = self.config_entry.options.get(
            CONF_SQUEEZEBOX_ENTRY,
            self.config_entry.data.get(
                CONF_SQUEEZEBOX_ENTRY,
                current_entity.config_entry_id if current_entity else None,
            ),
        )
        entries = {entry.entry_id: entry.title for entry in squeezebox_entries}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SQUEEZEBOX_ENTRY,
                    default=(
                        default_server
                        if default_server in entries
                        else next(iter(entries))
                    ),
                ): vol.In(entries)
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_player(self, user_input=None):
        """Select a player and offer to move it from another LMS server."""
        player_entities = _all_player_entities(self.hass)
        if not player_entities:
            return self.async_abort(reason="no_players")

        player_options = {
            entity.entity_id: _player_option_label(
                self.hass, entity, self._squeezebox_entry_id
            )
            for entity in player_entities
        }
        if user_input is not None:
            self._player_entity = user_input[CONF_PLAYER]
            entity = next(
                (
                    item
                    for item in player_entities
                    if item.entity_id == self._player_entity
                ),
                None,
            )
            if entity is None:
                return self.async_abort(reason="player_not_found")
            if entity.config_entry_id != self._squeezebox_entry_id:
                self._source_squeezebox_entry_id = entity.config_entry_id
                return await self.async_step_move_player()
            return await self.async_step_formats()

        current_player = self.config_entry.options.get(
            CONF_PLAYER, self.config_entry.data[CONF_PLAYER]
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PLAYER,
                    default=(
                        current_player
                        if current_player in player_options
                        else next(iter(player_options))
                    ),
                ): vol.In(player_options)
            }
        )
        return self.async_show_form(step_id="player", data_schema=schema)

    async def async_step_move_player(self, user_input=None):
        """Confirm and request a move from the selected destination LMS."""
        if user_input is not None:
            if not user_input["confirm"]:
                return self.async_abort(reason="move_cancelled")
            if not await _async_move_player_to_server(
                self.hass,
                self._squeezebox_entry_id,
                self._player_entity,
                self._source_squeezebox_entry_id,
            ):
                return self.async_show_form(
                    step_id="move_player",
                    data_schema=_move_confirmation_schema(),
                    errors={"base": "move_failed"},
                )
            return await self.async_step_wait_for_player()

        return self.async_show_form(
            step_id="move_player", data_schema=_move_confirmation_schema()
        )

    async def async_step_wait_for_player(self, user_input=None):
        """Verify the LMS move, update the device, and wait for discovery."""
        if user_input is not None and not await _async_confirm_move_and_update_device(
            self.hass,
            self._player_entity,
            self._source_squeezebox_entry_id,
            self._squeezebox_entry_id,
        ):
            return self.async_show_form(
                step_id="wait_for_player",
                data_schema=vol.Schema({}),
                errors={"base": "move_not_verified"},
            )
        if _player_belongs_to_server(
            self.hass, self._player_entity, self._squeezebox_entry_id
        ):
            return await self.async_step_formats()
        if user_input is not None:
            return self.async_show_form(
                step_id="wait_for_player",
                data_schema=vol.Schema({}),
                errors={"base": "player_not_discovered"},
            )
        return self.async_show_form(
            step_id="wait_for_player", data_schema=vol.Schema({})
        )

    async def async_step_formats(self, user_input=None):
        """Edit the playlist summary and description templates."""
        errors = {}
        validation_error = ""
        if user_input is not None:
            for key in (CONF_PLAYLIST_SUMMARY_FORMAT, CONF_PLAYLIST_DESCRIPTION_FORMAT):
                try:
                    _validate_playlist_format(user_input[key])
                except ValueError as err:
                    errors["base"] = "invalid_format"
                    validation_error = str(err)
                    break
            if not errors:
                previous_player = self.config_entry.options.get(
                    CONF_PLAYER, self.config_entry.data[CONF_PLAYER]
                )
                previous_server = self.config_entry.options.get(
                    CONF_SQUEEZEBOX_ENTRY,
                    self.config_entry.data.get(CONF_SQUEEZEBOX_ENTRY),
                )
                if (
                    previous_player != self._player_entity
                    or previous_server != self._squeezebox_entry_id
                ):
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        title=_config_entry_title(
                            self.hass,
                            self._squeezebox_entry_id,
                            self._player_entity,
                        ),
                    )
                return self.async_create_entry(
                    title="",
                    data={
                        **user_input,
                        CONF_SQUEEZEBOX_ENTRY: self._squeezebox_entry_id,
                        CONF_PLAYER: self._player_entity,
                    },
                )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PLAYLIST_SUMMARY_FORMAT,
                    default=options.get(
                        CONF_PLAYLIST_SUMMARY_FORMAT,
                        DEFAULT_PLAYLIST_SUMMARY_FORMAT,
                    ),
                ): str,
                vol.Required(
                    CONF_PLAYLIST_DESCRIPTION_FORMAT,
                    default=options.get(
                        CONF_PLAYLIST_DESCRIPTION_FORMAT,
                        DEFAULT_PLAYLIST_DESCRIPTION_FORMAT,
                    ),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="formats",
            data_schema=schema,
            errors=errors,
            description_placeholders={"validation_error": validation_error},
        )


def _all_player_entities(hass):
    """Return registered players owned by loaded Squeezebox server entries."""
    entity_registry = er.async_get(hass)
    loaded_entries = {
        entry.entry_id
        for entry in hass.config_entries.async_entries("squeezebox")
        if getattr(entry.state, "value", entry.state) == "loaded"
    }
    return sorted(
        (
            entity
            for entity in entity_registry.entities.values()
            if entity.platform == "squeezebox"
            and entity.domain == "media_player"
            and entity.config_entry_id in loaded_entries
        ),
        key=lambda entity: entity.entity_id,
    )


def _player_belongs_to_server(hass, player_entity: str, squeezebox_entry_id: str):
    """Return whether the Squeezebox integration currently assigns the player."""
    entity = er.async_get(hass).async_get(player_entity)
    return (
        entity is not None
        and entity.platform == "squeezebox"
        and entity.domain == "media_player"
        and entity.config_entry_id == squeezebox_entry_id
    )


def _player_option_label(hass, entity, destination_entry_id: str) -> str:
    """Label the current server and indicate when a move will be offered."""
    server = hass.config_entries.async_get_entry(entity.config_entry_id)
    server_name = server.title if server else "Unknown server"
    suffix = (
        "" if entity.config_entry_id == destination_entry_id else " (Move to this LMS)"
    )
    return f"{_player_name(hass, entity.entity_id)} — {server_name}{suffix}"


def _move_confirmation_schema():
    """Return the explicit confirmation form for moving a player."""
    return vol.Schema({vol.Required("confirm", default=False): bool})


async def _async_move_player_to_server(
    hass, destination_entry_id: str, player_entity: str, source_entry_id: str
) -> bool:
    """Ask the destination LMS to pull a player from its current server."""
    destination_entry = hass.config_entries.async_get_entry(destination_entry_id)
    runtime_data = getattr(destination_entry, "runtime_data", None)
    coordinators = getattr(runtime_data, "player_coordinators", {})
    if not coordinators:
        _LOGGER.error("No player coordinator available for destination LMS")
        return False

    entity = er.async_get(hass).async_get(player_entity)
    if entity is None:
        return False

    coordinator = next(iter(coordinators.values()))
    destination_server = getattr(getattr(coordinator, "player", None), "_lms", None)
    if destination_server is None or not getattr(destination_server, "host", None):
        _LOGGER.error("Could not resolve the destination LMS connection")
        return False

    source_entry = hass.config_entries.async_get_entry(source_entry_id)
    source_runtime_data = getattr(source_entry, "runtime_data", None)
    source_coordinators = getattr(source_runtime_data, "player_coordinators", {})
    if not source_coordinators:
        _LOGGER.error("No player coordinator available for source LMS")
        return False

    source_coordinator = next(iter(source_coordinators.values()))
    source_server = getattr(getattr(source_coordinator, "player", None), "_lms", None)
    if source_server is None or not getattr(source_server, "host", None):
        _LOGGER.error("Could not resolve the source LMS connection")
        return False

    try:
        response = await destination_server.async_query(
            "disconnect", entity.unique_id, source_server.host
        )
    except Exception:
        _LOGGER.exception("Failed to ask LMS to move player %s", player_entity)
        return False
    if response is None:
        _LOGGER.error("Destination LMS did not confirm the player move command")
        return False
    return True


async def _async_confirm_move_and_update_device(
    hass, player_entity: str, source_entry_id: str, destination_entry_id: str
) -> bool:
    """Verify that LMS moved the player, then transfer its Squeezebox device."""
    entity_registry = er.async_get(hass)
    entity = entity_registry.async_get(player_entity)
    if (
        entity is None
        or entity.platform != "squeezebox"
        or entity.domain != "media_player"
        or entity.device_id is None
    ):
        _LOGGER.error(
            "Could not find the Squeezebox entity/device for %s", player_entity
        )
        return False

    player_id = entity.unique_id
    source_entry = hass.config_entries.async_get_entry(source_entry_id)
    destination_entry = hass.config_entries.async_get_entry(destination_entry_id)
    source_server = _server_from_entry(source_entry)
    destination_server = _server_from_entry(destination_entry)
    if source_server is None or destination_server is None:
        _LOGGER.error("Could not resolve both LMS connections to verify player move")
        return False

    try:
        destination_connected = await _async_player_connected(
            destination_server, player_id
        )
        source_connected = await _async_player_connected(source_server, player_id)
    except Exception:
        _LOGGER.exception("Could not verify LMS move for player %s", player_entity)
        return False

    if destination_connected is not True or source_connected is not False:
        _LOGGER.info(
            "LMS move not yet confirmed for %s (destination=%s, source=%s)",
            player_entity,
            destination_connected,
            source_connected,
        )
        return False

    device_registry = dr.async_get(hass)
    device = device_registry.async_get(entity.device_id)
    if device is None:
        _LOGGER.error("Squeezebox device registry entry missing for %s", player_entity)
        return False
    config_entry_id = getattr(device, "config_entry_id", None)
    config_entry_ids = (
        {config_entry_id} if config_entry_id is not None else set(device.config_entries)
    )
    if (
        destination_entry_id in config_entry_ids
        and source_entry_id not in config_entry_ids
    ):
        return True
    if source_entry_id not in config_entry_ids:
        _LOGGER.error(
            "Squeezebox device %s is not associated with source entry %s",
            device.id,
            source_entry_id,
        )
        return False

    # Core creates a second, empty device record for the same player as soon
    # as the destination Squeezebox server discovers it. Remove that empty
    # record first so the existing device (and its user customizations) can
    # be transferred without an identifier collision.
    destination_devices = dr.async_entries_for_config_entry(
        device_registry, destination_entry_id
    )
    for destination_device in destination_devices:
        if destination_device.id == device.id or not (
            destination_device.identifiers & device.identifiers
        ):
            continue
        destination_entity_registry_ids = er.async_entries_for_device(
            entity_registry, destination_device.id
        )
        if destination_entity_registry_ids:
            _LOGGER.error(
                "Cannot transfer Squeezebox device %s: destination device %s "
                "already has entities",
                device.id,
                destination_device.id,
            )
            return False
        device_registry.async_remove_device(destination_device.id)

    update_parameters = signature(device_registry.async_update_device).parameters
    try:
        if "new_config_entry_id" in update_parameters:
            device_registry.async_update_device(
                device.id, new_config_entry_id=destination_entry_id
            )
        else:
            # Core 2025.11 uses add/remove; newer releases use new_config_entry_id.
            device_registry.async_update_device(
                device.id,
                add_config_entry_id=destination_entry_id,
                remove_config_entry_id=source_entry_id,
            )
    except (KeyError, ValueError):
        _LOGGER.exception("Could not move Squeezebox device %s", device.id)
        return False
    return True


async def _async_player_connected(server, player_id: str) -> bool | None:
    """Read a player's connected flag from the live LMS players status query."""
    response = await server.async_query("players", "status")
    if not isinstance(response, dict) or not isinstance(
        response.get("players_loop"), list
    ):
        return None

    normalized_player_id = player_id.casefold()
    for player in response["players_loop"]:
        if (
            isinstance(player, dict)
            and isinstance(player.get("playerid"), str)
            and player["playerid"].casefold() == normalized_player_id
        ):
            connected = player.get("connected")
            if connected in (1, "1", True):
                return True
            if connected in (0, "0", False):
                return False
            return None
    return False


def _server_from_entry(entry):
    """Return the existing LMS connection from a Squeezebox config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    coordinators = getattr(runtime_data, "player_coordinators", {})
    if not coordinators:
        return None
    coordinator = next(iter(coordinators.values()))
    return getattr(getattr(coordinator, "player", None), "_lms", None)


def _squeezebox_entries_with_players(hass):
    """Return configured Squeezebox servers that currently expose players."""
    players_by_entry = {
        entity.config_entry_id
        for entity in er.async_get(hass).entities.values()
        if entity.platform == "squeezebox"
        and entity.domain == "media_player"
        and entity.config_entry_id
    }
    return [
        entry
        for entry in _loaded_squeezebox_entries(hass)
        if entry.entry_id in players_by_entry
    ]


def _loaded_squeezebox_entries(hass):
    """Return loaded Squeezebox server entries."""
    return [
        entry
        for entry in hass.config_entries.async_entries("squeezebox")
        if getattr(entry.state, "value", entry.state) == "loaded"
    ]


def _player_name(hass, player_entity: str) -> str:
    """Return the player's visible name for the LMS Plus config entry."""
    state = hass.states.get(player_entity)
    if state is not None and (name := state.attributes.get("friendly_name")):
        return name
    entity = er.async_get(hass).async_get(player_entity)
    return (entity.name if entity is not None else None) or player_entity


def _config_entry_title(hass, squeezebox_entry_id: str, player_entity: str) -> str:
    """Name the entry after its Squeezebox server and selected player."""
    server = hass.config_entries.async_get_entry(squeezebox_entry_id)
    server_name = server.title if server is not None else "Unknown server"
    return f"{server_name} — {_player_name(hass, player_entity)}"


def _validate_playlist_format(template: str) -> None:
    """Reject invalid template fields before storing the user's format."""
    try:
        fields = [field for _, field, _, _ in Formatter().parse(template) if field]
    except ValueError as err:
        raise ValueError("The format syntax is invalid.") from err

    unsupported = next(
        (field for field in fields if field not in _PLAYLIST_FORMAT_FIELDS), None
    )
    if unsupported:
        raise ValueError(f"Unsupported placeholder: {{{unsupported}}}.")
