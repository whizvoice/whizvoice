import logging
import json
import uuid

# Configure logging
logger = logging.getLogger(__name__)

def launch_app(app_name: str, user_id: str = None) -> dict:
    """
    Create a WebSocket message to trigger app launch on the Android device.
    
    This tool doesn't directly launch the app - instead, it returns a special
    response that signals the server should send a tool_execution message to 
    the Android app via WebSocket.
    
    Args:
        app_name: The name of the app to launch (e.g., "YouTube", "Chrome", "Maps")
        user_id: The user ID (for logging purposes)
    
    Returns:
        A dictionary containing the WebSocket message to send to the Android app
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"Creating app launch request for '{app_name}' (user: {user_id}, request: {tool_request_id})")
        
        # Return a special response that indicates a WebSocket message should be sent
        # The server will recognize this format and send it via WebSocket instead of 
        # returning it to Claude
        return {
            "_websocket_action": "tool_execution",  # Special marker for WebSocket routing
            "_request_id": tool_request_id,
            "tool": "launch_app",
            "params": {
                "app_name": app_name
            },
            "_user_message": f"Launching {app_name} on your device..."  # Message to show user
        }
        
    except Exception as e:
        logger.error(f"Error creating app launch request for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to create app launch request: {str(e)}",
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