"""Config flows, sensors and Energy dashboard forecast of SolXpect Sites."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.solxpect_sites.const import DOMAIN
from custom_components.solxpect_sites.energy import async_get_solar_forecast

from .conftest import LATITUDE, LONGITUDE, app_row, make_app_backup

SHADING = ",".join(["0"] * 36)
FILE_ID = "0123456789abcdef0123456789abcdef"


async def _create_site(hass: HomeAssistant, interval: str = "60") -> config_entries.ConfigEntry:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Home", "latitude": LATITUDE, "longitude": LONGITUDE, "interval": interval, "beam_mode": "substep"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


async def _add_inverter(hass, entry, name="Deye", limit=8000):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "inverter"), context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": name, "efficiency": 97, "power_limit": limit}
    )


async def _add_array(hass, entry, inverter_uid, name="South", power=6000, shading=SHADING):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "array"), context={"source": config_entries.SOURCE_USER}
    )
    if result["type"] is FlowResultType.ABORT:
        return result
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": name, "inverter": inverter_uid, "size_mode": "power", "azimuth": 180, "tilt": 30},
    )
    assert result["step_id"] == "details"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "cells_max_power": power, "temp_coeff": -0.3, "cooling_factor": 0.024,
            "diffuse_efficiency": 93, "albedo": 0.18,
            "shading_elevation": shading, "shading_opacity": shading,
        },
    )


def _inverter_uid(entry, name):
    return next(s.unique_id for s in entry.subentries.values() if s.title == name)


async def test_manual_site_with_inverter_and_array(hass: HomeAssistant) -> None:
    """Create a site, add an inverter and an array; the sensors follow."""
    entry = await _create_site(hass)
    assert entry.state is ConfigEntryState.LOADED

    # an array needs an inverter first
    result = await _add_array(hass, entry, "none")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_inverters"

    result = await _add_inverter(hass, entry)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    result = await _add_inverter(hass, entry)
    assert result["errors"] == {"name": "name_exists"}

    result = await _add_array(hass, entry, _inverter_uid(entry, "Deye"))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    tomorrow_site = hass.states.get("sensor.home_energy_tomorrow")
    tomorrow_inverter = hass.states.get("sensor.deye_energy_tomorrow")
    assert float(tomorrow_site.state) > 0
    assert tomorrow_site.state == tomorrow_inverter.state  # only one inverter
    assert len(tomorrow_site.attributes["intervals"]) == 24
    assert hass.states.get("sensor.deye_power_now") is not None

    # the inverter's entities and device belong to its subentry
    inverter_sub = next(s for s in entry.subentries.values() if s.subentry_type == "inverter")
    ent = er.async_get(hass).async_get("sensor.deye_energy_tomorrow")
    assert ent.config_subentry_id == inverter_sub.subentry_id
    device = dr.async_get(hass).async_get(ent.device_id)
    assert device.name == "Deye"

    forecast = await async_get_solar_forecast(hass, entry.entry_id)
    assert len(forecast["wh_hours"]) == 48
    assert sum(forecast["wh_hours"].values()) > 0


async def test_shared_limit_and_second_inverter(hass: HomeAssistant) -> None:
    """The limit applies to each inverter's own arrays; the total is the sum."""
    entry = await _create_site(hass)
    await _add_inverter(hass, entry, "Deye", limit=2000)
    await hass.async_block_till_done()
    await _add_inverter(hass, entry, "Micro", limit=800)
    await hass.async_block_till_done()
    await _add_array(hass, entry, _inverter_uid(entry, "Deye"), "South", 4000)
    await hass.async_block_till_done()
    await _add_array(hass, entry, _inverter_uid(entry, "Deye"), "East", 4000)
    await hass.async_block_till_done()
    await _add_array(hass, entry, _inverter_uid(entry, "Micro"), "Balcony", 600)
    await hass.async_block_till_done()

    data = entry.runtime_data.data
    assert data.power["Deye"].max() == 2000
    assert data.power["Micro"].max() < 800
    assert (data.power["total"] == data.power["Deye"] + data.power["Micro"]).all()
    total = float(hass.states.get("sensor.home_energy_tomorrow").state)
    parts = float(hass.states.get("sensor.deye_energy_tomorrow").state) + float(
        hass.states.get("sensor.micro_energy_tomorrow").state
    )
    assert abs(total - parts) < 0.01


async def test_quarter_hour_site(hass: HomeAssistant) -> None:
    """15-minute intervals: 96 per day, the Energy dashboard still gets hours."""
    entry = await _create_site(hass, interval="15")
    await _add_inverter(hass, entry)
    await hass.async_block_till_done()
    await _add_array(hass, entry, _inverter_uid(entry, "Deye"))
    await hass.async_block_till_done()
    assert len(hass.states.get("sensor.home_energy_tomorrow").attributes["intervals"]) == 96
    forecast = await async_get_solar_forecast(hass, entry.entry_id)
    assert len(forecast["wh_hours"]) == 48


