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

async def cancel_pending_screen_tools(device_id: str = None, **kwargs) -> dict:
    """
    Cancel all pending (queued, not executing) screen agent tools.

    This cancels tools that are waiting in the queue but does NOT stop
    the currently executing tool.

    Args:
        device_id: The device ID to cancel pending tools for
        **kwargs: Additional context (user_id, websocket, etc.)

    Returns:
        A dictionary containing the result of the cancel operation
    """
    from screen_agent_queue import screen_agent_queue

    if not device_id:
        return {
            "error": "device_id is required",
            "success": False
        }

    return await screen_agent_queue.cancel_pending(device_id)


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

async def agent_open_app(user_id: str = None, websocket = None,
                         tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Bring the WhizVoice app from bubble/background mode to the full foreground chat view.

    Args:
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the operation
    """
    try:
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Opening app to foreground (user: {user_id}, request: {tool_request_id})")

        if not websocket:
            logger.error("No WebSocket connection available for open_app")
            return {
                "error": "No connection to device available",
                "success": False
            }

        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_open_app",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id
        }

        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending open_app message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent open_app command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # Wait for result - unlike close_app, the app stays alive
        if tool_result_handler:
            logger.info(f"Waiting for open_app result from Android device (request_id: {tool_request_id})")

            try:
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=5.0
                )

                logger.info(f"Open app result for {tool_request_id}: {result}")
                return result

            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for open_app result (request_id: {tool_request_id})")
                return {
                    "status": "sent",
                    "message": "Open app command sent but timed out waiting for confirmation",
                    "request_id": tool_request_id,
                    "success": True
                }

        return {
            "status": "sent",
            "message": "Open app command sent to device",
            "request_id": tool_request_id,
            "success": True
        }

    except Exception as e:
        logger.error(f"Error in open_app for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to open app: {str(e)}",
            "success": False
        }

async def agent_close_other_app(app_name: str, user_id: str = None, websocket = None,
                                tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Close another app running on the user's Android device by dismissing it from recent apps.

    Args:
        app_name: The name of the app to close (e.g., "YouTube", "Chrome", "Maps")
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the close operation
    """
    try:
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Closing other app '{app_name}' (user: {user_id}, request: {tool_request_id})")

        if not websocket:
            logger.error("No WebSocket connection available for close_other_app")
            return {
                "error": "No connection to device available",
                "success": False
            }

        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_close_other_app",
            "request_id": tool_request_id,
            "params": {
                "app_name": app_name
            },
            "conversation_id": conversation_id
        }

        try:
            message_json = json.dumps(tool_execution_message)
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_close_other_app command for '{app_name}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        if tool_result_handler:
            logger.info(f"Waiting for close_other_app result (request_id: {tool_request_id})")

            try:
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=15.0
                )

                logger.info(f"Close other app result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for close_other_app result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            return {
                "status": "sent",
                "message": f"Command to close {app_name} sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in close_other_app for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to close app: {str(e)}",
            "success": False
        }


