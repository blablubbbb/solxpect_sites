"""Conversion between the stored configuration and the solxpect library objects."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME

from .const import (
    CONF_ALBEDO,
    CONF_AZIMUTH,
    CONF_BEAM_MODE,
    CONF_CELLS_AREA,
    CONF_CELLS_EFFICIENCY,
    CONF_CELLS_MAX_POWER,
    CONF_COOLING_FACTOR,
    CONF_DIFFUSE_EFFICIENCY,
    CONF_EFFICIENCY,
    CONF_INVERTER,
    CONF_POWER_LIMIT,
    CONF_SHADING_ELEVATION,
    CONF_SHADING_OPACITY,
    CONF_SIZE_MODE,
    CONF_TEMP_COEFF,
    CONF_TILT,
    SUBENTRY_ARRAY,
    SUBENTRY_INVERTER,
)
from .solxpect.Inverter import Inverter
from .solxpect.PanelArray import PanelArray
from .solxpect.Site import Site

_LOGGER = logging.getLogger(__name__)


def array_from_data(name: str, data: dict[str, Any]) -> PanelArray:
    """Build a PanelArray from array subentry data."""
    uses_area = data.get(CONF_SIZE_MODE) == "area"
    return PanelArray(
        name=name,
        cellsMaxPower=0 if uses_area else data[CONF_CELLS_MAX_POWER],
        cellsArea=data[CONF_CELLS_AREA] if uses_area else 0,
        cellsEfficiency=data[CONF_CELLS_EFFICIENCY] if uses_area else 0,
        cellsTempCoeff=data[CONF_TEMP_COEFF],
        cellsCoolingFactor=data[CONF_COOLING_FACTOR],
        diffuseEfficiency=data[CONF_DIFFUSE_EFFICIENCY],
        albedo=data[CONF_ALBEDO],
        azimuthAngle=data[CONF_AZIMUTH],
        tiltAngle=data[CONF_TILT],
        shadingElevation=data[CONF_SHADING_ELEVATION],
        shadingOpacity=data[CONF_SHADING_OPACITY],
    )


def data_from_array(array: PanelArray, inverter_id: str) -> dict[str, Any]:
    """Array subentry data from a PanelArray (used by the app import)."""
    s = array.settings
    uses_area = s["cellsArea"] > 0 and s["cellsEfficiency"] > 0
    return {
        CONF_INVERTER: inverter_id,
        CONF_SIZE_MODE: "area" if uses_area else "power",
        CONF_CELLS_MAX_POWER: s["cellsMaxPower"],
        CONF_CELLS_AREA: s["cellsArea"],
        CONF_CELLS_EFFICIENCY: s["cellsEfficiency"],
        CONF_TEMP_COEFF: s["cellsTempCoeff"],
        CONF_COOLING_FACTOR: s["cellsCoolingFactor"],
        CONF_DIFFUSE_EFFICIENCY: s["diffuseEfficiency"],
        CONF_ALBEDO: s["albedo"],
        CONF_AZIMUTH: s["azimuthAngle"],
        CONF_TILT: s["tiltAngle"],
        CONF_SHADING_ELEVATION: list(s["shadingElevation"]),
        CONF_SHADING_OPACITY: list(s["shadingOpacity"]),
    }


def site_from_entry(entry: ConfigEntry) -> tuple[Site, dict[str, str]]:
    """Build the Site of a config entry.

    Returns the site and a map from inverter subentry_id to the inverter name
    used in the result columns.
    """
    inverters: dict[str, Inverter] = {}  # by inverter unique_id
    names: dict[str, str] = {}  # subentry_id -> inverter name
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_INVERTER:
            continue
        inverters[subentry.unique_id] = Inverter(
            subentry.data[CONF_EFFICIENCY],
            subentry.data[CONF_POWER_LIMIT],
            name=subentry.title,
        )
        names[subentry.subentry_id] = subentry.title

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ARRAY:
            continue
        inverter = inverters.get(subentry.data[CONF_INVERTER])
        if inverter is None:
            _LOGGER.warning(
                "Array %s of site %s has no inverter (was it removed?); it is ignored",
                subentry.title,
                entry.title,
            )
            continue
        inverter.arrays.append(array_from_data(subentry.title, dict(subentry.data)))

    site = Site(
        entry.data[CONF_LATITUDE],
        entry.data[CONF_LONGITUDE],
        list(inverters.values()),
        name=entry.data[CONF_NAME],
        beamMode=entry.data[CONF_BEAM_MODE],
    )
    return site, names