async def test_array_validation_and_reconfigure(hass: HomeAssistant) -> None:
    """Shading needs 36 values; reconfiguring an array changes the forecast."""
    entry = await _create_site(hass)
    await _add_inverter(hass, entry)
    await hass.async_block_till_done()
    uid = _inverter_uid(entry, "Deye")
    result = await _add_array(hass, entry, uid, shading="0,0,0")
    assert result["errors"] == {"shading_elevation": "invalid_shading", "shading_opacity": "invalid_shading"}

    await _add_array(hass, entry, uid, power=3000)
    await hass.async_block_till_done()
    before = float(hass.states.get("sensor.deye_energy_tomorrow").state)

    array = next(s for s in entry.subentries.values() if s.subentry_type == "array")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "array"),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": array.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "South", "inverter": uid, "size_mode": "area", "azimuth": 180, "tilt": 30}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "cells_area": 30, "cells_efficiency": 20, "temp_coeff": -0.3, "cooling_factor": 0.024,
            "diffuse_efficiency": 93, "albedo": 0.18, "shading_elevation": SHADING, "shading_opacity": SHADING,
        },
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    array = entry.subentries[array.subentry_id]
    assert array.data["size_mode"] == "area" and array.data["cells_max_power"] == 0
    after = float(hass.states.get("sensor.deye_energy_tomorrow").state)
    assert after > before * 1.9  # 6000 W instead of 3000 W


@contextmanager
def _uploaded(path):
    @contextmanager
    def fake_process(hass, file_id):
        yield path

    with patch("custom_components.solxpect_sites.config_flow.process_uploaded_file", fake_process):
        yield


async def test_app_import(hass: HomeAssistant, tmp_path) -> None:
    """Import chosen plants of an app backup as one site with subentries."""
    zip_path = make_app_backup(tmp_path, [
        app_row(1, "Roof east", central=True, limit=4000, azimuth=90),
        app_row(2, "Roof west", central=True, limit=4000, azimuth=270),
        app_row(3, "Balcony", central=False, limit=600, power=800, area=4.0, efficiency=21.0),
        app_row(4, "Garden house", central=False, latitude=LATITUDE + 0.1),
    ])
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_app"})
    with _uploaded(zip_path):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"file": FILE_ID})
    assert result["step_id"] == "import_select"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"plants": ["1", "4"], "name": "Home", "interval": "60", "beam_mode": "app"}
    )
    assert result["errors"] == {"plants": "different_locations"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"plants": ["1", "2", "3"], "name": "Home", "interval": "60", "beam_mode": "app"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["description"] == "size_conflict"  # balcony: 800 Wp vs 4 m² x 21 % = 840 W
    assert "Balcony" in result["description_placeholders"]["conflicts"]
    await hass.async_block_till_done()

    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    subs = sorted((s.subentry_type, s.title) for s in entry.subentries.values())
    assert subs == [
        ("array", "Balcony"), ("array", "Roof east"), ("array", "Roof west"),
        ("inverter", "Balcony"), ("inverter", "Central inverter"),
    ]
    central = next(s for s in entry.subentries.values() if s.title == "Central inverter")
    assert central.data["power_limit"] == 8000
    balcony = next(s for s in entry.subentries.values() if s.subentry_type == "array" and s.title == "Balcony")
    assert balcony.data["size_mode"] == "area"
    assert float(hass.states.get("sensor.central_inverter_energy_tomorrow").state) > 0


async def test_invalid_backup(hass: HomeAssistant, tmp_path) -> None:
    """A file that isn't an app backup gives an error."""
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "import_app"})
    with _uploaded(bad):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"file": FILE_ID})
    assert result["errors"] == {"file": "invalid_backup"}


async def test_removed_inverter_leaves_array_ignored(hass: HomeAssistant) -> None:
    """Removing an inverter keeps the site running; its arrays are skipped."""
    entry = await _create_site(hass)
    await _add_inverter(hass, entry)
    await hass.async_block_till_done()
    await _add_array(hass, entry, _inverter_uid(entry, "Deye"))
    await hass.async_block_till_done()
    inverter = next(s for s in entry.subentries.values() if s.subentry_type == "inverter")
    hass.config_entries.async_remove_subentry(entry, inverter.subentry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.deye_energy_tomorrow") is None
    assert float(hass.states.get("sensor.home_energy_tomorrow").state) == 0
