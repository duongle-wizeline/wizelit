"""
Weather MCP Server (FastMCP wrapper)
Provides tools to query weather information from OpenWeatherMap API.
Runs via FastMCP SSE on port 1339 by default.
"""

import asyncio
import os
import sys
from typing import Optional

# Ensure repo root on path so local packages resolve BEFORE imports
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.wizelit_agent_wrapper import WizelitAgentWrapper
import httpx

# Initialize FastMCP wrapper (SSE transport, port 1339 to avoid clashing with other servers)
mcp = WizelitAgentWrapper("WeatherAgent", transport="sse", port=1339)

# Get API key from environment variable
API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_OPENWEATHER_API_KEY")
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


@mcp.ingest(is_long_running=False, description="Get current weather for a specific city.")
async def get_weather(city: str):
    """
    Get current weather for a specific city.

    Args:
        city: The name of the city (e.g., London, New York, Tokyo).

    Returns:
        Dictionary containing weather information including temperature,
        humidity, wind speed, and weather description.
    """
    def _run():
        try:
            response = httpx.get(
                BASE_URL,
                params={
                    "q": city,
                    "appid": API_KEY,
                    "units": "metric"  # Metric = Celsius
                },
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            return {
                "city": city,
                "description": data['weather'][0]['description'],
                "temperature_celsius": data['main']['temp'],
                "humidity_percent": data['main']['humidity'],
                "wind_speed_ms": data['wind']['speed'],
                "feels_like_celsius": data['main']['feels_like'],
                "pressure_hpa": data['main']['pressure'],
                "visibility_meters": data.get('visibility', 'N/A'),
                "cloudiness_percent": data['clouds']['all'],
                "raw_data": data
            }

        except httpx.HTTPStatusError as e:
            return {
                "error": f"API Error: {e.response.status_code}",
                "message": e.response.text,
                "city": city
            }
        except Exception as e:
            return {
                "error": "Error fetching weather",
                "message": str(e),
                "city": city
            }

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Get weather forecast for a city (requires API key with forecast access).")
async def get_weather_forecast(city: str, days: int = 5):
    """
    Get weather forecast for a city.

    Args:
        city: The name of the city.
        days: Number of days to forecast (max 5 for free tier).

    Returns:
        Dictionary containing forecast data for multiple days.
    """
    def _run():
        try:
            forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
            response = httpx.get(
                forecast_url,
                params={
                    "q": city,
                    "appid": API_KEY,
                    "units": "metric",
                    "cnt": days * 8  # 8 forecasts per day (3-hour intervals)
                },
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            # Process forecast data
            forecasts = []
            for forecast_item in data.get('list', []):
                forecasts.append({
                    "date_time": forecast_item['dt_txt'],
                    "temperature_celsius": forecast_item['main']['temp'],
                    "humidity_percent": forecast_item['main']['humidity'],
                    "wind_speed_ms": forecast_item['wind']['speed'],
                    "description": forecast_item['weather'][0]['description'],
                    "cloudiness_percent": forecast_item['clouds']['all']
                })

            return {
                "city": city,
                "forecast_count": len(forecasts),
                "forecasts": forecasts,
                "raw_data": data
            }

        except httpx.HTTPStatusError as e:
            return {
                "error": f"API Error: {e.response.status_code}",
                "message": e.response.text,
                "city": city
            }
        except Exception as e:
            return {
                "error": "Error fetching forecast",
                "message": str(e),
                "city": city
            }

    return await asyncio.to_thread(_run)


@mcp.ingest(is_long_running=False, description="Get weather for multiple cities at once.")
async def get_weather_multiple(cities: list[str]):
    """
    Get current weather for multiple cities.

    Args:
        cities: List of city names to query.

    Returns:
        Dictionary with weather data for each city.
    """
    def _run():
        results = {}
        for city in cities:
            try:
                response = httpx.get(
                    BASE_URL,
                    params={
                        "q": city,
                        "appid": API_KEY,
                        "units": "metric"
                    },
                    timeout=10.0
                )
                response.raise_for_status()
                data = response.json()

                results[city] = {
                    "temperature_celsius": data['main']['temp'],
                    "humidity_percent": data['main']['humidity'],
                    "wind_speed_ms": data['wind']['speed'],
                    "description": data['weather'][0]['description'],
                    "status": "success"
                }
            except Exception as e:
                results[city] = {
                    "error": str(e),
                    "status": "failed"
                }

        return {"weather_data": results}

    return await asyncio.to_thread(_run)


if __name__ == "__main__":
    mcp.run()
