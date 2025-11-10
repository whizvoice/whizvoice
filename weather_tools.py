"""
Weather Tools - Tools for getting weather forecasts using Weather.gov API
"""

import logging
import json
from typing import Dict, Any, Optional
import httpx
from preferences import get_preference

# Configure logging
logger = logging.getLogger(__name__)

# User-Agent required by Weather.gov API
WEATHER_GOV_USER_AGENT = "WhizVoice/1.0 (contact@whizvoice.com)"


async def get_weather(days_ahead: int, user_id: str, location_type: str = "weather_default") -> dict:
    """
    Get weather forecast for a specific number of days ahead.

    Args:
        days_ahead: Number of days ahead to get forecast for (0 = today, 1 = tomorrow, etc., max 6)
        user_id: The user ID
        location_type: Which saved location to use (default: "weather_default")

    Returns:
        Dictionary with weather forecast data or error
    """
    try:
        # Validate days_ahead parameter
        if days_ahead < 0 or days_ahead > 6:
            logger.warning(f"Invalid days_ahead value: {days_ahead}")
            return {
                "success": False,
                "error": "days_ahead must be between 0 (today) and 6 (7 days from now). Weather.gov only provides 7-day forecasts."
            }

        logger.info(f"Getting weather for user {user_id}, location_type '{location_type}', days_ahead {days_ahead}")

        # Get location from preferences
        locations_json = get_preference(user_id, 'locations')

        if not locations_json:
            logger.warning(f"No locations saved for user {user_id}")
            return {
                "success": False,
                "error": "No locations saved. Please save a location first using the save_location tool (use 'weather_default' as the location type for weather)."
            }

        # Parse locations
        try:
            locations = json.loads(locations_json) if isinstance(locations_json, str) else locations_json
        except json.JSONDecodeError:
            logger.error(f"Failed to parse locations JSON for user {user_id}")
            return {
                "success": False,
                "error": "Error reading saved locations"
            }

        # Get the requested location
        location = locations.get(location_type)
        if not location:
            available_types = list(locations.keys())
            logger.warning(f"Location type '{location_type}' not found for user {user_id}")
            return {
                "success": False,
                "error": f"Location type '{location_type}' not found. Available locations: {', '.join(available_types)}. Please save this location first."
            }

        latitude = location.get("latitude")
        longitude = location.get("longitude")
        location_name = location.get("name", f"{latitude}, {longitude}")

        if latitude is None or longitude is None:
            logger.error(f"Location '{location_type}' missing coordinates for user {user_id}")
            return {
                "success": False,
                "error": "Saved location is missing coordinates"
            }

        logger.info(f"Fetching weather for {location_name} ({latitude}, {longitude})")

        # Step 1: Get forecast URL from points endpoint
        points_url = f"https://api.weather.gov/points/{latitude},{longitude}"
        headers = {"User-Agent": WEATHER_GOV_USER_AGENT}

        async with httpx.AsyncClient() as client:
            # Get points metadata
            try:
                points_response = await client.get(points_url, headers=headers, timeout=10.0)
                points_response.raise_for_status()
                points_data = points_response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Location not covered by Weather.gov: {latitude}, {longitude}")
                    return {
                        "success": False,
                        "error": f"Weather.gov does not provide forecasts for this location ({location_name}). The service only covers the United States and its territories."
                    }
                raise
            except Exception as e:
                logger.error(f"Error fetching points data: {str(e)}")
                return {
                    "success": False,
                    "error": f"Error contacting Weather.gov: {str(e)}"
                }

            # Extract forecast URL
            forecast_url = points_data.get("properties", {}).get("forecast")
            if not forecast_url:
                logger.error(f"No forecast URL in points response for {latitude}, {longitude}")
                return {
                    "success": False,
                    "error": "Weather.gov did not return a forecast URL for this location"
                }

            # Step 2: Get the actual forecast
            try:
                forecast_response = await client.get(forecast_url, headers=headers, timeout=10.0)
                forecast_response.raise_for_status()
                forecast_data = forecast_response.json()
            except Exception as e:
                logger.error(f"Error fetching forecast data: {str(e)}")
                return {
                    "success": False,
                    "error": f"Error fetching forecast: {str(e)}"
                }

        # Parse forecast periods
        periods = forecast_data.get("properties", {}).get("periods", [])
        if not periods:
            logger.error("No forecast periods returned")
            return {
                "success": False,
                "error": "No forecast data available"
            }

        # Weather.gov returns 12-hour periods (day/night), so we need to handle this
        # days_ahead=0 means today, which could be multiple periods depending on time of day
        # For simplicity, we'll return the relevant periods for the requested day

        # Calculate which periods correspond to the requested day
        # Each day has 2 periods (day and night), except possibly today
        if days_ahead == 0:
            # Today - return the next 1-2 periods (rest of today)
            relevant_periods = periods[:2]
            day_label = "today"
        else:
            # Future days - estimate period indices
            # If we're in daytime, periods[0] is today-day, periods[1] is today-night
            # days_ahead=1 would be periods[2] (tomorrow-day) and periods[3] (tomorrow-night)
            start_idx = days_ahead * 2
            end_idx = start_idx + 2
            relevant_periods = periods[start_idx:end_idx] if start_idx < len(periods) else []

            if days_ahead == 1:
                day_label = "tomorrow"
            else:
                day_label = f"in {days_ahead} days"

        if not relevant_periods:
            logger.warning(f"No forecast data available for {days_ahead} days ahead")
            return {
                "success": False,
                "error": f"No forecast data available for {day_label}"
            }

        # Format the forecast for the user
        forecast_text = f"Weather forecast for {location_name} {day_label}:\n\n"

        for period in relevant_periods:
            name = period.get("name", "Unknown")
            temp = period.get("temperature")
            temp_unit = period.get("temperatureUnit", "F")
            short_forecast = period.get("shortForecast", "No description")
            detailed_forecast = period.get("detailedForecast", short_forecast)

            forecast_text += f"**{name}:**\n"
            forecast_text += f"Temperature: {temp}°{temp_unit}\n"
            forecast_text += f"{detailed_forecast}\n\n"

        logger.info(f"Successfully retrieved weather forecast for user {user_id}")

        return {
            "success": True,
            "location": location_name,
            "forecast": forecast_text.strip(),
            "periods": relevant_periods  # Include raw data in case it's useful
        }

    except httpx.TimeoutException:
        logger.error(f"Timeout fetching weather data")
        return {
            "success": False,
            "error": "Request timed out while fetching weather data"
        }
    except Exception as e:
        logger.error(f"Error in get_weather for user {user_id}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Error getting weather: {str(e)}"
        }


# Define the weather tools for Claude
weather_tools = [
    {
        "type": "custom",
        "name": "get_weather",
        "description": "Get weather forecast for a specific number of days ahead (0-6 days). Uses Weather.gov API which provides 7-day forecasts. The location is retrieved from user preferences - make sure the user has saved a location first using save_location with location_type 'weather_default'. Days ahead: 0 = today, 1 = tomorrow, 2 = day after tomorrow, etc. Maximum is 6 (7 days from now).",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days ahead to get forecast for. 0 = today, 1 = tomorrow, 2 = day after tomorrow, etc. Must be between 0 and 6 (Weather.gov provides 7-day forecasts)."
                },
                "location_type": {
                    "type": "string",
                    "description": "Which saved location to use. Defaults to 'weather_default'. Other options include 'home', 'work', or any custom location type the user has saved."
                }
            },
            "required": ["days_ahead"]
        }
    }
]
