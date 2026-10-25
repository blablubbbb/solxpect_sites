"""Coordinator fetching the weather and calculating the site's forecast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

import pandas as pd

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_INTERVAL, DOMAIN, UPDATE_INTERVAL
from .model import site_from_entry

_LOGGER = logging.getLogger(__name__)

type SolxpectSitesConfigEntry = ConfigEntry[SolxpectSitesCoordinator]


@dataclass
class ForecastData:
    """Forecast of one site.

    power: one row per interval, indexed by the start of the interval (UTC),
    mean power in W. Columns: "<inverter>/<array>" (DC), "<inverter>" (AC), "total" (AC).
    """

    power: pd.DataFrame
    interval: timedelta

    def wh_periods(self, column: str, start: datetime, end: datetime) -> dict[datetime, float]:
        """Energy (Wh) per interval starting in [start, end)."""
        series = self.power[column]
        series = series[(series.index >= start) & (series.index < end)]
        hours = self.interval / timedelta(hours=1)
        return {ts.to_pydatetime(): float(w) * hours for ts, w in series.items()}

    def energy(self, column: str, start: datetime, end: datetime) -> float:
        """Energy (Wh) of the intervals starting in [start, end)."""
        return sum(self.wh_periods(column, start, end).values())

    def power_at(self, column: str, moment: datetime) -> float | None:
        """Mean power (W) of the interval containing moment."""
        series = self.power[column]
        series = series[(series.index <= moment) & (series.index + self.interval > moment)]
        return float(series.iloc[0]) if len(series) else None


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
        interval_seconds = int(self.interval.total_seconds())
        try:
            power = await self.hass.async_add_executor_job(
                self.site.forecast,
                dt_util.as_utc(start),
                dt_util.as_utc(end),
                interval_seconds,
            )
        except Exception as err:  # requests, pandas and pvlib raise many kinds of errors
            raise UpdateFailed(f"Forecast for {self.config_entry.title} failed: {err}") from err
        # the library labels intervals by their end; HA and the sensors use the start
        power.index = power.index - self.interval
        return ForecastData(power=power, interval=self.interval)
