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

FILE_ID = "0123456789abcdef0123456789abcdef"


async def _create_site(hass: HomeAssistant, interval: str = "60", name: str = "Home") -> config_entries.ConfigEntry:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": name, "latitude": LATITUDE, "longitude": LONGITUDE, "interval": interval, "beam_mode": "substep"},
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


async def _add_array(hass, entry, inverter_uid, name="South", power=6000, shading=None,
                     azimuth=180, tilt=30):
    """Run the three steps; shading maps field names to texts (defaults are kept otherwise)."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "array"), context={"source": config_entries.SOURCE_USER}
    )
    if result["type"] is FlowResultType.ABORT:
        return result
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": name, "inverter": inverter_uid, "size_mode": "power", "azimuth": azimuth, "tilt": tilt},
    )
    assert result["step_id"] == "details"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "cells_max_power": power, "temp_coeff": -0.3, "cooling_factor": 0.024,
            "diffuse_efficiency": 93, "albedo": 0.18,
        },
    )
    assert result["step_id"] == "shading"
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], _shading_input(result, shading or {})
    )


def _shading_input(result, changes):
    """The shading form's defaults (incl. the collapsed section) with some fields changed."""
    values = {}
    for field in result["data_schema"].schema:
        key = str(field)
        if key == "unreachable":
            inner = result["data_schema"].schema[field].schema.schema
            values[key] = {str(f): changes.get(str(f), f.default()) for f in inner}
        else:
            values[key] = changes.get(key, field.default())
    return values


def _inverter_uid(entry, name):
    return next(s.unique_id for s in entry.subentries.values() if s.title == name)


def _array(entry, name):
    return next(
        s for s in entry.subentries.values() if s.subentry_type == "array" and s.data.get("name") == name
    )


