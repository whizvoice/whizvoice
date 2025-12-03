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

async def agent_search_google_maps_location(address_keyword: str, user_id: str = None, websocket = None,
                                     tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Search for a SPECIFIC ADDRESS or LOCATION in Google Maps and automatically select the first result.

    This tool is fully automatic: it clicks the first search suggestion. If that suggestion happens to be
    'See locations' (which can occur if a business name was accidentally used instead of an address),
    it automatically selects the first location from that list as a fallback.

    Args:
        address_keyword: A specific address or location (e.g., "1885 Mission St", "Mission and 5th", "Golden Gate Bridge")
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

        logger.info(f"Searching Google Maps for location: '{address_keyword}' (user: {user_id}, request: {tool_request_id})")

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
            "tool": "agent_search_google_maps_location",
            "request_id": tool_request_id,
            "params": {
                "address": address_keyword
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps search message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_search_google_maps_location command for '{address_keyword}'")
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
                "message": f"Command to search for '{address_keyword}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in search_google_maps_location for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to search Google Maps: {str(e)}",
            "success": False
        }


async def agent_search_google_maps_phrase(search_phrase: str, user_id: str = None, websocket = None,
                                     tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Search Google Maps with a discovery phrase and display the search results list without selecting any.
    Use this for browsing/discovery searches like "korean food", "cafes near me", "pizza", etc.

    Args:
        search_phrase: The search phrase for discovery (e.g., "korean food", "cafes near me", "pizza restaurants")
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

        logger.info(f"Searching Google Maps with phrase: '{search_phrase}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for Google Maps phrase search")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_search_google_maps_phrase",
            "request_id": tool_request_id,
            "params": {
                "search_phrase": search_phrase
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps phrase search message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_search_google_maps_phrase command for '{search_phrase}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for Google Maps phrase search result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (15 seconds for search operations)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"Google Maps phrase search result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for Google Maps phrase search result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to search for '{search_phrase}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in search_google_maps_phrase for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to search Google Maps: {str(e)}",
            "success": False
        }


async def agent_recenter_google_maps(user_id: str = None, websocket = None,
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
            "tool": "agent_recenter_google_maps",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps recenter message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_recenter_google_maps command")
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


async def agent_select_location_from_list(position: Optional[int] = None, fragment: Optional[str] = None,
                                   user_id: str = None, websocket = None,
                                   tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Select a specific location from a Google Maps search results list.

    Args:
        position: Select by position number (1 for first, 2 for second, etc.). Takes precedence over fragment.
        fragment: Select by matching part of the business name or address (e.g., "Market St", "Mandalay").
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

        # Default to position 1 if neither specified
        if position is None and fragment is None:
            position = 1

        selection_desc = f"position {position}" if position else f"fragment '{fragment}'"
        logger.info(f"Selecting location from list: {selection_desc} (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for location selection")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        params = {}
        if position is not None:
            params["position"] = position
        if fragment is not None:
            params["fragment"] = fragment

        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_select_location_from_list",
            "request_id": tool_request_id,
            "params": params,
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending location selection message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_select_location_from_list command: {selection_desc}")
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


async def agent_fullscreen_google_maps(user_id: str = None, websocket = None,
                                 tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Bring Google Maps to fullscreen/foreground when it's running in the background or shown as a small overlay.

    Args:
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the fullscreen operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Fullscreening Google Maps (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for Google Maps fullscreen")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_fullscreen_google_maps",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps fullscreen message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_fullscreen_google_maps command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for Google Maps fullscreen result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (10 seconds for fullscreen operation)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=10.0
                )

                logger.info(f"Google Maps fullscreen result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for Google Maps fullscreen result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": "Command to fullscreen map sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in fullscreen_google_maps for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to fullscreen Google Maps: {str(e)}",
            "success": False
        }


async def agent_get_google_maps_directions(mode: Optional[str] = None, already_in_directions: bool = False,
                                     user_id: str = None, websocket = None,
                                     tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Get directions to a location that's currently displayed in Google Maps.

    Args:
        mode: Optional. Mode of transportation - 'drive', 'walk', 'bike', or 'transit'. If not specified, uses Google Maps' currently selected mode (usually the user's last used mode).
        already_in_directions: Optional. Set to true if already viewing directions for the SAME DESTINATION and want to get directions to a different place. This will press back first before getting new directions.
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

        # Normalize mode if provided
        if mode:
            valid_modes = ['drive', 'walk', 'bike', 'transit']
            mode = mode.lower()
            if mode not in valid_modes:
                logger.warning(f"Invalid transportation mode '{mode}', will use default")
                mode = None

        logger.info(f"Getting Google Maps directions with mode '{mode}', already_in_directions={already_in_directions} (user: {user_id}, request: {tool_request_id})")

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
            "tool": "agent_get_google_maps_directions",
            "request_id": tool_request_id,
            "params": {
                "mode": mode,
                "already_in_directions": already_in_directions
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending Google Maps directions message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_get_google_maps_directions command with mode '{mode}'")
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
        "name": "agent_search_google_maps_location",
        "description": "Search for a SPECIFIC ADDRESS or LOCATION in Google Maps and automatically select the first result. This tool automatically opens Google Maps. Use this for addresses ('1885 Mission St'), cross streets ('Mission and 5th'), landmarks ('Golden Gate Bridge'), or specific named places. Do NOT use for general searches like 'coffee' or 'restaurants' - use search_google_maps_phrase for those. This tool results in a single location displayed on the map. After calling this tool, you can call get_google_maps_directions if the user wants directions to the place.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address_keyword": {
                    "type": "string",
                    "description": "A specific address or location (e.g., '1885 Mission St', 'Mission and 5th', 'Golden Gate Bridge', 'Dolores Park')"
                }
            },
            "required": ["address_keyword"]
        }
    },
    {
        "type": "custom",
        "name": "agent_search_google_maps_phrase",
        "description": "Search Google Maps with a discovery/browsing phrase and display the list of results WITHOUT selecting any. This tool automatically opens Google Maps. Use this when the user wants to BROWSE or DISCOVER options like 'korean food', 'cafes near me', 'pizza restaurants', 'gas stations', etc. This tool shows the search results list. After using this tool, you MUST ask the user to select one. Based on what they say, you can use the select_location_from_list tool to actually select an item from the list of results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_phrase": {
                    "type": "string",
                    "description": "The discovery search phrase (e.g., 'korean food', 'cafes near me', 'pizza restaurants', 'gas stations nearby')"
                }
            },
            "required": ["search_phrase"]
        }
    },
    {
        "type": "custom",
        "name": "agent_get_google_maps_directions",
        "description": "Get directions to a location that's currently displayed in Google Maps. This tool automatically opens Google Maps. If you do not know the mode of transportation, you must call this tool without the mode specified so it can use the user's default mode of transportation. A location must already be displayed in Google Maps - this tool is meant to be used after search_google_maps_location or select_location_from_list. If you have JUST already called get_google_maps_directions successfully and are just changing the mode of transportation, you will have to set already_in_directions to true, otherwise, make sure it is set to false or leave it as its default value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Mode of transportation. Valid options: 'drive' (car), 'walk' (walking), 'bike' (bicycle), or 'transit' (public transportation). No specific mode of transporation is selected if not provided, which defaults to the mode last used by Google Maps.",
                    "enum": ["drive", "walk", "bike", "transit"]
                },
                "already_in_directions": {
                    "type": "boolean",
                    "description": "Set to true if already viewing directions. This will press back first before getting new directions. Defaults to false."
                }
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_recenter_google_maps",
        "description": "Re-center the Google Maps view to the user's current location. This tool automatically opens Google Maps. This is useful during navigation when the user wants to see their current position on the map.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_fullscreen_google_maps",
        "description": "Bring Google Maps to fullscreen/foreground when it's running in the background or shown as a small overlay. This tool automatically opens Google Maps. Use this when the user asks you to make Google Maps big or fullscreen.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_select_location_from_list",
        "description": "Select a specific location from a Google Maps search results list. This tool automatically opens Google Maps. This must be used after user responds to search_google_maps_phrase results, to select the user's choice. This function can select by position (1 for first item, 2 for second, etc.) or by matching part of the business name or address. If your goal was to get directions for the location, you MUST call get_google_maps_directions afterwards.",
        "input_schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "integer",
                    "description": "Select by position number: 1 for first item, 2 for second, etc. Takes precedence over fragment if both provided."
                },
                "fragment": {
                    "type": "string",
                    "description": "Select by matching part of the business name or address (e.g., 'Market St', 'Daly City', 'Mandalay'). Used if position not provided."
                }
            },
            "required": []
        }
    }
]
