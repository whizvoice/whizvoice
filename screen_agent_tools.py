"""
Screen Agent Tools - Tools for app launching and app control via Android Accessibility Service
"""

import logging
import json
import uuid
import asyncio
from typing import Optional, Dict, Any

# Configure logging
logger = logging.getLogger(__name__)

async def agent_launch_app(app_name: str, user_id: str = None, websocket = None,
                     tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Launch an application on the user's Android device via WebSocket.

    This tool sends a tool_execution message directly to the Android app via WebSocket
    and waits for the result.

    Args:
        app_name: The name of the app to launch (e.g., "YouTube", "Chrome", "Maps")
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the app launch operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        # Debug logging for optimistic ID migration investigation
        logger.info(f"🔧 TOOL_DEBUG agent_launch_app: app_name={app_name}, user_id={user_id}")
        logger.info(f"🔧 TOOL_DEBUG agent_launch_app: websocket is {'present' if websocket else 'None'}")
        logger.info(f"🔧 TOOL_DEBUG agent_launch_app: conversation_id={conversation_id}")
        if websocket:
            try:
                logger.info(f"🔧 TOOL_DEBUG agent_launch_app: websocket.client_state={websocket.client_state}")
            except Exception as state_err:
                logger.warning(f"🔧 TOOL_DEBUG agent_launch_app: Could not get websocket.client_state: {state_err}")

        logger.info(f"Launching app '{app_name}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error(f"🔧 TOOL_DEBUG agent_launch_app: FAILED - No WebSocket, user_id={user_id}, conversation_id={conversation_id}")
            logger.error("No WebSocket connection available for app launch")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_launch_app",
            "request_id": tool_request_id,
            "params": {
                "app_name": app_name
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending tool_execution message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_launch_app command for '{app_name}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for tool result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (5 seconds for faster response)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=5.0
                )

                logger.info(f"Tool execution result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for tool result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to launch {app_name} sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in launch_app for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to launch app: {str(e)}",
            "success": False
        }

async def agent_disable_continuous_listening(user_id: str = None, websocket = None,
                                      tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Disable continuous listening mode on the user's Android device.

    Args:
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        # Debug logging for optimistic ID migration investigation
        logger.info(f"🔧 TOOL_DEBUG agent_disable_continuous_listening: user_id={user_id}")
        logger.info(f"🔧 TOOL_DEBUG agent_disable_continuous_listening: websocket is {'present' if websocket else 'None'}")
        logger.info(f"🔧 TOOL_DEBUG agent_disable_continuous_listening: conversation_id={conversation_id}")
        if websocket:
            try:
                logger.info(f"🔧 TOOL_DEBUG agent_disable_continuous_listening: websocket.client_state={websocket.client_state}")
            except Exception as state_err:
                logger.warning(f"🔧 TOOL_DEBUG agent_disable_continuous_listening: Could not get websocket.client_state: {state_err}")

        logger.info(f"Disabling continuous listening (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error(f"🔧 TOOL_DEBUG agent_disable_continuous_listening: FAILED - No WebSocket, user_id={user_id}, conversation_id={conversation_id}")
            logger.error("No WebSocket connection available for disable_continuous_listening")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_disable_continuous_listening",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending disable_continuous_listening message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent disable_continuous_listening command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for disable_continuous_listening result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=3.0
                )

                logger.info(f"Disable continuous listening result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for disable_continuous_listening result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to disable continuous listening sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in disable_continuous_listening for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to disable continuous listening: {str(e)}",
            "success": False
        }

async def agent_set_tts_enabled(enabled: bool, user_id: str = None, websocket = None,
                         tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Enable or disable text-to-speech for bot responses on the user's Android device.

    Args:
        enabled: True to enable TTS, False to disable TTS
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        # Debug logging for optimistic ID migration investigation
        logger.info(f"🔧 TOOL_DEBUG agent_set_tts_enabled: enabled={enabled}, user_id={user_id}")
        logger.info(f"🔧 TOOL_DEBUG agent_set_tts_enabled: websocket is {'present' if websocket else 'None'}")
        logger.info(f"🔧 TOOL_DEBUG agent_set_tts_enabled: conversation_id={conversation_id}")
        if websocket:
            try:
                logger.info(f"🔧 TOOL_DEBUG agent_set_tts_enabled: websocket.client_state={websocket.client_state}")
            except Exception as state_err:
                logger.warning(f"🔧 TOOL_DEBUG agent_set_tts_enabled: Could not get websocket.client_state: {state_err}")

        logger.info(f"Setting TTS enabled to {enabled} (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error(f"🔧 TOOL_DEBUG agent_set_tts_enabled: FAILED - No WebSocket, user_id={user_id}, conversation_id={conversation_id}")
            logger.error("No WebSocket connection available for set_tts_enabled")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_set_tts_enabled",
            "request_id": tool_request_id,
            "params": {
                "enabled": enabled
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending set_tts_enabled message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent set_tts_enabled command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for set_tts_enabled result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=3.0
                )

                logger.info(f"Set TTS enabled result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for set_tts_enabled result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to set TTS enabled={enabled} sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in set_tts_enabled for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to set TTS enabled: {str(e)}",
            "success": False
        }

async def agent_close_app(user_id: str = None, websocket = None,
                          tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Close the WhizVoice app on the user's Android device.
    This will fully exit the application, stopping all services and activities.

    Args:
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Closing app (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for close_app")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_close_app",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending close_app message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent close_app command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # Don't wait for result - the app will be closing and the WebSocket will disconnect
        return {
            "status": "sent",
            "message": "Close app command sent to device",
            "request_id": tool_request_id,
            "success": True
        }

    except Exception as e:
        logger.error(f"Error in close_app for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to close app: {str(e)}",
            "success": False
        }

# Define the Screen Agent tools for Claude
screen_agent_tools = [
    {
        "type": "custom",
        "name": "agent_launch_app",
        "description": "Launch an application on the user's Android device. This will also show a bubble overlay for easy return to WhizVoice. Use this when the user asks to open or launch an app like YouTube, Chrome, Maps, Gmail, Camera, Settings, WhatsApp, etc. For WhatsApp messaging, always launch WhatsApp first before using WhatsApp-specific tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The name of the app to launch (e.g., 'YouTube', 'Chrome', 'Maps', 'Gmail', 'Camera', 'Photos', 'Calendar', 'Calculator', 'Clock', 'Messages', 'WhatsApp', 'Instagram', 'Facebook', 'Twitter', 'Spotify', 'Netflix', 'Settings')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "type": "custom",
        "name": "agent_disable_continuous_listening",
        "description": "Turn off the microphone, also known as continuous listening mode, on the user's WhizVoice app. After calling this, the user will need to manually press the microphone button to speak again. Note that microphone/continuous listening mode is completely INDEPENDENT of text to speech mode. You should NOT enable or disable continuous listening to modify text to speech mode.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_set_tts_enabled",
        "description": "Enable or disable text-to-speech (TTS) for bot responses on the user's WhizVoice app. When enabled, bot responses will be spoken aloud. When disabled, responses will only be shown as text. Note that text to speech mode is completely INDEPENDENT of microphone/continuous listening mode. You should NOT enable or disable continuous listening first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true to enable Text To Speech (voice responses), false to disable Text To Speech (text-only responses)"
                }
            },
            "required": ["enabled"]
        }
    },
    {
        "type": "custom",
        "name": "agent_close_app",
        "description": "Close the WhizVoice app completely. This will exit the app, stopping all voice listening and background services. Use this when the user wants to close, exit, or quit the app.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]