async def _change_array(hass, entry, array, part):
    """Open the change menu of an array and choose a part."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "array"),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": array.subentry_id},
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": part})


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
    site_device = dr.async_get(hass).async_get(
        er.async_get(hass).async_get("sensor.home_energy_tomorrow").device_id
    )
    assert site_device.model == "Total of all inverters"
    assert _array(entry, "South").title == "Deye › South"

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
    assert max(data.power["Deye"]) == 2000
    assert max(data.power["Micro"]) < 800
    assert all(
        t == d + m
        for t, d, m in zip(data.power["total"], data.power["Deye"], data.power["Micro"], strict=True)
    )
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


async def test_shading_step_lists_reachable_directions(hass: HomeAssistant) -> None:
    """A vertical facade: directions behind it are prefilled as blocked and collapsed."""
    entry = await _create_site(hass)
    await _add_inverter(hass, entry)
    await hass.async_block_till_done()
    uid = _inverter_uid(entry, "Deye")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "array"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Facade", "inverter": uid, "size_mode": "power", "azimuth": 171, "tilt": 90}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"cells_max_power": 6160, "temp_coeff": -0.3, "cooling_factor": 0.024, "diffuse_efficiency": 50, "albedo": 0.18},
    )
    assert result["step_id"] == "shading"
    visible = [str(f) for f in result["data_schema"].schema if str(f) != "unreachable"]
    assert visible[0] == "dir_080" and visible[-1] == "dir_260"   # sun in front: 81°-261°
    hidden = result["data_schema"].schema["unreachable"].schema.schema
    assert all(f.default() == "90" for f in hidden)
    assert "170–180°: up to 60°" in result["description_placeholders"]["sun"]

    bad = _shading_input(result, {"dir_150": "35/120"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"dir_150": "invalid_direction"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _shading_input(result, {"dir_150": "35", "dir_180": "45/60", "dir_200": ""})
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = _array(entry, "Facade").data
    assert (data["shading_elevation"][15], data["shading_opacity"][15]) == (35, 100)
    assert (data["shading_elevation"][18], data["shading_opacity"][18]) == (45, 60)
    assert (data["shading_elevation"][20], data["shading_opacity"][20]) == (0, 0)
    assert (data["shading_elevation"][0], data["shading_opacity"][0]) == (90, 100)   # behind
    await hass.async_block_till_done()

    # changing only the shading shows the stored values again
    result = await _change_array(hass, entry, _array(entry, "Facade"), "shading")
    assert result["step_id"] == "shading"
    defaults = {str(f): f.default() for f in result["data_schema"].schema if str(f) != "unreachable"}
    assert (defaults["dir_150"], defaults["dir_180"], defaults["dir_200"]) == ("35", "45/60", "0")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _shading_input(result, {"dir_200": "10"})
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    data = _array(entry, "Facade").data
    assert data["shading_elevation"][20] == 10 and data["shading_elevation"][15] == 35
    assert data["cells_max_power"] == 6160


async def test_array_reconfigure(hass: HomeAssistant) -> None:
    """Reconfiguring an array changes the forecast."""
    entry = await _create_site(hass)
    await _add_inverter(hass, entry)
    await hass.async_block_till_done()
    uid = _inverter_uid(entry, "Deye")
    await _add_array(hass, entry, uid, power=3000)
    await hass.async_block_till_done()
    before = float(hass.states.get("sensor.deye_energy_tomorrow").state)

    array = _array(entry, "South")
    result = await _change_array(hass, entry, array, "panels")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"size_mode": "area", "azimuth": 180, "tilt": 30}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "cells_area": 30, "cells_efficiency": 20, "temp_coeff": -0.3, "cooling_factor": 0.024,
            "diffuse_efficiency": 93, "albedo": 0.18,
        },
    )
    # same orientation: no shading step
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    array = entry.subentries[array.subentry_id]
    assert array.data["size_mode"] == "area" and array.data["cells_max_power"] == 0
    after = float(hass.states.get("sensor.deye_energy_tomorrow").state)
    assert after > before * 1.9  # 6000 W instead of 3000 W

    # a new orientation asks for the shading again
    result = await _change_array(hass, entry, array, "panels")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"size_mode": "area", "azimuth": 90, "tilt": 30}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {
        "cells_area": 30, "cells_efficiency": 20, "temp_coeff": -0.3, "cooling_factor": 0.024,
        "diffuse_efficiency": 93, "albedo": 0.18,
    })
    assert result["step_id"] == "shading"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], _shading_input(result, {}))
    assert result["type"] is FlowResultType.ABORT
    assert entry.subentries[array.subentry_id].data["azimuth"] == 90


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
        ("array", "Balcony › Balcony"), ("array", "Central inverter › Roof east"),
        ("array", "Central inverter › Roof west"),
        ("inverter", "Balcony"), ("inverter", "Central inverter"),
    ]
    central = next(s for s in entry.subentries.values() if s.title == "Central inverter")
    assert central.data["power_limit"] == 8000
    assert _array(entry, "Balcony").data["size_mode"] == "area"
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
    assert _array(entry, "South").title == "(no inverter) › South"


async def test_array_titles_follow_names(hass: HomeAssistant) -> None:
    """Titles show the inverter; renaming the inverter or the array (HA's "Rename") updates them."""
    entry = await _create_site(hass)
    await _add_inverter(hass, entry)
    await hass.async_block_till_done()
    await _add_array(hass, entry, _inverter_uid(entry, "Deye"))
    await hass.async_block_till_done()

    inverter = next(s for s in entry.subentries.values() if s.subentry_type == "inverter")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "inverter"),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": inverter.subentry_id},
    )
    assert "site" not in {str(f) for f in result["data_schema"].schema}  # only one site
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Deye2", "efficiency": 97, "power_limit": 8000}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert _array(entry, "South").title == "Deye2 › South"

    hass.config_entries.async_update_subentry(entry, _array(entry, "South"), title="Roof")
    await hass.async_block_till_done()
    array = _array(entry, "Roof")
    assert array.title == "Deye2 › Roof"
    assert array.data["name"] == "Roof"

    # arrays created before the titles had the inverter prefix
    hass.config_entries.async_update_subentry(
        entry, array, title="Old", data={k: v for k, v in array.data.items() if k != "name"}
    )
    await hass.async_block_till_done()
    assert _array(entry, "Old").title == "Deye2 › Old"
    assert entry.state is ConfigEntryState.LOADED


