"""
Messaging Tools - Tools for WhatsApp messaging via Android Accessibility Service
"""

import logging
import json
import uuid
import asyncio
from typing import Optional, Dict, Any

# Configure logging
logger = logging.getLogger(__name__)

async def whatsapp_select_chat(chat_name: str, user_id: str = None, websocket = None,
                               tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Select a specific chat in WhatsApp on the user's Android device.

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

async def whatsapp_draft_message(message: str, user_id: str = None, websocket = None,
                                tool_result_handler = None, conversation_id: str = None, previous_text: str = None) -> dict:
    """
    Draft a message in WhatsApp by showing an overlay for user review.

    This tool shows a WhizVoice overlay with the message text for user confirmation
    before actually sending. Always use this before sending messages.

    If previous_text is provided, the overlay will show tracked changes:
    - Deleted text appears with red strikethrough
    - Added text appears in blue
    - Unchanged text appears in black

    Args:
        message: The message text to draft for user review
        previous_text: Optional. The previous version of the message for track changes display
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the draft operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Drafting WhatsApp message (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for WhatsApp message draft")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        params = {"message": message}
        if previous_text is not None:
            params["previous_text"] = previous_text

        tool_execution_message = {
            "type": "tool_execution",
            "tool": "whatsapp_draft_message",
            "request_id": tool_request_id,
            "params": params,
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending WhatsApp draft message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent whatsapp_draft_message command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for WhatsApp message draft result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=5.0
                )

                logger.info(f"WhatsApp message draft result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for WhatsApp message draft result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to draft message sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in whatsapp_draft_message for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to draft WhatsApp message: {str(e)}",
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


# ========== SMS Functions ==========

async def sms_select_chat(contact_name: str, user_id: str = None, websocket = None,
                          tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Select a specific SMS conversation on the user's Android device.

    Args:
        contact_name: The name of the contact to select
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the conversation selection operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Selecting SMS chat '{contact_name}' (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for SMS chat selection")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "sms_select_chat",
            "request_id": tool_request_id,
            "params": {
                "contact_name": contact_name
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending SMS select chat message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent sms_select_chat command for '{contact_name}'")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for SMS chat selection result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (10 seconds for accessibility operations)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=10.0
                )

                logger.info(f"SMS chat selection result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for SMS chat selection result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": f"Command to select conversation '{contact_name}' sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in sms_select_chat for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to select SMS chat: {str(e)}",
            "success": False
        }


async def sms_draft_message(message: str, user_id: str = None, websocket = None,
                            tool_result_handler = None, conversation_id: str = None,
                            previous_text: str = None) -> dict:
    """
    Draft an SMS message with a visual overlay for user confirmation before sending.

    Args:
        message: The message text to draft
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context
        previous_text: Optional previous version of the message (for tracked changes)

    Returns:
        A dictionary containing the result of the draft operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Drafting SMS message (user: {user_id}, request: {tool_request_id})")
        logger.debug(f"Message content: '{message}', previous_text: '{previous_text}'")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for SMS message draft")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "sms_draft_message",
            "request_id": tool_request_id,
            "params": {
                "message": message
            },
            "conversation_id": conversation_id
        }

        # Add previous_text if provided (for tracked changes)
        if previous_text:
            tool_execution_message["params"]["previous_text"] = previous_text

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending SMS draft message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent sms_draft_message command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for SMS draft message result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (5 seconds for draft operation)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=5.0
                )

                logger.info(f"SMS draft message result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for SMS draft message result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": "SMS draft command sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in sms_draft_message for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to draft SMS message: {str(e)}",
            "success": False
        }


