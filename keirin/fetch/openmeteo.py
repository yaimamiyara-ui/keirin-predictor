import logging
from typing import Any, Dict, List, Optional
from keirin.fetch.http import PoliteClient, PoliteError

logger = logging.getLogger(__name__)


def parse_openmeteo(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    wind_speeds = hourly.get("wind_speed_10m", [])
    wind_dirs = hourly.get("wind_direction_10m", [])
    temps = hourly.get("temperature_2m", [])
    precips = hourly.get("precipitation", [])

    results = []
    n = len(times)
    for i in range(n):
        results.append({
            "time": times[i] if i < len(times) else None,
            "wind_speed": wind_speeds[i] if i < len(wind_speeds) else None,
            "wind_dir_deg": wind_dirs[i] if i < len(wind_dirs) else None,
            "temp": temps[i] if i < len(temps) else None,
            "precip": precips[i] if i < len(precips) else None,
        })
    return results


def fetch_openmeteo_hourly(
    client: PoliteClient, lat: float, lon: float, start_date: str, end_date: str
) -> List[Dict[str, Any]]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "wind_speed_10m,wind_direction_10m,temperature_2m,precipitation",
        "timezone": "Asia/Tokyo",
        "wind_speed_unit": "ms",
    }
    data = client.get_json(url, params=params)
    return parse_openmeteo(data)


def fetch_openmeteo_forecast(client: PoliteClient, lat: float, lon: float) -> List[Dict[str, Any]]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "past_days": 1,
        "forecast_days": 2,
        "hourly": "wind_speed_10m,wind_direction_10m,temperature_2m,precipitation",
        "timezone": "Asia/Tokyo",
        "wind_speed_unit": "ms",
    }
    data = client.get_json(url, params=params)
    return parse_openmeteo(data)
