"""Forecast sensors for each inverter and for the whole site."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import ForecastData, SolxpectSitesConfigEntry, SolxpectSitesCoordinator

TOTAL = "total"


def _day(offset: int) -> tuple[datetime, datetime]:
    start = dt_util.start_of_local_day(dt_util.now().date() + timedelta(days=offset))
    return start, dt_util.start_of_local_day(start.date() + timedelta(days=1))


@dataclass(frozen=True, kw_only=True)
class SolxpectSensorDescription(SensorEntityDescription):
    """Sensor description with the function computing its value."""

    value_fn: Callable[[ForecastData, str], float | None]
    day: int | None = None  # day offset whose intervals are shown as attribute


def _energy_kwh(data: ForecastData, column: str, start: datetime, end: datetime) -> float:
    return round(data.energy(column, start, end) / 1000, 3)


SENSORS: tuple[SolxpectSensorDescription, ...] = (
    SolxpectSensorDescription(
        key="power_now",
        translation_key="power_now",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda data, column: data.power_at(column, dt_util.utcnow()),
    ),
    SolxpectSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda data, column: _energy_kwh(data, column, *_day(0)),
        day=0,
    ),
    SolxpectSensorDescription(
        key="energy_today_remaining",
        translation_key="energy_today_remaining",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        # intervals that haven't started yet; the running one is left out
        value_fn=lambda data, column: _energy_kwh(data, column, dt_util.utcnow(), _day(0)[1]),
    ),
    SolxpectSensorDescription(
        key="energy_tomorrow",
        translation_key="energy_tomorrow",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda data, column: _energy_kwh(data, column, *_day(1)),
        day=1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolxpectSitesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of a site."""
    coordinator = entry.runtime_data
    site_device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="SolXpect",
        # HA lists this device under "devices that don't belong to a sub-entry"
        model="Total of all inverters",
        entry_type=DeviceEntryType.SERVICE,
    )
    # the inverter devices are linked to the site device by its registry id
    site_device_id = (
        dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **site_device).id
    )
    async_add_entities(
        SolxpectSensor(coordinator, description, TOTAL, f"{entry.entry_id}_{TOTAL}", site_device)
        for description in SENSORS
    )
    for subentry_id, inverter_name in coordinator.inverter_names.items():
        device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{subentry_id}")},
            name=inverter_name,
            manufacturer="SolXpect",
            model="Inverter",
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=site_device_id,
        )
        async_add_entities(
            (
                SolxpectSensor(
                    coordinator, description, inverter_name, f"{entry.entry_id}_{subentry_id}", device
                )
                for description in SENSORS
            ),
            config_subentry_id=subentry_id,
        )


class SolxpectSensor(CoordinatorEntity[SolxpectSitesCoordinator], SensorEntity):
    """A forecast value of one inverter or of the whole site."""

    entity_description: SolxpectSensorDescription
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"intervals"})

    def __init__(
        self,
        coordinator: SolxpectSitesCoordinator,
        description: SolxpectSensorDescription,
        column: str,
        unique_prefix: str,
        device: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._column = column
        self._attr_unique_id = f"{unique_prefix}_{description.key}"
        self._attr_device_info = device

    @property
    def native_value(self) -> float | None:
        """Return the forecast value."""
        return self.entity_description.value_fn(self.coordinator.data, self._column)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the energy (Wh) per interval of the day, keyed by local start time."""
        if self.entity_description.day is None:
            return None
        periods = self.coordinator.data.wh_periods(self._column, *_day(self.entity_description.day))
        return {
            "intervals": {
                dt_util.as_local(start).isoformat(): round(wh, 1) for start, wh in periods.items()
            }
        }
