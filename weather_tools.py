"""
Weather Tools - Tools for getting weather forecasts using Weather.gov API
"""

import logging
import json
from typing import Dict, Any, Optional
import httpx
from preferences import get_preference, set_preference
from location_tools import geocode_location

# Configure logging
logger = logging.getLogger(__name__)

# User-Agent required by Weather.gov API
WEATHER_GOV_USER_AGENT = "WhizVoice/1.0 (contact@whizvoice.com)"


async def get_weather(days_ahead: int, user_id: str, location: Optional[str] = None, temperature_units: Optional[str] = None) -> dict:
    """
    Get weather forecast for a specific number of days ahead.

    Args:
        days_ahead: Number of days ahead to get forecast for (0 = today, 1 = tomorrow, etc., max 6)
        user_id: The user ID
        location: Location to use.
        temperature_units: Temperature units - "us" for Fahrenheit or "si" for Celsius. If not provided, uses saved preference or defaults to "us".

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

        # Default to weather_default if no location specified
        if location is None:
            location = "weather_default"

        logger.info(f"Getting weather for user {user_id}, location '{location}', days_ahead {days_ahead}")

        # Try to get location from saved preferences first
        locations_json = get_preference(user_id, 'locations')
        saved_locations = {}

        if locations_json:
            try:
                saved_locations = json.loads(locations_json) if isinstance(locations_json, str) else locations_json
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse locations JSON for user {user_id}, will attempt to geocode location string")
                # Don't fail here - the location string may still be geocodeable
                # Only fail if location is "weather_default" (handled below)

        # Check if location is a saved location type
        location_data = saved_locations.get(location)

        if location_data:
            # Using a saved location
            latitude = location_data.get("latitude")
            longitude = location_data.get("longitude")
            location_name = location_data.get("name", f"{latitude}, {longitude}")

            if latitude is None or longitude is None:
                logger.error(f"Saved location '{location}' missing coordinates for user {user_id}")
                return {
                    "success": False,
                    "error": "Saved location is missing coordinates"
                }
        else:
            # Not a saved location - try to geocode it
            logger.info(f"Location '{location}' not in saved locations, attempting to geocode")

            # Special case: if they don't have weather_default saved and didn't specify a location
            if location == "weather_default":
                logger.warning(f"No weather_default location saved for user {user_id}")
                return {
                    "success": False,
                    "error": "Failed to get weather because user has no weather_default location set. Please ask the user what location they'd like to use for weather forecasts and set weather_default with the save_location tool."
                }

            # Try to geocode the location string
            geocode_result = await geocode_location(location)

            if not geocode_result.get("success"):
                error_msg = geocode_result.get("error", "Failed to geocode location")
                logger.warning(f"Failed to geocode location '{location}': {error_msg}")
                return {
                    "success": False,
                    "error": f"Could not find location '{location}'. Is it a location name that needs to be defined by save_location tool, such as 'home' or 'work'? : {error_msg}"
                }

            latitude = geocode_result["latitude"]
            longitude = geocode_result["longitude"]
            location_name = geocode_result["formatted_address"]
            logger.info(f"Successfully geocoded '{location}' to {location_name} ({latitude}, {longitude})")


        logger.info(f"Fetching weather for {location_name} ({latitude}, {longitude})")

        # Get temperature units preference
        saved_preference = get_preference(user_id, 'temperature_units')

        # Determine units to use and save preference if needed
        if not saved_preference:
            # No saved preference exists
            if temperature_units:
                # Parameter provided, save it
                logger.info(f"No saved temperature preference found, saving provided units: {temperature_units}")
                set_preference(user_id, 'temperature_units', temperature_units)
                units_to_use = temperature_units
            else:
                # No parameter provided, save default "us"
                logger.info(f"No saved temperature preference found and no parameter provided, saving default: us")
                set_preference(user_id, 'temperature_units', 'us')
                units_to_use = "us"
        elif temperature_units:
            # Saved preference exists, but parameter provided - use parameter
            if saved_preference != temperature_units:
                set_preference(user_id, 'temperature_units', temperature_units)
            units_to_use = temperature_units
        else:
            # Use saved preference
            units_to_use = saved_preference

        logger.info(f"Using temperature units: {units_to_use}")

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

            # Step 2: Get the actual forecast with temperature units
            try:
                # Add units parameter to the forecast URL
                forecast_params = {"units": units_to_use}
                forecast_response = await client.get(forecast_url, headers=headers, params=forecast_params, timeout=10.0)
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


async def set_temperature_units(unit: str, user_id: str) -> dict:
    """
    Set the user's preferred temperature unit for weather forecasts.

    Args:
        unit: Temperature unit - either "us" for Fahrenheit or "si" for Celsius
        user_id: The user ID

    Returns:
        Dictionary with success status and message
    """
    try:
        # Validate the unit parameter
        if unit not in ["us", "si"]:
            logger.warning(f"Invalid temperature unit value: {unit}")
            return {
                "success": False,
                "error": "Invalid temperature unit. Must be 'us' (Fahrenheit) or 'si' (Celsius)."
            }

        # Check if the value is already set
        current = get_preference(user_id, 'temperature_units')
        if current == unit:
            unit_name = "Fahrenheit" if unit == "us" else "Celsius"
            return {
                "success": True,
                "message": f"Temperature unit already set to {unit_name}. No update needed."
            }

        # Set the preference
        if set_preference(user_id, 'temperature_units', unit):
            unit_name = "Fahrenheit" if unit == "us" else "Celsius"
            logger.info(f"Successfully set temperature units to {unit} ({unit_name}) for user {user_id}")
            return {
                "success": True,
                "message": f"Successfully set temperature unit to {unit_name}."
            }
        else:
            logger.error(f"Failed to save temperature_units preference for user {user_id}")
            return {
                "success": False,
                "error": "Failed to save temperature unit preference."
            }

    except Exception as e:
        logger.error(f"Error in set_temperature_units for user {user_id}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Error setting temperature units: {str(e)}"
        }


# Define the weather tools for Claude
weather_tools = [
    {
        "type": "custom",
        "name": "get_weather",
        "description": "Get weather forecast for a specific number of days ahead (0-6 days). Uses the Weather.gov API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days ahead to get forecast for. 0 = today, 1 = tomorrow, 2 = day after tomorrow, etc. Must be between 0 and 6 (Weather.gov provides 7-day forecasts)."
                },
                "location": {
                    "type": "string",
                    "description": "Location string. Do not submit this parameter if the user did not specify, so that the user's default weather location can be used."
                },
                "temperature_units": {
                    "type": "string",
                    "description": "Temperature units: 'us' for Fahrenheit or 'si' for Celsius. Do not submit this parameter unless user asked for Fahrenheit or Celsius specifically as their temperature_units preference will be used by default.",
                    "enum": ["us", "si"]
                }
            },
            "required": ["days_ahead"]
        }
    },
    {
        "type": "custom",
        "name": "set_temperature_units",
        "description": "Set the user's preferred temperature unit. Use 'us' for Fahrenheit or 'si' for Celsius. Use this when the user asks you to remember their preference for Fahrenheit or Celsius.",
        "input_schema": {
            "type": "object",
            "properties": {
                "unit": {
                    "type": "string",
                    "description": "Temperature unit: 'us' for Fahrenheit or 'si' for Celsius",
                    "enum": ["us", "si"]
                }
            },
            "required": ["unit"]
        }
    }
]
