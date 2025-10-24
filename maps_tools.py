"""
Maps Tools - Tools for Google Maps navigation and location search via Android Accessibility Service
"""

import logging
import json
import uuid
import asyncio
from typing import Optional, Dict, Any

# Configure logging
logger = logging.getLogger(__name__)

async def search_google_maps_location(address: str, user_id: str = None, websocket = None,
                                     tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Search for a location/address in Google Maps and display the first result.

    Args:
        address: The location, address, or place name to search for
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the search operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Searching Google Maps for location: '{address}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for Google Maps search")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "search_google_maps_location",
            "request_id": tool_request_id,
            "params": {
                "address": address
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps search message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent search_google_maps_location command for '{address}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for Google Maps search result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (15 seconds for search operations)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"Google Maps search result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for Google Maps search result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to search for '{address}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in search_google_maps_location for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to search Google Maps: {str(e)}",
            "success": False
        }


async def recenter_google_maps(user_id: str = None, websocket = None,
                               tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Re-center the Google Maps view to the user's current location.

    Args:
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the recenter operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Re-centering Google Maps (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for Google Maps recenter")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "recenter_google_maps",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps recenter message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent recenter_google_maps command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for Google Maps recenter result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (10 seconds for recenter operation)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=10.0
                )

                logger.info(f"Google Maps recenter result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for Google Maps recenter result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": "Command to recenter map sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in recenter_google_maps for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to recenter Google Maps: {str(e)}",
            "success": False
        }


async def select_location_from_list(selection: Optional[str] = None, user_id: str = None, websocket = None,
                                   tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Select a specific location from the Google Maps 'See locations' list.

    Args:
        selection: How to select - ordinal ('first', 'second', 'third') or address fragment to match. Defaults to 'first'.
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the selection operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        # Default to 'first' if not specified
        if not selection:
            selection = 'first'

        logger.info(f"Selecting location from list: '{selection}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for location selection")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "select_location_from_list",
            "request_id": tool_request_id,
            "params": {
                "selection": selection
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending location selection message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent select_location_from_list command for '{selection}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for location selection result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (15 seconds for selection operations)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"Location selection result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for location selection result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to select '{selection}' location sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in select_location_from_list for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to select location: {str(e)}",
            "success": False
        }


async def get_google_maps_directions(mode: Optional[str] = None, user_id: str = None, websocket = None,
                                     tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Get directions to a location that's currently displayed in Google Maps.

    Args:
        mode: Optional. Mode of transportation - 'drive', 'walk', 'bike', or 'transit'. Defaults to 'drive' if not specified.
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the directions operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        # Normalize mode
        valid_modes = ['drive', 'walk', 'bike', 'transit']
        if mode:
            mode = mode.lower()
            if mode not in valid_modes:
                logger.warning(f"Invalid transportation mode '{mode}', defaulting to 'drive'")
                mode = 'drive'
        else:
            mode = 'drive'

        logger.info(f"Getting Google Maps directions with mode '{mode}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for Google Maps directions")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "get_google_maps_directions",
            "request_id": tool_request_id,
            "params": {
                "mode": mode
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps directions message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent get_google_maps_directions command with mode '{mode}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for Google Maps directions result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (15 seconds for directions operations)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"Google Maps directions result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for Google Maps directions result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to get directions with mode '{mode}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in get_google_maps_directions for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to get Google Maps directions: {str(e)}",
            "success": False
        }


# Define the maps tools for Claude
maps_tools = [
    {
        "type": "custom",
        "name": "search_google_maps_location",
        "description": "Search for a location, address, or place in Google Maps and display the first result. IMPORTANT: Google Maps must already be open - use launch_app tool first to open Google Maps if needed. This will search for the location and show it on the map.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "The location, address, or place name to search for in Google Maps"
                }
            },
            "required": ["address"]
        }
    },
    {
        "type": "custom",
        "name": "get_google_maps_directions",
        "description": "Get directions to a location that's currently displayed in Google Maps. IMPORTANT: A location must already be displayed in Google Maps - use search_google_maps_location first if needed. This will show the directions to the currently displayed location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Mode of transportation. Valid options: 'drive' (car), 'walk' (walking), 'bike' (bicycle), or 'transit' (public transportation). Defaults to 'drive' if not specified.",
                    "enum": ["drive", "walk", "bike", "transit"]
                }
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "recenter_google_maps",
        "description": "Re-center the Google Maps view to the user's current location. This is useful during navigation when the user wants to see their current position on the map.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "select_location_from_list",
        "description": "Select a specific location from the Google Maps 'See locations' list. Use this after searching for a business name that returned multiple locations. You can select by position (e.g., 'first', 'second', 'third') or by matching part of the address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selection": {
                    "type": "string",
                    "description": "How to select the location. Can be an ordinal like 'first', 'second', 'third' or a part of the address to match (e.g., 'Market St', 'Daly City'). Defaults to 'first' if not specified."
                }
            },
            "required": []
        }
    }
]
