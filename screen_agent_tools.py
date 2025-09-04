"""
Screen Agent Tools - Consolidated tools for screen interaction via Android Accessibility Service
Combines app launching and app-specific interactions (like WhatsApp chat selection)
"""

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

async def whatsapp_select_chat(chat_name: str, user_id: str = None, websocket = None,
                               tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Select a specific chat in WhatsApp on the user's Android device.
    
    This tool first opens WhatsApp and then navigates to the specified chat.
    
    Args:
        chat_name: The name of the chat/contact to select
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context
    
    Returns:
        A dictionary containing the result of the chat selection operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"Selecting WhatsApp chat '{chat_name}' (user: {user_id}, request: {tool_request_id})")
        
        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for WhatsApp chat selection")
            return {
                "error": "No connection to device available",
                "success": False
            }
        
        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "whatsapp_select_chat",
            "request_id": tool_request_id,
            "params": {
                "chat_name": chat_name
            },
            "conversation_id": conversation_id
        }
        
        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending WhatsApp select chat message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent whatsapp_select_chat command for '{chat_name}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }
        
        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for WhatsApp chat selection result from Android device (request_id: {tool_request_id})")
            
            try:
                # Wait for tool result with timeout (10 seconds for accessibility operations)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=10.0
                )
                
                logger.info(f"WhatsApp chat selection result for {tool_request_id}: {result}")
                return result
                
            except Exception as e:
                logger.error(f"Error waiting for WhatsApp chat selection result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to select chat '{chat_name}' sent to device",
                "request_id": tool_request_id
            }
        
    except Exception as e:
        logger.error(f"Error in whatsapp_select_chat for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to select WhatsApp chat: {str(e)}",
            "success": False
        }

async def whatsapp_send_message(message: str, user_id: str = None, websocket = None,
                                tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Send a message in the current WhatsApp chat on the user's Android device.
    
    This tool assumes a WhatsApp chat is already open and sends the specified message.
    
    Args:
        message: The message text to send
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context
    
    Returns:
        A dictionary containing the result of the message send operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"Sending WhatsApp message (user: {user_id}, request: {tool_request_id})")
        
        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for WhatsApp message send")
            return {
                "error": "No connection to device available",
                "success": False
            }
        
        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "whatsapp_send_message",
            "request_id": tool_request_id,
            "params": {
                "message": message
            },
            "conversation_id": conversation_id
        }
        
        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending WhatsApp send message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent whatsapp_send_message command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }
        
        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for WhatsApp message send result from Android device (request_id: {tool_request_id})")
            
            try:
                # Wait for tool result with timeout
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=8.0
                )
                
                logger.info(f"WhatsApp message send result for {tool_request_id}: {result}")
                return result
                
            except Exception as e:
                logger.error(f"Error waiting for WhatsApp message send result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to send message sent to device",
                "request_id": tool_request_id
            }
        
    except Exception as e:
        logger.error(f"Error in whatsapp_send_message for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to send WhatsApp message: {str(e)}",
            "success": False
        }

# Define the Screen Agent tools for Claude
screen_agent_tools = [
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
    },
    {
        "type": "custom",
        "name": "whatsapp_select_chat",
        "description": "Select a specific chat in WhatsApp by contact or group name. Use this when the user wants to open a conversation with a specific person or group in WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_name": {
                    "type": "string",
                    "description": "The name of the contact or group chat to select in WhatsApp"
                }
            },
            "required": ["chat_name"]
        }
    },
    {
        "type": "custom",
        "name": "whatsapp_send_message",
        "description": "Send a message in the currently open WhatsApp chat. Use this after selecting a chat to send a message to that contact or group.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message text to send in the current WhatsApp chat"
                }
            },
            "required": ["message"]
        }
    }
]