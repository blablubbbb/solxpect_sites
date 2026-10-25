"""Coordinator fetching the weather and calculating the site's forecast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_INTERVAL, DOMAIN, UPDATE_INTERVAL
from .model import site_from_entry
from .solxpect.Site import Site

_LOGGER = logging.getLogger(__name__)

type SolxpectSitesConfigEntry = ConfigEntry[SolxpectSitesCoordinator]


@dataclass
class ForecastData:
    """Forecast of one site, as plain Python values (no pandas in the event loop).

    starts: start of each interval (UTC). power: mean power in W per interval for
    each column: "<inverter>/<array>" (DC), "<inverter>" (AC), "total" (AC).
    """

    starts: list[datetime]
    power: dict[str, list[float]]
    interval: timedelta

    def wh_periods(self, column: str, start: datetime, end: datetime) -> dict[datetime, float]:
        """Energy (Wh) per interval starting in [start, end)."""
        hours = self.interval / timedelta(hours=1)
        return {
            s: w * hours
            for s, w in zip(self.starts, self.power[column], strict=True)
            if start <= s < end
        }

    def energy(self, column: str, start: datetime, end: datetime) -> float:
        """Energy (Wh) of the intervals starting in [start, end)."""
        return sum(self.wh_periods(column, start, end).values())

    def power_at(self, column: str, moment: datetime) -> float | None:
        """Mean power (W) of the interval containing moment."""
        for s, w in zip(self.starts, self.power[column], strict=True):
            if s <= moment < s + self.interval:
                return w
        return None


def _calculate(site: Site, start: datetime, end: datetime, interval: timedelta) -> ForecastData:
    """Fetch the weather and calculate the site (runs in the executor)."""
    power = site.forecast(start, end, int(interval.total_seconds()))
    # the library labels intervals by their end; HA and the sensors use the start
    starts = [ts.to_pydatetime() - interval for ts in power.index]
    return ForecastData(
        starts=starts,
        power={column: [float(w) for w in power[column]] for column in power.columns},
        interval=interval,
    )


class SolxpectSitesCoordinator(DataUpdateCoordinator[ForecastData]):
    """Fetches the weather once per update and calculates all arrays of the site."""

    config_entry: SolxpectSitesConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SolxpectSitesConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.site, self.inverter_names = site_from_entry(entry)
        self.interval = timedelta(minutes=int(entry.data[CONF_INTERVAL]))

    async def _async_update_data(self) -> ForecastData:
        """Fetch the weather from today 00:00 to the day after tomorrow 00:00 (local time)."""
        start = dt_util.start_of_local_day()
        end = dt_util.start_of_local_day(start.date() + timedelta(days=2))
        try:
            return await self.hass.async_add_executor_job(
                _calculate, self.site, dt_util.as_utc(start), dt_util.as_utc(end), self.interval
            )
        except Exception as err:  # requests, pandas and pvlib raise many kinds of errors
            raise UpdateFailed(f"Forecast for {self.config_entry.title} failed: {err}") from err
