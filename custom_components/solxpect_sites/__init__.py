"""SolXpect Sites: PV forecast for sites with several inverters and panel arrays."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import SolxpectSitesConfigEntry, SolxpectSitesCoordinator
from .model import sync_array_titles

PLATFORMS = [Platform.SENSOR]
STATE_REFRESH = timedelta(minutes=5)
_PENDING_RELOADS: HassKey[set[str]] = HassKey(f"{DOMAIN}_pending_reloads")


async def async_setup_entry(hass: HomeAssistant, entry: SolxpectSitesConfigEntry) -> bool:
    """Set up a site."""
    # before the update listener exists, so this doesn't trigger another reload
    sync_array_titles(hass, entry)
    try:
        coordinator = SolxpectSitesCoordinator(hass, entry)
    except ValueError as err:  # e.g. inverter names that clash
        raise ConfigEntryError(str(err)) from err
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # adding, changing or removing inverters and arrays (subentries) also calls this
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    # "power now" and "remaining today" depend on the time, not only on new data
    @callback
    def _refresh_states(_now: datetime) -> None:
        coordinator.async_update_listeners()

    entry.async_on_unload(async_track_time_interval(hass, _refresh_states, STATE_REFRESH))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolxpectSitesConfigEntry) -> bool:
    """Unload a site."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SolxpectSitesConfigEntry) -> None:
    """Reload the site after its settings, inverters or arrays changed."""
    # Moving an inverter changes several subentries in one go, and HA starts this
    # listener right at the first change. Wait until the others are done, then
    # reload once.
    pending = hass.data.setdefault(_PENDING_RELOADS, set())
    if entry.entry_id in pending:
        return
    pending.add(entry.entry_id)
    await asyncio.sleep(0)
    pending.discard(entry.entry_id)
    hass.config_entries.async_schedule_reload(entry.entry_id)
