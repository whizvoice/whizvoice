"""
Location Tools - Tools for saving and managing user locations
"""

import logging
import json
import os
from typing import Dict, Any
import httpx
from preferences import get_preference, set_preference

# Configure logging
logger = logging.getLogger(__name__)

# Load Google Maps API key from test_credentials.json
def load_google_maps_api_key():
    """Load the Google Maps geocoding API key from test_credentials.json"""
    try:
        credentials_path = os.path.join(os.path.dirname(__file__), 'test_credentials.json')
        with open(credentials_path, 'r') as f:
            credentials = json.load(f)
            return credentials.get('google_maps', {}).get('geocoding_api_key')
    except Exception as e:
        logger.error(f"Error loading Google Maps API key: {str(e)}")
        return None

GOOGLE_MAPS_API_KEY = load_google_maps_api_key()

async def geocode_location(location_name: str) -> Dict[str, Any]:
    """
    Use Google Maps Geocoding API to convert a location name to lat/lon coordinates.

    Args:
        location_name: Human-readable location (address, city, landmark, etc.)

    Returns:
        Dictionary with 'success', 'formatted_address', 'latitude', 'longitude', or 'error'
    """
    if not GOOGLE_MAPS_API_KEY:
        logger.error("Google Maps API key not configured")
        return {
            "success": False,
            "error": "Google Maps API key not configured"
        }

    try:
        # Call Google Maps Geocoding API
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": location_name,
            "key": GOOGLE_MAPS_API_KEY
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        # Check if we got results
        if data.get("status") != "OK" or not data.get("results"):
            error_message = data.get("error_message", data.get("status", "Unknown error"))
            logger.warning(f"Geocoding failed for '{location_name}': {error_message}")
            return {
                "success": False,
                "error": f"Could not find location '{location_name}': {error_message}"
            }

        # Extract first result
        result = data["results"][0]
        location = result["geometry"]["location"]

        return {
            "success": True,
            "formatted_address": result["formatted_address"],
            "latitude": round(location["lat"], 4),  # 4 decimal places as recommended by Weather.gov
            "longitude": round(location["lng"], 4)
        }

    except httpx.TimeoutException:
        logger.error(f"Timeout geocoding location: {location_name}")
        return {
            "success": False,
            "error": "Request timed out while geocoding location"
        }
    except Exception as e:
        logger.error(f"Error geocoding location '{location_name}': {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Error geocoding location: {str(e)}"
        }


async def save_location(location_name: str, location_type: str, user_id: str) -> dict:
    """
    Save a location to user preferences after geocoding it.

    Args:
        location_name: Human-readable location (e.g., "Seattle", "1600 Amphitheatre Parkway", "Golden Gate Bridge")
        location_type: Type of location (e.g., "weather_default", "home", "work")
        user_id: The user ID

    Returns:
        Dictionary with success status and message
    """
    try:
        logger.info(f"Saving location '{location_name}' as '{location_type}' for user {user_id}")

        # Geocode the location
        geocode_result = await geocode_location(location_name)

        if not geocode_result.get("success"):
            return {
                "success": False,
                "error": geocode_result.get("error", "Failed to geocode location")
            }

        # Get existing locations from preferences
        locations_json = get_preference(user_id, 'locations')
        if locations_json:
            try:
                locations = json.loads(locations_json) if isinstance(locations_json, str) else locations_json
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse existing locations for user {user_id}, creating new")
                locations = {}
        else:
            locations = {}

        # Add/update the location
        locations[location_type] = {
            "name": geocode_result["formatted_address"],
            "latitude": geocode_result["latitude"],
            "longitude": geocode_result["longitude"]
        }

        # Save back to preferences
        success = set_preference(user_id, 'locations', json.dumps(locations))

        if success:
            logger.info(f"Successfully saved location '{location_type}' for user {user_id}")
            return {
                "success": True,
                "message": f"Saved location '{geocode_result['formatted_address']}' as '{location_type}'",
                "location": locations[location_type]
            }
        else:
            logger.error(f"Failed to save location preference for user {user_id}")
            return {
                "success": False,
                "error": "Failed to save location to preferences"
            }

    except Exception as e:
        logger.error(f"Error in save_location for user {user_id}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Error saving location: {str(e)}"
        }


# Define the location tools for Claude
location_tools = [
    {
        "type": "custom",
        "name": "save_location",
        "description": "Save a location to user preferences with geocoding. The location can be an address, city, landmark, or any place name. Use 'weather_default' as the location_type when the user wants to set their default location for weather. Other useful location types: 'home', 'work'. This tool will convert the location name to coordinates and store both the human-readable name and coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location_name": {
                    "type": "string",
                    "description": "Human-readable location name (e.g., 'Seattle', '1600 Amphitheatre Parkway, Mountain View, CA', 'Golden Gate Bridge', 'New York City')"
                },
                "location_type": {
                    "type": "string",
                    "description": "Type of location to save. Use 'weather_default' for the default weather location. Other options: 'home', 'work', or any custom name."
                }
            },
            "required": ["location_name", "location_type"]
        }
    }
]