async def test_move_array(hass: HomeAssistant) -> None:
    """An array moves to another inverter of its site, or to an inverter of another site."""
    home = await _create_site(hass)
    await _add_inverter(hass, home, "Deye")
    await hass.async_block_till_done()
    await _add_inverter(hass, home, "Micro", limit=800)
    await hass.async_block_till_done()
    await _add_array(hass, home, _inverter_uid(home, "Deye"), "South", 600)
    await hass.async_block_till_done()
    await _add_array(hass, home, _inverter_uid(home, "Micro"), "South", 600)
    await hass.async_block_till_done()
    cabin = await _create_site(hass, name="Cabin")
    await _add_inverter(hass, cabin, "Hut")
    await hass.async_block_till_done()

    deye_south = next(s for s in home.subentries.values() if s.title == "Deye › South")
    result = await _change_array(hass, home, deye_south, "name_inverter")
    labels = [o["label"] for o in result["data_schema"].schema["inverter"].config["options"]]
    assert labels == ["Home › Deye", "Home › Micro", "Cabin › Hut"]

    # Micro already has an array "South"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "South", "inverter": _inverter_uid(home, "Micro")}
    )
    assert result["errors"] == {"name": "name_exists"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "South 2", "inverter": _inverter_uid(home, "Micro")}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert home.subentries[deye_south.subentry_id].title == "Micro › South 2"
    assert float(hass.states.get("sensor.deye_energy_tomorrow").state) == 0

    result = await _change_array(hass, home, home.subentries[deye_south.subentry_id], "name_inverter")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "South 2", "inverter": _inverter_uid(cabin, "Hut")}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "moved"
    assert result["description_placeholders"] == {"site": "Cabin"}
    await hass.async_block_till_done()
    assert deye_south.subentry_id not in home.subentries
    moved = _array(cabin, "South 2")
    assert moved.title == "Hut › South 2"
    assert moved.data["shading_elevation"] == deye_south.data["shading_elevation"]
    assert home.state is ConfigEntryState.LOADED and cabin.state is ConfigEntryState.LOADED
    assert float(hass.states.get("sensor.hut_energy_tomorrow").state) > 0


async def test_move_inverter_to_other_site(hass: HomeAssistant, mock_weather) -> None:
    """An inverter moves with its arrays; each site reloads only once."""
    home = await _create_site(hass)
    await _add_inverter(hass, home, "Deye")
    await hass.async_block_till_done()
    await _add_array(hass, home, _inverter_uid(home, "Deye"), "South")
    await hass.async_block_till_done()
    await _add_array(hass, home, _inverter_uid(home, "Deye"), "East", azimuth=90)
    await hass.async_block_till_done()
    cabin = await _create_site(hass, name="Cabin")
    await _add_inverter(hass, cabin, "Hut")
    await hass.async_block_till_done()
    deye_before = hass.states.get("sensor.deye_energy_tomorrow").state

    inverter = next(s for s in home.subentries.values() if s.title == "Deye")
    result = await hass.config_entries.subentries.async_init(
        (home.entry_id, "inverter"),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": inverter.subentry_id},
    )
    # the name must be free in the target site
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Hut", "efficiency": 97, "power_limit": 8000, "site": cabin.entry_id}
    )
    assert result["errors"] == {"name": "name_exists"}
    fetches = mock_weather.call_count
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Deye", "efficiency": 97, "power_limit": 8000, "site": cabin.entry_id}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "moved"
    assert result["description_placeholders"] == {"site": "Cabin", "arrays": "2"}
    await hass.async_block_till_done()
    assert mock_weather.call_count - fetches == 2  # one reload per site

    assert [s.title for s in home.subentries.values()] == []
    assert sorted(s.title for s in cabin.subentries.values()) == [
        "Deye", "Deye › East", "Deye › South", "Hut",
    ]
    # the sensors are recreated in the other site under the same entity IDs
    ent = er.async_get(hass).async_get("sensor.deye_energy_tomorrow")
    assert ent.config_entry_id == cabin.entry_id
    assert hass.states.get("sensor.deye_energy_tomorrow").state == deye_before
    assert float(hass.states.get("sensor.home_energy_tomorrow").state) == 0
