"""Fixtures for SolXpect Sites tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
import sqlite3
import sys
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd
import pytest
import pytest_socket

pytest_plugins = "pytest_homeassistant_custom_component"

if sys.platform == "win32":
    # HA's test plugin blocks all non-Unix sockets, but on Windows the asyncio event loop
    # itself needs a local socket pair. Connections stay limited to 127.0.0.1, and the
    # weather request is mocked anyway (mock_weather).
    pytest_socket.disable_socket = lambda *args, **kwargs: None

LATITUDE = 53.5
LONGITUDE = 10.0


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components."""
    return


def fake_weather(latitude, longitude, start_dt, end_dt, intervalSeconds=3600, historical=False):
    """Clear-sky-like weather: irradiance follows the hour of day, labels are interval ends."""
    times = pd.date_range(start_dt, end_dt, freq=timedelta(seconds=intervalSeconds), tz="UTC")
    hours = np.asarray(times.hour + times.minute / 60)
    sun = np.clip(np.clip(hours - 6, 0, 12) * np.clip(18 - hours, 0, 12) / 36, 0, 1)
    return pd.DataFrame(
        {
            "time": times,
            "direct_normal_irradiance": 700 * sun,
            "diffuse_radiation": 120 * sun,
            "shortwave_radiation": 600 * sun,
            "temperature_2m": 15.0,
        }
    )


@pytest.fixture(autouse=True)
def mock_weather() -> Generator:
    """No network in tests: Site.forecast gets synthetic weather."""
    with patch(
        "custom_components.solxpect_sites.solxpect.Site.fetch_open_meteo_data",
        side_effect=fake_weather,
    ) as mock:
        yield mock


APP_COLUMNS = [
    "cities_to_watch_id", "city_id", "rank", "city_name", "longitude", "latitude",
    "cells_max_pPower", "cells_area", "cells_efficiency", "diffuse_efficiency",
    "inverter_power_limit", "inverter_efficiency", "azimuth_angle", "tilt_angle",
    "shading_elevation", "shading_opacity", "cells_temp_coeff", "albedo", "is_central_inverter",
]


def app_row(rank, name, *, central, limit=8000, power=3000, area=0.0, efficiency=0.0,
            latitude=LATITUDE, azimuth=180.0, tilt=30.0):
    """One plant row as stored by the solXpect app."""
    shading = ",".join("0" for _ in range(36))
    return (rank, rank, rank, name, LONGITUDE, latitude, power, area, efficiency, 93.0,
            limit, 97.0, azimuth, tilt, shading, shading, -0.3, 0.18, int(central))


def make_app_backup(path, rows):
    """Write a ZIP like the app's backup (databases/SQLITE.db)."""
    db_path = path / "SQLITE.db"
    conn = sqlite3.connect(db_path)
    conn.execute(f"CREATE TABLE CITIES_TO_WATCH ({', '.join(APP_COLUMNS)})")
    conn.executemany(
        f"INSERT INTO CITIES_TO_WATCH VALUES ({', '.join('?' * len(APP_COLUMNS))})", rows
    )
    conn.commit()
    conn.close()
    zip_path = path / "solXpect.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(db_path, "databases/SQLITE.db")
    return zip_path
