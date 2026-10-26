"""Config flow: sites, their inverters and panel arrays, and the solXpect app import."""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    BEAM_MODES,
    CONF_ALBEDO,
    CONF_AZIMUTH,
    CONF_BEAM_MODE,
    CONF_CELLS_AREA,
    CONF_CELLS_EFFICIENCY,
    CONF_CELLS_MAX_POWER,
    CONF_COOLING_FACTOR,
    CONF_DIFFUSE_EFFICIENCY,
    CONF_EFFICIENCY,
    CONF_INTERVAL,
    CONF_INVERTER,
    CONF_POWER_LIMIT,
    CONF_SHADING_ELEVATION,
    CONF_SHADING_OPACITY,
    CONF_SITE,
    CONF_SIZE_MODE,
    CONF_TEMP_COEFF,
    CONF_TILT,
    DEFAULT_ALBEDO,
    DEFAULT_COOLING_FACTOR,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_SITE_NAME,
    DEFAULT_TEMP_COEFF,
    DOMAIN,
    INTERVALS,
    SHADING_BINS,
    SIZE_MODES,
    SUBENTRY_ARRAY,
    SUBENTRY_INVERTER,
)
from .model import SEPARATOR, array_name, array_title, data_from_array, inverter_titles
from .solxpect.PanelArray import maxSunElevationPerBin
from .solxpect.SolarPowerPlant import SolarPowerPlant
from .solxpect.app_import import read_app_plants, sites_from_app_plants

_LOGGER = logging.getLogger(__name__)

CONF_FILE = "file"
CONF_PLANTS = "plants"


def _number(minimum: float, maximum: float, step: float | str = "any", unit: str | None = None):
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _select(options: list[str], translation_key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=translation_key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _site_schema(defaults: dict[str, Any], with_location: bool = True) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
    }
    if with_location:
        fields[vol.Required(CONF_LATITUDE, default=defaults[CONF_LATITUDE])] = _number(-90, 90)
        fields[vol.Required(CONF_LONGITUDE, default=defaults[CONF_LONGITUDE])] = _number(-180, 180)
    fields[vol.Required(CONF_INTERVAL, default=defaults[CONF_INTERVAL])] = _select(INTERVALS, "interval")
    fields[vol.Required(CONF_BEAM_MODE, default=defaults[CONF_BEAM_MODE])] = _select(BEAM_MODES, "beam_mode")
    return vol.Schema(fields)


def _read_plants(hass: HomeAssistant, file_id: str) -> list[dict[str, Any]]:
    """Read the plants of an uploaded app backup (runs in the executor)."""
    with process_uploaded_file(hass, file_id) as path:
        return read_app_plants(path)


def _sites(hass: HomeAssistant) -> dict[str, ConfigEntry]:
    """All sites by entry_id."""
    return {e.entry_id: e for e in hass.config_entries.async_entries(DOMAIN, include_ignore=False)}


@callback
def _move_subentries(
    hass: HomeAssistant,
    source: ConfigEntry,
    target: ConfigEntry,
    moves: list[tuple[ConfigSubentry, str, dict[str, Any]]],
) -> None:
    """Move subentries to another site with new titles and data; both sites reload."""
    for subentry, title, data in moves:
        hass.config_entries.async_add_subentry(
            target,
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=subentry.subentry_type,
                title=title,
                unique_id=subentry.unique_id,
            ),
        )
    # removing a subentry also removes its devices and entities from the source site
    for subentry, _, _ in moves:
        hass.config_entries.async_remove_subentry(source, subentry.subentry_id)


def _plant_label(plant: dict[str, Any]) -> str:
    return (
        f"{str(plant['city_name']).strip()}: azimuth {float(plant['azimuth_angle']):.0f}°, "
        f"tilt {float(plant['tilt_angle']):.0f}°"
    )


class SolxpectSitesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a site manually or from a solXpect app backup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._plants: list[dict[str, Any]] = []

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Inverters and panel arrays are subentries of a site."""
        return {SUBENTRY_INVERTER: InverterSubentryFlow, SUBENTRY_ARRAY: ArraySubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose between manual setup and app import."""
        return self.async_show_menu(step_id="user", menu_options=["manual", "import_app"])

    def _site_defaults(self) -> dict[str, Any]:
        return {
            CONF_NAME: DEFAULT_SITE_NAME,
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_INTERVAL: "60",
            CONF_BEAM_MODE: "substep",
        }

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create an empty site; inverters and arrays are added afterwards."""
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="manual", data_schema=_site_schema(self._site_defaults()))

    async def async_step_import_app(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Upload a solXpect backup ZIP."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._plants = await self.hass.async_add_executor_job(
                    _read_plants, self.hass, user_input[CONF_FILE]
                )
            except Exception as err:  # zipfile, sqlite3 and missing tables raise various errors
                _LOGGER.warning("Could not read the solXpect backup: %s", err)
                errors[CONF_FILE] = "invalid_backup"
            else:
                return await self.async_step_import_select()
        return self.async_show_form(
            step_id="import_app",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FILE): selector.FileSelector(
                        selector.FileSelectorConfig(accept=".zip")
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_import_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the plants that form the site."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ranks = [int(rank) for rank in user_input[CONF_PLANTS]]
            sites = sites_from_app_plants(self._plants, beamMode=user_input[CONF_BEAM_MODE], select=ranks)
            if len(sites) > 1:
                errors[CONF_PLANTS] = "different_locations"
            else:
                return self._create_imported_site(sites[0], user_input)

        defaults = self._site_defaults()
        defaults[CONF_NAME] = str(self._plants[0]["city_name"]).strip()
        defaults[CONF_BEAM_MODE] = "app"  # same calculation as the app, for comparison
        schema = vol.Schema(
            {
                vol.Required(CONF_PLANTS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=str(p["rank"]), label=_plant_label(p))
                            for p in self._plants
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                **_site_schema(defaults, with_location=False).schema,
            }
        )
        return self.async_show_form(step_id="import_select", data_schema=schema, errors=errors)

    def _create_imported_site(self, site, user_input: dict[str, Any]) -> ConfigFlowResult:
        subentries: list[ConfigSubentryData] = []
        conflicts: list[str] = []
        for inverter in site.inverters:
            inverter_id = uuid4().hex
            subentries.append(
                ConfigSubentryData(
                    subentry_type=SUBENTRY_INVERTER,
                    title=inverter.name,
                    unique_id=inverter_id,
                    data={
                        CONF_EFFICIENCY: inverter.settings["inverterEfficiency"],
                        CONF_POWER_LIMIT: inverter.settings["inverterPowerLimit"],
                    },
                )
            )
            for array in inverter.arrays:
                s = array.settings
                if s["cellsArea"] > 0 and s["cellsEfficiency"] > 0 and s["cellsMaxPower"] > 0:
                    area_power = s["cellsArea"] * s["cellsEfficiency"] * 10
                    if abs(area_power - s["cellsMaxPower"]) > 0.01 * s["cellsMaxPower"]:
                        conflicts.append(
                            f"{array.name}: {s['cellsMaxPower']:.0f} Wp ignored, "
                            f"area × efficiency = {area_power:.0f} W used"
                        )
                subentries.append(
                    ConfigSubentryData(
                        subentry_type=SUBENTRY_ARRAY,
                        title=array_title(inverter.name, array.name),
                        unique_id=None,
                        data=data_from_array(array, inverter_id),
                    )
                )
        data = {
            CONF_NAME: user_input[CONF_NAME],
            CONF_LATITUDE: site.latitude,
            CONF_LONGITUDE: site.longitude,
            CONF_INTERVAL: user_input[CONF_INTERVAL],
            CONF_BEAM_MODE: user_input[CONF_BEAM_MODE],
        }
        if conflicts:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=data,
                subentries=subentries,
                description="size_conflict",
                description_placeholders={"conflicts": "\n".join(f"- {c}" for c in conflicts)},
            )
        return self.async_create_entry(title=user_input[CONF_NAME], data=data, subentries=subentries)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change name, location, interval and calculation mode of a site."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            # the update listener reloads the site
            return self.async_update_and_abort(entry, title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(
            step_id="reconfigure", data_schema=_site_schema(dict(entry.data))
        )


class InverterSubentryFlow(ConfigSubentryFlow):
    """Add or change an inverter."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add an inverter."""
        return await self._async_step_inverter(user_input, None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change an inverter."""
        return await self._async_step_inverter(user_input, self._get_reconfigure_subentry())

    async def _async_step_inverter(self, user_input, subentry) -> SubentryFlowResult:
        entry = self._get_entry()
        sites = _sites(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.pop(CONF_NAME).strip()
            target = sites.get(user_input.pop(CONF_SITE, entry.entry_id), entry)
            taken = {
                s.title
                for s in target.subentries.values()
                if s.subentry_type == SUBENTRY_INVERTER and s is not subentry
            }
            if name in taken or name == "total":
                errors[CONF_NAME] = "name_exists"
            elif subentry is None:
                return self.async_create_entry(title=name, data=user_input, unique_id=uuid4().hex)
            elif target is entry:
                # the reload puts the new name into the titles of its arrays
                return self.async_update_and_abort(entry, subentry, title=name, data=user_input)
            else:
                return self._async_move(entry, target, subentry, name, user_input)

        defaults = {CONF_NAME: "", CONF_EFFICIENCY: DEFAULT_INVERTER_EFFICIENCY, CONF_POWER_LIMIT: None}
        if subentry is not None:
            defaults = {CONF_NAME: subentry.title, **subentry.data}
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
            vol.Required(CONF_EFFICIENCY, default=defaults[CONF_EFFICIENCY]): _number(50, 100, unit="%"),
            vol.Required(
                CONF_POWER_LIMIT,
                description={"suggested_value": defaults[CONF_POWER_LIMIT]},
            ): _number(1, 1_000_000, unit="W"),
        }
        if subentry is not None and len(sites) > 1:
            fields[vol.Required(CONF_SITE, default=entry.entry_id)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=site_id, label=site.title)
                        for site_id, site in sites.items()
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        return self.async_show_form(
            step_id="reconfigure" if subentry else "user", data_schema=vol.Schema(fields), errors=errors
        )

    def _async_move(self, entry, target, subentry, name, data) -> SubentryFlowResult:
        """Move the inverter and its arrays to another site."""
        arrays = [
            s
            for s in entry.subentries.values()
            if s.subentry_type == SUBENTRY_ARRAY and s.data[CONF_INVERTER] == subentry.unique_id
        ]
        moves = [(subentry, name, data)]
        moves += [(a, array_title(name, array_name(a)), {**a.data, CONF_NAME: array_name(a)}) for a in arrays]
        _move_subentries(self.hass, entry, target, moves)
        return self.async_abort(
            reason="moved", description_placeholders={"site": target.title, "arrays": str(len(arrays))}
        )


SECTION_UNREACHABLE = "unreachable"
BLOCKED = (90.0, 100.0)  # prefilled for directions the sun can't reach in front of the panel


def _direction_key(index: int) -> str:
    """Field name of a shading direction, e.g. "dir_170" for 170–180°."""
    return f"dir_{index * 10:03d}"


def _parse_direction(text: str) -> tuple[float, float]:
    """Parse "elevation/opacity"; a single number means an opaque obstacle, empty means none."""
    parts = [p for p in str(text).replace(";", "/").replace(" ", "/").split("/") if p]
    if not parts:
        return 0.0, 0.0
    if len(parts) > 2:
        raise ValueError
    elevation = float(parts[0])
    opacity = float(parts[1]) if len(parts) == 2 else (100.0 if elevation > 0 else 0.0)
    if not 0 <= elevation <= 90 or not 0 <= opacity <= 100:
        raise ValueError
    return elevation, opacity


def _direction_text(elevation: float, opacity: float) -> str:
    if elevation == 0:
        return "0"
    return f"{elevation:g}" if opacity == 100 else f"{elevation:g}/{opacity:g}"


def _inverter_selector(labels: dict[str, str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=uid, label=label) for uid, label in labels.items()],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _orientation_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_SIZE_MODE, default=defaults[CONF_SIZE_MODE]): _select(SIZE_MODES, "size_mode"),
        vol.Required(CONF_AZIMUTH, default=defaults[CONF_AZIMUTH]): _number(0, 360, unit="°"),
        vol.Required(CONF_TILT, default=defaults[CONF_TILT]): _number(0, 90, unit="°"),
    }


def _name_taken(site: ConfigEntry, inverter_uid: str, name: str, subentry: ConfigSubentry | None) -> bool:
    """Whether another array of the inverter already has this name."""
    return any(
        s.subentry_type == SUBENTRY_ARRAY
        and s.data.get(CONF_INVERTER) == inverter_uid
        and (subentry is None or s.subentry_id != subentry.subentry_id)
        and array_name(s) == name
        for s in site.subentries.values()
    )


class ArraySubentryFlow(ConfigSubentryFlow):
    """Add a panel array in three steps (basics, details, shading), or change one part of it."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._name = ""
        self._needs_shading = True  # when adding, and after changing the orientation

    def _subentry(self):
        return self._get_reconfigure_subentry() if self.source == SOURCE_RECONFIGURE else None

    def _async_save(self) -> SubentryFlowResult:
        entry = self._get_entry()
        data = {**self._data, CONF_NAME: self._name}
        title = array_title(inverter_titles(entry).get(data[CONF_INVERTER]), self._name)
        subentry = self._subentry()
        if subentry is None:
            return self.async_create_entry(title=title, data=data)
        return self.async_update_and_abort(entry, subentry, title=title, data=data)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a panel array: name, inverter and orientation."""
        entry = self._get_entry()
        inverters = inverter_titles(entry)
        if not inverters:
            return self.async_abort(reason="no_inverters")

        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.pop(CONF_NAME).strip()
            if _name_taken(entry, user_input[CONF_INVERTER], name, None):
                errors[CONF_NAME] = "name_exists"
            else:
                self._name = name
                self._data.update(user_input)
                return await self.async_step_details()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=""): str,
                vol.Required(CONF_INVERTER, default=next(iter(inverters))): _inverter_selector(inverters),
                **_orientation_fields({CONF_SIZE_MODE: "power", CONF_AZIMUTH: 180, CONF_TILT: 30}),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a panel array: choose which part."""
        subentry = self._get_reconfigure_subentry()
        self._data = dict(subentry.data)
        self._name = array_name(subentry)
        self._needs_shading = False
        return self.async_show_menu(
            step_id="reconfigure", menu_options=["name_inverter", "panels", "shading"]
        )

    async def async_step_name_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename the array or connect it to another inverter, also one of another site."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        sites = _sites(self.hass)
        inverters = {  # unique_id -> (site, inverter title)
            uid: (site, title) for site in sites.values() for uid, title in inverter_titles(site).items()
        }
        if not inverters:
            return self.async_abort(reason="no_inverters")

        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            uid = user_input[CONF_INVERTER]
            target, inverter_title = inverters[uid]
            if _name_taken(target, uid, name, subentry):
                errors[CONF_NAME] = "name_exists"
            else:
                self._name = name
                self._data[CONF_INVERTER] = uid
                if target is entry:
                    return self._async_save()
                data = {**self._data, CONF_NAME: name}
                _move_subentries(
                    self.hass, entry, target, [(subentry, array_title(inverter_title, name), data)]
                )
                return self.async_abort(reason="moved", description_placeholders={"site": target.title})

        current = self._data[CONF_INVERTER]
        if current not in inverters:
            current = next(iter(inverter_titles(entry)), next(iter(inverters)))
        labels = {
            uid: f"{site.title}{SEPARATOR}{title}" if len(sites) > 1 else title
            for uid, (site, title) in inverters.items()
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self._name): str,
                vol.Required(CONF_INVERTER, default=current): _inverter_selector(labels),
            }
        )
        return self.async_show_form(step_id="name_inverter", data_schema=schema, errors=errors)

    async def async_step_panels(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the size mode and orientation, then the details."""
        if user_input is not None:
            # other directions may now be in front of the panel
            self._needs_shading = (user_input[CONF_AZIMUTH], user_input[CONF_TILT]) != (
                self._data[CONF_AZIMUTH],
                self._data[CONF_TILT],
            )
            self._data.update(user_input)
            return await self.async_step_details()
        return self.async_show_form(
            step_id="panels", data_schema=vol.Schema(_orientation_fields(self._data))
        )

    async def async_step_details(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Panel size, thermal and optical values."""
        uses_area = self._data[CONF_SIZE_MODE] == "area"
        if user_input is not None:
            self._data.update(user_input)
            if uses_area:
                self._data[CONF_CELLS_MAX_POWER] = 0
            else:
                self._data[CONF_CELLS_AREA] = 0
                self._data[CONF_CELLS_EFFICIENCY] = 0
            if self._needs_shading:
                return await self.async_step_shading()
            return self._async_save()

        d = self._data
        tilt = d[CONF_TILT]
        fields: dict[Any, Any] = {}
        if uses_area:
            fields[vol.Required(CONF_CELLS_AREA, description={"suggested_value": d.get(CONF_CELLS_AREA) or None})] = _number(0.01, 100_000, unit="m²")
            fields[vol.Required(CONF_CELLS_EFFICIENCY, description={"suggested_value": d.get(CONF_CELLS_EFFICIENCY) or None})] = _number(0.1, 100, unit="%")
        else:
            fields[vol.Required(CONF_CELLS_MAX_POWER, description={"suggested_value": d.get(CONF_CELLS_MAX_POWER) or None})] = _number(1, 10_000_000, unit="W")
        fields.update(
            {
                vol.Required(CONF_TEMP_COEFF, default=d.get(CONF_TEMP_COEFF, DEFAULT_TEMP_COEFF)): _number(-2, 0, unit="%/°C"),
                vol.Required(CONF_COOLING_FACTOR, default=d.get(CONF_COOLING_FACTOR, DEFAULT_COOLING_FACTOR)): _number(0, 0.2, unit="°C·m²/W"),
                vol.Required(
                    CONF_DIFFUSE_EFFICIENCY,
                    default=d.get(CONF_DIFFUSE_EFFICIENCY, SolarPowerPlant.calcDiffuseEfficiency(tilt)),
                ): _number(0, 100, unit="%"),
                vol.Required(CONF_ALBEDO, default=d.get(CONF_ALBEDO, DEFAULT_ALBEDO)): _number(0, 1),
            }
        )
        return self.async_show_form(step_id="details", data_schema=vol.Schema(fields))

    async def async_step_shading(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Obstacle elevation and opacity per 10° direction, like the table in the app.

        Directions where the sun never stands in front of the panel are prefilled as
        blocked and shown collapsed; the others show the highest sun elevation there.
        """
        latitude = self._get_entry().data[CONF_LATITUDE]
        highest = await self.hass.async_add_executor_job(
            maxSunElevationPerBin, latitude, self._data[CONF_AZIMUTH], self._data[CONF_TILT]
        )
        reachable = [i for i in range(SHADING_BINS) if highest[i] is not None]
        unreachable = [i for i in range(SHADING_BINS) if highest[i] is None]

        errors: dict[str, str] = {}
        if user_input is not None:
            texts = {**user_input.pop(SECTION_UNREACHABLE, {}), **user_input}
            elevation: list[float] = []
            opacity: list[float] = []
            for i in range(SHADING_BINS):
                try:
                    e, o = _parse_direction(texts.get(_direction_key(i), ""))
                except ValueError:
                    # errors inside the collapsed section are shown for the whole form
                    errors[_direction_key(i) if i in reachable else "base"] = "invalid_direction"
                    continue
                elevation.append(e)
                opacity.append(o)
            if not errors:
                self._data[CONF_SHADING_ELEVATION] = elevation
                self._data[CONF_SHADING_OPACITY] = opacity
                return self._async_save()

        stored = list(
            zip(
                self._data.get(CONF_SHADING_ELEVATION) or [None] * SHADING_BINS,
                self._data.get(CONF_SHADING_OPACITY) or [None] * SHADING_BINS,
                strict=True,
            )
        )

        def default(i: int, fallback: tuple[float, float]) -> str:
            e, o = stored[i]
            # unreachable directions don't affect the result, so "no obstacle" there is shown as blocked
            if e is None or (fallback == BLOCKED and e == 0):
                return _direction_text(*fallback)
            return _direction_text(e, o)

        fields: dict[Any, Any] = {
            vol.Optional(_direction_key(i), default=default(i, (0.0, 0.0))): str for i in reachable
        }
        if unreachable:
            fields[vol.Required(SECTION_UNREACHABLE)] = section(
                vol.Schema(
                    {vol.Optional(_direction_key(i), default=default(i, BLOCKED)): str for i in unreachable}
                ),
                {"collapsed": True},
            )
        sun = "\n".join(f"- {i * 10}–{i * 10 + 10}°: up to {highest[i]:.0f}°" for i in reachable)
        return self.async_show_form(
            step_id="shading",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"sun": sun or "-"},
        )
