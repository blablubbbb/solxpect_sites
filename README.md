# SolXpect Sites

A Home Assistant integration that forecasts the output of PV installations with **several inverters and panel arrays** per location. It uses the solar model of the [solXpect](https://github.com/woheller69/solxpect) app, as reimplemented in [solXpect_meets_python](https://github.com/blablubbbb/solXpect_meets_python), with weather from [Open-Meteo](https://open-meteo.com).

## What it does

- **Site** = one location. Each site has **inverters**, and each inverter has **panel arrays** (groups of panels with the same orientation, e.g. one string).
- Each inverter limits the **sum of its own arrays** to its AC power limit. A shared inverter with two strings and a separate micro inverter are modelled correctly.
- Hourly or **15-minute** forecasts (Open-Meteo's 15-minute data is native in Central Europe).
- Two calculation modes: **sub-steps** (angle of incidence and shading averaged every 7.5 minutes) or **like the app** (identical to solXpect).
- **Import from the solXpect app**: upload the app's backup ZIP and choose the plants.
- Forecast in the **Energy dashboard** (as solar forecast of the site).
- Shading input like in the app: one row per 10° direction. Directions where the sun never stands in front of the panel are prefilled as blocked; the others show the highest sun elevation there.

## Installation

HACS → ⋮ → Custom repositories → `https://github.com/blablubbbb/solxpect_sites`, type *Integration*. Download **SolXpect Sites** and restart Home Assistant. Requires Home Assistant 2026.10 or newer.

## Setup

Settings → Devices & services → Add integration → **SolXpect Sites**:

- **Set up manually**: name, location (defaults to your home), interval and calculation mode. Then, on the site's page, use **Add inverter** and **Add panel array**.
- **Import from a solXpect app backup**: upload the ZIP that the app exports and choose the plants. Plants without "central inverter" get their own inverter; plants with "central inverter" share one inverter whose limit is the sum of their limits. If the plants in the app are alternative variants of the same installation, choose only one.

Each panel array asks for:

| Setting | Meaning |
|---|---|
| Panel size | **Rated power** (Wp, from the datasheet) **or** cell area and efficiency |
| Azimuth, tilt | 0° = north, 90° = east, 180° = south; tilt 0° = horizontal, 90° = vertical |
| Temperature coefficient | From the datasheet, e.g. −0.30 %/°C |
| Cooling factor | Cell temperature = air temperature + factor × irradiance. Default 0.0342 ("not so well cooled", as in the app); see the [library README](https://github.com/blablubbbb/solXpect_meets_python) for typical values per mounting |
| Diffuse light efficiency | Default follows from the tilt |
| Albedo | Ground reflectivity: grass ~0.2, asphalt ~0.1, snow 0.6–0.8 |
| Shading | Per 10° direction: obstacle elevation and opacity as `elevation/opacity`, e.g. `35/100` (a single number means opaque, `0` no obstacle). Only directions the sun can reach in front of the panel are shown; the rest are prefilled as blocked |

Panel arrays are listed as **"inverter › array"**, so the site's page shows them grouped by inverter.

## Changing and moving

- **Panel array** (⚙): choose **Name and inverter**, **Orientation, size and panel values**, or **Shading**. Only the chosen part is asked for; a new azimuth or tilt also asks for the shading again. Choosing an inverter of another site moves the array there.
- **Inverter** (⚙): with more than one site, the **Site** field moves the inverter, together with its arrays, to another site. Its sensors keep their entity IDs.

## Sensors

For the site (total) and for each inverter: **Power now** (W), **Energy today**, **Energy remaining today** and **Energy tomorrow** (kWh). The energy sensors have the forecast per interval as attribute `intervals`. The site's device ("Total of all inverters") belongs to no inverter, so Home Assistant lists it under "Devices that don't belong to a sub-entry".

## Development

The calculation code in `custom_components/solxpect_sites/solxpect/` is a copy of the library's `calcs/` package. Don't edit it here; change the library and run `python scripts/sync_library.py`.

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and run on Linux (CI). On Windows, run them with `PYTHONPATH=tests/winstubs`, which provides stand-ins for two Unix-only modules.

## License

GPL-3.0, like solXpect and solXpect_meets_python.
