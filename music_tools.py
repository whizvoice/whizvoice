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

async def agent_play_youtube_music(query: str, content_type: str = "song",
                            user_id: str = None, websocket = None,
                            tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Play a song on YouTube Music by searching for it and playing the first result.

    Args:
        query: The song, artist, album, or playlist to search for
        content_type: Type of content to play - "song", "album", "artist", "episode", "video", or "community_playlist"
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

        logger.info(f"Playing YouTube Music: '{query}' (user: {user_id}, request: {tool_request_id}, content_type={content_type})")

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
            "tool": "agent_play_youtube_music",
            "request_id": tool_request_id,
            "params": {
                "query": query,
                "content_type": content_type
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending YouTube Music play message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_play_youtube_music command for '{query}'")
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

async def agent_queue_youtube_music(query: str, user_id: str = None, websocket = None,
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
            "tool": "agent_queue_youtube_music",
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
            logger.info(f"Successfully sent agent_queue_youtube_music command for '{query}'")
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

async def agent_pause_youtube_music(user_id: str = None, websocket = None,
                                    tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Pause or resume YouTube Music playback by toggling the play/pause button.

    Args:
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the pause/resume operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Pausing/resuming YouTube Music (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for YouTube Music pause")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_pause_youtube_music",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending YouTube Music pause message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_pause_youtube_music command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for YouTube Music pause result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (5 seconds should be enough for pause)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=5.0
                )

                logger.info(f"YouTube Music pause result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for YouTube Music pause result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": "Command to pause/resume sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in pause_youtube_music for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to pause/resume YouTube Music: {str(e)}",
            "success": False
        }

# Define the music tools for Claude
music_tools = [
    {
        "type": "custom",
        "name": "manage_music_app_preference",
        "description": "Get or set the user's preferred music app. Use action 'get' to check which app is preferred before playing music. Use action 'set' with music_app to change it. Valid music_app options: 'youtube_music' or 'spotify'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set"],
                    "description": "Whether to get or set the music app preference"
                },
                "music_app": {
                    "type": "string",
                    "description": "The music app to use. Valid options: 'youtube_music' or 'spotify' (required for 'set' action)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "type": "custom",
        "name": "agent_youtube_music",
        "description": "Play or queue music on YouTube Music. This tool automatically opens YouTube Music, searches for the query, and plays or queues the first matching result. Use action 'play' to play immediately, or 'queue' to add to the queue. You MUST specify content_type when using 'play' action. If the user hasn't specified a music app, please check their music app preference first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "queue"],
                    "description": "Whether to play immediately or add to queue"
                },
                "query": {
                    "type": "string",
                    "description": "The song name, artist, album, or playlist. Examples: 'Bohemian Rhapsody', 'Taylor Swift', 'Abbey Road', 'Chill vibes'"
                },
                "content_type": {
                    "type": "string",
                    "enum": ["song", "album", "artist", "video", "episode", "community_playlist"],
                    "description": "The type of content. Use 'song' for specific songs (default). Use 'album' for albums. Use 'artist' for artist pages/radio. Use 'video' for music videos. Use 'episode' for podcasts. Use 'community_playlist' for playlists or genre-based requests. Required for 'play' action."
                }
            },
            "required": ["action", "query"]
        }
    },
    {
        "type": "custom",
        "name": "agent_pause_youtube_music",
        "description": "Pause or resume YouTube Music playback. Toggles between playing and paused states. Use this when the user wants to stop the music or resume playback.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]
