# Weather MCP Server

A Model Context Protocol (MCP) server for querying weather information from OpenWeatherMap API.

## Features

- **Get Current Weather**: Query current weather for any city
- **Weather Forecast**: Get 5-day weather forecast with 3-hour intervals
- **Batch Queries**: Get weather for multiple cities at once

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set API Key

Get a free API key from [OpenWeatherMap](https://openweathermap.org/api)

```bash
export OPENWEATHER_API_KEY="your_api_key_here"
```

## Tools

### get_weather

Get current weather for a specific city.

**Parameters:**

- `city` (string): City name (e.g., "London", "New York", "Tokyo")

**Returns:**

- `city`: City name
- `description`: Weather description
- `temperature_celsius`: Current temperature
- `humidity_percent`: Humidity percentage
- `wind_speed_ms`: Wind speed in m/s
- `feels_like_celsius`: Feels like temperature
- `pressure_hpa`: Atmospheric pressure
- `visibility_meters`: Visibility distance
- `cloudiness_percent`: Cloud coverage
- `raw_data`: Full API response

### get_weather_forecast

Get weather forecast for a city.

**Parameters:**

- `city` (string): City name
- `days` (integer, optional): Number of days to forecast (default: 5)

**Returns:**

- `city`: City name
- `forecast_count`: Number of forecast entries
- `forecasts`: Array of forecast data with timestamps

### get_weather_multiple

Get weather for multiple cities at once.

**Parameters:**

- `cities` (list of strings): List of city names

**Returns:**

- `weather_data`: Dictionary with weather data for each city

## Running the Server

```bash
python server.py
```

The server will start on SSE transport at port 1339.

## Configuration

The server uses the following environment variables:

- `OPENWEATHER_API_KEY`: Your OpenWeatherMap API key (required)

## MCP Client Configuration

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "weather": {
      "command": "python",
      "args": ["/path/to/server.py"],
      "env": {
        "OPENWEATHER_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

## Free Tier Limitations

- Current weather: Unlimited calls
- Forecast: 5-day forecast, 3-hour steps
- Batch requests: Recommended to query 1-10 cities at a time
- Rate limit: ~60 calls/minute for free tier

## Error Handling

The server gracefully handles:

- Invalid city names
- API rate limiting
- Network timeouts
- Missing or invalid API key

Each request returns a status field indicating success or failure.
