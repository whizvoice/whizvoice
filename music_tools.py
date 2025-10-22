"""
Music Tools - Tools for music playback and music app preferences
"""

import logging
import json
import uuid
import asyncio
from typing import Optional, Dict, Any, Tuple
from preferences import get_preference, set_preference

# Configure logging
logger = logging.getLogger(__name__)

VALID_MUSIC_APPS = ["youtube_music", "spotify"]

# ================== Music Preference Functions ==================

def get_music_app_preference(user_id: str) -> tuple[bool, str]:
    """Get the user's music app preference from preferences. Returns a tuple of (success, preference_value).
    If no preference is set, returns (False, error message)."""
    preference = get_preference(user_id, 'music_app_preference')
    if not preference:
        return False, "No music app preference set. Please ask the user which music app they prefer to use (YouTube Music or Spotify)."
    else:
        return True, preference

def set_music_app_preference(user_id: str, music_app: str) -> tuple[bool, str]:
    """Set the user's music app preference. Returns a tuple of (success, message).
    Validates the music app choice before setting it. Valid options: youtube_music, spotify.
    If the value is already set to this choice, does nothing."""
    try:
        # Normalize the input
        normalized_app = music_app.lower().replace(" ", "_")

        # Map common variations to standard values
        app_mapping = {
            "youtube_music": "youtube_music",
            "youtubemusic": "youtube_music",
            "youtube": "youtube_music",
            "yt_music": "youtube_music",
            "ytmusic": "youtube_music",
            "spotify": "spotify"
        }

        standard_app = app_mapping.get(normalized_app)

        if not standard_app or standard_app not in VALID_MUSIC_APPS:
            valid_options = ", ".join(VALID_MUSIC_APPS)
            return False, f"Invalid music app: '{music_app}'. Valid options are: {valid_options}"

        # Check if the value is already set
        current = get_preference(user_id, 'music_app_preference')
        if current == standard_app:
            return True, f"Music app preference already set to {standard_app}. No update needed."

        # If we get here, the choice is valid and needs updating
        if set_preference(user_id, 'music_app_preference', standard_app):
            return True, f"Successfully set music app preference to {standard_app}."
        else:
            return False, "Failed to save music app preference."
    except Exception as e:
        logger.error(f"Error setting music app preference for user {user_id}: {str(e)}", exc_info=True)
        return False, f"Error setting music app preference: {str(e)}"

# ================== Music Playback Functions ==================

async def play_youtube_music(query: str, user_id: str = None, websocket = None,
                            tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Play a song on YouTube Music by searching for it and playing the first result.

    Args:
        query: The song, artist, album, or playlist to search for
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the play operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Playing YouTube Music: '{query}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for YouTube Music play")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "play_youtube_music",
            "request_id": tool_request_id,
            "params": {
                "query": query
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending YouTube Music play message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent play_youtube_music command for '{query}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for YouTube Music play result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (15 seconds for searching and playing)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"YouTube Music play result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for YouTube Music play result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to play '{query}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in play_youtube_music for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to play YouTube Music: {str(e)}",
            "success": False
        }

async def queue_youtube_music(query: str, user_id: str = None, websocket = None,
                              tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Add a song to the queue in YouTube Music by searching for it and adding the first result.

    Args:
        query: The song, artist, album, or playlist to search for
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the queue operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Queueing YouTube Music: '{query}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for YouTube Music queue")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "queue_youtube_music",
            "request_id": tool_request_id,
            "params": {
                "query": query
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending YouTube Music queue message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent queue_youtube_music command for '{query}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for YouTube Music queue result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (15 seconds for searching and queueing)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"YouTube Music queue result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for YouTube Music queue result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to queue '{query}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in queue_youtube_music for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to queue YouTube Music: {str(e)}",
            "success": False
        }

# Define the music tools for Claude
music_tools = [
    {
        "type": "custom",
        "name": "get_music_app_preference",
        "description": "Get the user's preferred music app from preferences. Use this before playing music to determine which app to use if the user hasn't specified in their request.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "set_music_app_preference",
        "description": "Set the user's preferred music app (YouTube Music or Spotify). Use this when the user specifies which music app they want to use for playing music. Valid options: 'youtube_music' or 'spotify'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "music_app": {
                    "type": "string",
                    "description": "The music app to use. Valid options: 'youtube_music' or 'spotify'"
                }
            },
            "required": ["music_app"]
        }
    },
    {
        "type": "custom",
        "name": "play_youtube_music",
        "description": "Play a song, album, artist, or playlist on YouTube Music. IMPORTANT: YouTube Music must already be open - use launch_app tool first to open YouTube Music if needed. This will search for the query and play the first result. If the user hasn't specified a music app, please check their music app preference first. They may prefer an app other than YouTube Music.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The song name, artist, album, or playlist to play on YouTube Music. Examples: 'Bohemian Rhapsody', 'Taylor Swift', 'Abbey Road album', 'Chill playlist'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "type": "custom",
        "name": "queue_youtube_music",
        "description": "Add a song, album, artist, or playlist to the queue in YouTube Music. IMPORTANT: YouTube Music must already be open - use launch_app tool first to open YouTube Music if needed. This will search for the query and add the first result to the queue. Use this when the user wants to add music to their queue without immediately playing it. If the user hasn't specified a music app, please check their music app preference first. They may prefer an app other than YouTube Music.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The song name, artist, album, or playlist to add to the queue. Examples: 'Stairway to Heaven', 'The Beatles', 'Thriller album', 'Rock classics playlist'"
                }
            },
            "required": ["query"]
        }
    }
]
