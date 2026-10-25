"""Solar forecast for the Energy dashboard."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

TOTAL = "total"


async def async_get_solar_forecast(
    hass: HomeAssistant, config_entry_id: str
) -> dict[str, dict[str, float | int]] | None:
    """Return the site's forecast in Wh per hour, keyed by the start of the hour."""
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return None
    data = coordinator.data

    hourly: dict[str, float] = {}
    if not data.starts:
        return {"wh_hours": {}}
    start = data.starts[0]
    end = data.starts[-1] + data.interval
    for period_start, wh in data.wh_periods(TOTAL, start, end).items():
        hour = period_start.replace(minute=0, second=0, microsecond=0)
        key = dt_util.as_local(hour).isoformat()
        hourly[key] = hourly.get(key, 0.0) + wh
    return {"wh_hours": {key: round(wh, 1) for key, wh in hourly.items()}}
