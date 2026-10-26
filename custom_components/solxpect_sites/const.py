"""Constants for SolXpect Sites."""

from datetime import timedelta

DOMAIN = "solxpect_sites"

UPDATE_INTERVAL = timedelta(hours=1)

# Config entry (site)
CONF_INTERVAL = "interval"  # minutes per forecast interval: "60" or "15"
CONF_BEAM_MODE = "beam_mode"  # "substep" or "app", see solxpect.PanelArray

INTERVALS = ["60", "15"]
BEAM_MODES = ["substep", "app"]
DEFAULT_SITE_NAME = "Home"

# Subentry types
SUBENTRY_INVERTER = "inverter"
SUBENTRY_ARRAY = "array"

# Inverter subentry
CONF_SITE = "site"  # only in the change form: entry_id of the site to move the inverter to
CONF_EFFICIENCY = "efficiency"  # %
CONF_POWER_LIMIT = "power_limit"  # W, AC

# Array subentry; its title is "<inverter> › <name>", so HA lists the arrays grouped by inverter
CONF_INVERTER = "inverter"  # unique_id of the inverter subentry
CONF_SIZE_MODE = "size_mode"  # "power" or "area"
CONF_CELLS_MAX_POWER = "cells_max_power"  # Wp
CONF_CELLS_AREA = "cells_area"  # m²
CONF_CELLS_EFFICIENCY = "cells_efficiency"  # %
CONF_TEMP_COEFF = "temp_coeff"  # %/°C
CONF_COOLING_FACTOR = "cooling_factor"  # °C per W/m²
CONF_DIFFUSE_EFFICIENCY = "diffuse_efficiency"  # %
CONF_ALBEDO = "albedo"
CONF_AZIMUTH = "azimuth"  # °, 0 = north, 90 = east
CONF_TILT = "tilt"  # °, 0 = horizontal, 90 = vertical
CONF_SHADING_ELEVATION = "shading_elevation"  # 36 values, °
CONF_SHADING_OPACITY = "shading_opacity"  # 36 values, %

SIZE_MODES = ["power", "area"]
SHADING_BINS = 36

DEFAULT_INVERTER_EFFICIENCY = 97.0
DEFAULT_TEMP_COEFF = -0.35
DEFAULT_COOLING_FACTOR = 0.0342
DEFAULT_ALBEDO = 0.18
