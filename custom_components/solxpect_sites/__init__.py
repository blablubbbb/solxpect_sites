"""SolXpect Sites: PV forecast for sites with several inverters and panel arrays."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.event import async_track_time_interval

from .coordinator import SolxpectSitesConfigEntry, SolxpectSitesCoordinator

PLATFORMS = [Platform.SENSOR]
STATE_REFRESH = timedelta(minutes=5)


async def async_setup_entry(hass: HomeAssistant, entry: SolxpectSitesConfigEntry) -> bool:
    """Set up a site."""
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
    hass.config_entries.async_schedule_reload(entry.entry_id)