async def sms_send_message(message: str, user_id: str = None, websocket = None,
                           tool_result_handler = None, conversation_id: str = None) -> dict:
    """
    Send an SMS message. Must have already drafted the message for user confirmation.

    Args:
        message: The message text to send
        user_id: The user ID (for logging purposes)
        websocket: The WebSocket connection to send messages through
        tool_result_handler: Handler for tracking pending tool executions
        conversation_id: The conversation ID for context

    Returns:
        A dictionary containing the result of the send operation
    """
    try:
        # Generate a unique request ID for tracking
        tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

        logger.info(f"Sending SMS message (user: {user_id}, request: {tool_request_id})")

        # If no WebSocket provided, return error
        if not websocket:
            logger.error("No WebSocket connection available for SMS message send")
            return {
                "error": "No connection to device available",
                "success": False
            }

        # Create the WebSocket message for the Android app
        tool_execution_message = {
            "type": "tool_execution",
            "tool": "sms_send_message",
            "request_id": tool_request_id,
            "params": {
                "message": message
            },
            "conversation_id": conversation_id
        }

        # Send to Android app via WebSocket
        try:
            message_json = json.dumps(tool_execution_message)
            logger.debug(f"Sending SMS send message to Android: {tool_execution_message}")
            await websocket.send_text(message_json)
            logger.info(f"Successfully sent sms_send_message command")
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {str(e)}")
            return {
                "status": "error",
                "error": f"Failed to send command to device: {str(e)}",
                "success": False
            }

        # If we have a tool_result_handler, wait for the result
        if tool_result_handler:
            logger.info(f"Waiting for SMS send message result from Android device (request_id: {tool_request_id})")

            try:
                # Wait for tool result with timeout (8 seconds for send operation)
                result = await tool_result_handler.wait_for_tool_result(
                    request_id=tool_request_id,
                    timeout=8.0
                )

                logger.info(f"SMS send message result for {tool_request_id}: {result}")
                return result

            except Exception as e:
                logger.error(f"Error waiting for SMS send message result: {str(e)}")
                return {
                    "status": "error",
                    "error": f"Error waiting for device response: {str(e)}",
                    "success": False
                }
        else:
            # If no handler, just return success after sending
            return {
                "status": "sent",
                "message": "SMS send command sent to device",
                "request_id": tool_request_id
            }

    except Exception as e:
        logger.error(f"Error in sms_send_message for user {user_id}: {str(e)}")
        return {
            "error": f"Failed to send SMS message: {str(e)}",
            "success": False
        }


# Define the messaging tools for Claude
messaging_tools = [
    {
        "type": "custom",
        "name": "whatsapp_select_chat",
        "description": "Select a specific chat in WhatsApp by contact or group name. IMPORTANT: WhatsApp must already be open - use launch_app tool first to open WhatsApp if needed. Use this when the user wants to open a conversation with a specific person or group in WhatsApp.",
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
        "name": "whatsapp_draft_message",
        "description": "Draft a message for WhatsApp and show it in an overlay for user review. IMPORTANT: WhatsApp chat must be open first (use launch_app to open WhatsApp, then whatsapp_select_chat to open the chat). Always use this BEFORE sending any WhatsApp message. This allows the user to review and confirm the message text before it's sent. The message will appear in a yellow overlay. You MUST use this method to draft the message before you send the message so that you can confirm with the user before sending. Optional: If you are editing/correcting a previously drafted message, provide the previous_text parameter to show tracked changes (deletions in red strikethrough, additions in blue).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message text to draft for user review before sending"
                },
                "previous_text": {
                    "type": "string",
                    "description": "Optional. The previous version of the message text. When provided, the overlay will show tracked changes (deletions in red strikethrough, additions in blue)"
                }
            },
            "required": ["message"]
        }
    },
    {
        "type": "custom",
        "name": "whatsapp_send_message",
        "description": "Send a message in WhatsApp. IMPORTANT: You MUST have already: 1) Opened WhatsApp (launch_app), 2) Selected a chat (whatsapp_select_chat), 3) Drafted the message (whatsapp_draft_message), 4) Received explicit user confirmation that they are ready to send the message - you can ask for confirmation you don't have it yet. This tool will click the send button in WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The exact message text that was drafted and confirmed by the user"
                }
            },
            "required": ["message"]
        }
    }
]
