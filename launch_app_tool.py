import logging
import json
import uuid
import asyncio
from typing import Optional, Dict, Any

# Configure logging
logger = logging.getLogger(__name__)

async def launch_app(app_name: str, user_id: str = None, websocket = None, 
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
        
        logger.info(f"Launching app '{app_name}' (user: {user_id}, request: {tool_request_id})")
        
        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for app launch")
            return {
                "error": "No connection to device available",
                "success": False
            }
        
        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "launch_app",
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
            logger.info(f"Successfully sent launch_app command for '{app_name}'")
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
                # Wait for tool result with timeout (30 seconds to give Android more time)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=30.0
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

# Define the launch app tool for Claude
launch_app_tools = [
    {
        "type": "custom",
        "name": "launch_app",
        "description": "Launch an application on the user's Android device. Use this when the user asks to open or launch an app like YouTube, Chrome, Maps, Gmail, Camera, Settings, etc.",
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
    }
]