async def agent_log_health_data(data_type: str, value: float, user_id: str = None, websocket = None,
                                tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Log a health data point to the user's device.

    The Android client writes to Health Connect first. For data_type="calories" it
    falls back to Fitbit UI automation if Health Connect is unavailable or lacks
    write permission. For data_type="weight", it is Health-Connect-only.

    Args:
        data_type: "calories" or "weight"
        value: kcal for calories, kg for weight
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the operation, including a 'source'
        field ('health_connect' or 'fitbit_ui') indicating where the data landed.
    """
    try:
        if data_type not in ("calories", "weight"):
            return {"error": f"Unknown data_type: {data_type}", "success": False}

        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Logging health data: type={data_type} value={value} (user: {user_id}, request: {tool_request_id})")

        if not websocket:
            logger.error("No WebSocket connection available for agent_log_health_data")
            return {
                "error": "No connection to device available",
                "success": False
            }

        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_log_health_data",
            "request_id": tool_request_id,
            "params": {
                "data_type": data_type,
                "value": value,
            },
            "conversation_id": conversation_id
        }

        try:
            message_json = json.dumps(tool_execution_message)
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent agent_log_health_data command: {data_type}={value}")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        if tool_result_handler:
            logger.info(f"Waiting for agent_log_health_data result (request_id: {tool_request_id})")

            try:
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=30.0
                )

                logger.info(f"agent_log_health_data result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for agent_log_health_data result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            return {
                "status": "sent",
                "message": f"Command to log {value} {data_type} sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in agent_log_health_data for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to log health data: {str(e)}",
            "success": False
        }


async def agent_open_health_app_settings(user_id: str = None,
                                         websocket = None, tool_result_handler = None,
                                         conversation_id: str = None) -> dict:
    """
    Ask the user (via an in-app dialog) whether to open Health Connect settings so they
    can connect an app. On confirm, the Android client launches Health Connect's
    'Your health apps' page, where the user picks which app to wire up.

    Use this as a follow-up when agent_log_health_data returns an `unconnected_health_apps`
    array and the user agrees to set up the connection.

    Returns:
        Dict with `success: bool` indicating whether the settings page was opened.
    """
    try:
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"
        logger.info(f"Opening Health Connect settings (user: {user_id}, request: {tool_request_id})")

        if not websocket:
            return {"error": "No connection to device available", "success": False}

        tool_execution_message = {
            "type": "tool_execution",
            "tool": "agent_open_health_app_settings",
            "request_id": tool_request_id,
            "params": {},
            "conversation_id": conversation_id,
        }

        try:
            await websocket.send_text(json.dumps(tool_execution_message))
            logger.info("Sent agent_open_health_app_settings command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {e}")
            return {"status": "error", "error": f"Failed to send command: {e}", "success": False}

        if tool_result_handler:
            try:
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id, timeout=70.0
                )
                logger.info(f"agent_open_health_app_settings result for {tool_request_id}: {result}")
                return result
            except Exception as e:
                logger.error(f"Error waiting for agent_open_health_app_settings result: {e}")
                return {"status": "error", "error": f"Error waiting for device: {e}", "success": False}

        return {
            "status": "sent",
            "message": "Command to open Health Connect settings sent to device",
            "request_id": tool_request_id,
        }
    except Exception as e:
        logger.error(f"Error in agent_open_health_app_settings for user {user_id}: {e}")
        return {"error": f"Failed to open Health Connect settings: {e}", "success": False}


async def _send_tool_and_wait(tool_name: str, params: dict, user_id, websocket,
                              tool_result_handler, conversation_id, timeout: float = 5.0) -> dict:
    """Shared helper: send a tool_execution message over the WebSocket and await the result."""
    tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"
    logger.info(f"{tool_name} invoked (user: {user_id}, request: {tool_request_id}, params: {params})")

    if not websocket:
        logger.error(f"{tool_name}: No WebSocket connection available")
        return {"error": "No connection to device available", "success": False}

    tool_execution_message = {
        "type": "tool_execution",
        "tool": tool_name,
        "request_id": tool_request_id,
        "params": params,
        "conversation_id": conversation_id,
    }

    try:
        await websocket.send_text(json.dumps(tool_execution_message))
        logger.info(f"Sent {tool_name} to device (request: {tool_request_id})")
    except Exception as e:
        logger.error(f"{tool_name}: Failed to send WebSocket message: {e}")
        return {"status": "error", "error": f"Failed to send command to device: {e}", "success": False}

    if not tool_result_handler:
        return {"status": "sent", "message": f"{tool_name} sent to device", "request_id": tool_request_id}

    try:
        result = await tool_result_handler.wait_for_tool_result(
            request_id=tool_request_id, timeout=timeout
        )
        logger.info(f"{tool_name} result for {tool_request_id}: {result}")
        return result
    except Exception as e:
        logger.error(f"{tool_name}: Error waiting for tool result: {e}")
        return {"status": "error", "error": f"Error waiting for device response: {e}", "success": False}


async def agent_press_back(user_id: str = None, websocket=None,
                           tool_result_handler=None, conversation_id: str = None) -> dict:
    """Press the system back button on the user's Android device."""
    return await _send_tool_and_wait(
        "agent_press_back", {}, user_id, websocket, tool_result_handler, conversation_id
    )


async def agent_get_ui(scope: str = "interactable", user_id: str = None, websocket=None,
                       tool_result_handler=None, conversation_id: str = None) -> dict:
    """Dump the current on-screen accessibility tree so the LLM can pick element_ids."""
    params = {"scope": scope} if scope else {}
    return await _send_tool_and_wait(
        "agent_get_ui", params, user_id, websocket, tool_result_handler, conversation_id,
        timeout=8.0,
    )


async def agent_peek_app(app_name: str, scope: str = "full", user_id: str = None,
                         websocket=None, tool_result_handler=None,
                         conversation_id: str = None) -> dict:
    """Briefly bring a target app to the foreground, dump its UI, then restore the
    previous app. Read-only — useful for answering questions about backgrounded apps."""
    params = {"app_name": app_name, "scope": scope}
    return await _send_tool_and_wait(
        "agent_peek_app", params, user_id, websocket, tool_result_handler, conversation_id,
        timeout=10.0,
    )


async def agent_click(element_id: int, user_id: str = None, websocket=None,
                      tool_result_handler=None, conversation_id: str = None) -> dict:
    """Click the element identified by element_id from the last agent_get_ui call."""
    return await _send_tool_and_wait(
        "agent_click", {"element_id": element_id},
        user_id, websocket, tool_result_handler, conversation_id,
    )


async def agent_insert_text(text: str, element_id: int = None, user_id: str = None,
                            websocket=None, tool_result_handler=None,
                            conversation_id: str = None) -> dict:
    """Insert text into an input field. If element_id is omitted, targets the focused
    editable, or the sole editable on screen."""
    params: dict = {"text": text}
    if element_id is not None:
        params["element_id"] = element_id
    return await _send_tool_and_wait(
        "agent_insert_text", params,
        user_id, websocket, tool_result_handler, conversation_id,
    )


# Define the Screen Agent tools for Claude
screen_agent_tools = [
    {
        "type": "custom",
        "name": "agent_app_control",
        "description": "Launch or close an application on the user's Android device. Use action 'launch' to open an app (also shows a bubble overlay for easy return to WhizVoice). Use action 'close' to close another app by dismissing it from recent apps. Note: to close WhizVoice itself, use agent_close_app instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["launch", "close"],
                    "description": "Whether to launch or close the app"
                },
                "app_name": {
                    "type": "string",
                    "description": "The name of the app (e.g., 'YouTube', 'Chrome', 'Maps', 'Gmail', 'WhatsApp', 'Spotify', 'Settings')"
                }
            },
            "required": ["action", "app_name"]
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
        "description": "Stop or close the WhizVoice app completely. This will exit the app, stopping all voice listening and background services. Use this when the user wants to close, exit, stop, or quit, or wants you to go away, or says something like 'that's it' or 'that's all' or 'I'm good now'. This function automatically full screens Google Maps if there is an active Google Maps navigation in overlay mode, so the user can continue their navigation.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_open_app",
        "description": "Bring the WhizVoice app from bubble mode to the full foreground chat view. Use this when the user asks to open the app or make it bigger. No-op if the app is already in the foreground.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "cancel_pending_screen_tools",
        "description": "Cancel all pending screen agent tools that are waiting in the queue. This does NOT stop the currently executing tool, only tools that haven't started yet. Use this when the user wants to cancel or stop queued actions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_log_health_data",
        "description": "Log a health data point on the user's device. Use this when the user wants to log calories consumed or a body weight measurement. If the user names a food without a calorie count, estimate the calorie count to call this tool. If the result has `reason: \"requires_connection\"`, the value WAS written to Health Connect, but no health app is connected so the user can't see the logged data. The `unconnected_health_apps` array names the apps that aren't connected. EVERY TIME you get this result, even if you already offered earlier in the conversation, you MUST name the unconnected app(s) and tell the user the data won't show up there until they set up the connection, and offer to open Health Connect settings via agent_open_health_app_settings so user can connect an app.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data_type": {
                    "type": "string",
                    "enum": ["calories", "weight"],
                    "description": "Which kind of data to log. 'calories' = food calories consumed; 'weight' = body weight."
                },
                "value": {
                    "type": "number",
                    "description": "The numeric value. For 'calories', kilocalories (e.g. 500). For 'weight', kilograms (e.g. 72.5). Convert from lbs to kg before calling."
                }
            },
            "required": ["data_type", "value"]
        }
    },
    {
        "type": "custom",
        "name": "agent_open_health_app_settings",
        "description": "Open Health Connect's 'Your health apps' settings page so the user can connect a health app to Health Connect. Shows an in-app confirmation dialog, and on confirm launches Health Connect settings (NOT a third-party app itself — the user picks which app to connect from inside Health Connect, and grants permissions there). Use this in TWO scenarios: (1) as a follow-up when agent_log_health_data returned an `unconnected_health_apps` array and the user agrees to set up a connection. (2) when the user directly asks to connect any health app to Health Connect (e.g. 'connect Google Health', 'set up Fitbit with Health Connect', 'connect Samsung Health'). Do NOT call agent_launch_app for Health-Connect-connection requests — this tool's dialog flow is the correct path.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_press_back",
        "description": "Press the system back button on the user's Android device. Use this to dismiss a screen, close a keyboard, or go back to the previous screen.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_get_ui",
        "description": "Dump the current on-screen UI so you can pick an element to click or type into. Returns a list where each interactable node has a stable element_id prefix like [3]. Call this before agent_click or agent_insert_text. Re-call it if the UI has changed since your last dump (e.g. after navigating, typing, or dismissing something).",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["interactable", "full"],
                    "description": "\"interactable\" (default) returns only clickable/editable nodes; \"full\" returns the complete tree with non-interactable labels for more context if necessary, otherwise use interactable to only get what you need."
                }
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_peek_app",
        "description": "Briefly bring a target app to the foreground, dump its on-screen UI as text, and return to whatever app the user was looking at. Use this to answer questions about the current state of a backgrounded app (e.g., \"what song is playing in YouTube Music?\". The user briefly sees the target app flash on screen (~1s). This is READ-ONLY — the previous app is restored immediately after the dump, so you cannot follow up with agent_click or agent_insert_text on the peeked app. If you need to interact with the app, use agent_launch_app + agent_get_ui instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name of the app to peek (e.g., 'YouTube Music', 'WhatsApp', 'Maps'). Same fuzzy matching as agent_launch_app."
                },
                "scope": {
                    "type": "string",
                    "enum": ["full", "interactable"],
                    "description": "\"full\" (default for peek) returns the complete tree including non-interactable text — best when reading state. \"interactable\" returns only clickable/editable nodes."
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "type": "custom",
        "name": "agent_click",
        "description": "Click an on-screen element by its element_id. You MUST call agent_get_ui first to obtain valid element_ids. If the element is not itself clickable, the closest clickable ancestor is used. Fails with an error asking you to re-dump if the UI has changed since the last agent_get_ui.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_id": {
                    "type": "integer",
                    "description": "The integer id shown in square brackets in the most recent agent_get_ui output, e.g. the 3 in [3]."
                }
            },
            "required": ["element_id"]
        }
    },
    {
        "type": "custom",
        "name": "agent_insert_text",
        "description": "Type text into an input field on the current screen. If element_id is provided, it must come from the most recent agent_get_ui call. If omitted, the focused input is used, falling back to the sole editable field on screen. The field is cleared before the new text is inserted. Set submit=true to press the keyboard's Enter/Search/Go action on the field after typing — use this to submit a search bar or send a query in one step instead of looking for a separate search button.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to insert into the input field."
                },
                "element_id": {
                    "type": "integer",
                    "description": "Optional. The element_id from the most recent agent_get_ui call. Omit to target the focused input or the only editable field on screen."
                },
                "submit": {
                    "type": "boolean",
                    "description": "Optional, default false. If true, presses Enter / the keyboard's IME action (Search, Go, Done) on the field after the text is set, submitting it. Use for search bars where there is no obvious search button to click."
                }
            },
            "required": ["text"]
        }
    }
]
