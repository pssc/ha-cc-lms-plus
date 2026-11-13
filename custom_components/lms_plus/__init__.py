"""LMS Plus custom integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_PLAYER,
    CONF_SERVER_CONTROLS,
    CONF_SQUEEZEBOX_ENTRY,
    DOMAIN,
    PLATFORMS,
    SERVER_PLATFORMS,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration from YAML (config entries are used)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up entities for a player from its selected Squeezebox server."""
    if entry.data.get(CONF_SERVER_CONTROLS):
        squeezebox_entry_id = entry.data[CONF_SQUEEZEBOX_ENTRY]
        squeezebox_entry = hass.config_entries.async_get_entry(squeezebox_entry_id)
        if squeezebox_entry is None or squeezebox_entry.domain != "squeezebox":
            return False
        runtime_data = getattr(squeezebox_entry, "runtime_data", None)
        coordinators = getattr(runtime_data, "player_coordinators", {})
        if not coordinators:
            return False
        server = getattr(next(iter(coordinators.values())).player, "_lms", None)
        if server is None:
            return False
        previous_titles = {
            f"{squeezebox_entry.title} — Move players",
            f"Server controls — {squeezebox_entry.title}",
        }
        if entry.title in previous_titles:
            hass.config_entries.async_update_entry(
                entry, title=f"{squeezebox_entry.title} — LMS Plus controls"
            )
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "server_controls": True,
            "squeezebox_entry_id": squeezebox_entry_id,
            "server": server,
            # Reuse the Squeezebox integration's server identifier so these
            # server-level controls appear on its existing server device.
            "device_info": DeviceInfo(
                identifiers={("squeezebox", squeezebox_entry.unique_id)},
            ),
        }
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
        await hass.config_entries.async_forward_entry_setups(entry, SERVER_PLATFORMS)
        return True

    player_entity = entry.options.get(CONF_PLAYER, entry.data[CONF_PLAYER])
    entity_registry = er.async_get(hass)
    entity = entity_registry.async_get(player_entity)
    if entity is None or entity.platform != "squeezebox":
        return False

    # Older LMS Plus entries did not store the owning Squeezebox config entry.
    # The entity registry already records that relationship, so use it to keep
    # existing installations working while new entries save it explicitly.
    squeezebox_entry_id = entry.options.get(
        CONF_SQUEEZEBOX_ENTRY,
        entry.data.get(CONF_SQUEEZEBOX_ENTRY, entity.config_entry_id),
    )
    if entity.config_entry_id != squeezebox_entry_id:
        return False
    squeezebox_entry = hass.config_entries.async_get_entry(squeezebox_entry_id)
    if squeezebox_entry is None or squeezebox_entry.domain != "squeezebox":
        return False

    # Migrate the default player-only title used by earlier LMS Plus entries.
    # Preserve titles that a user may have customized in Home Assistant.
    player_state = hass.states.get(player_entity)
    player_name = (
        player_state.attributes.get("friendly_name")
        if player_state is not None
        else None
    )
    player_name = player_name or entity.name or player_entity
    if entry.title == player_name:
        hass.config_entries.async_update_entry(
            entry, title=f"{squeezebox_entry.title} — {player_name}"
        )

    player_id = entity.unique_id
    runtime_data = getattr(squeezebox_entry, "runtime_data", None)
    coordinators = getattr(runtime_data, "player_coordinators", {})
    player_coordinator = coordinators.get(player_id)
    if player_coordinator is None:
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "player_entity": player_entity,
        "player_id": player_id,
        "squeezebox_entry_id": squeezebox_entry_id,
        "coordinator": player_coordinator,
        "device_info": DeviceInfo(identifiers={("squeezebox", player_id)}),
    }
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a configured player sensor."""
    platforms = SERVER_PLATFORMS if entry.data.get(CONF_SERVER_CONTROLS) else PLATFORMS
    if not await hass.config_entries.async_unload_platforms(entry, platforms):
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
