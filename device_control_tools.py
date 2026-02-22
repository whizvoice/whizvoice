"""
Device Control Tools - Direct Android intents and system APIs for common device actions.

These tools use direct Android intents/APIs (alarms, calendar, flashlight, phone, volume)
rather than UI automation via the accessibility service. They are faster and more reliable.
"""

import logging
import json
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


async def _send_device_tool(tool_name: str, params: dict, user_id: str = None,
                            websocket=None, tool_result_handler=None,
                            conversation_id: str = None, timeout: float = 5.0) -> dict:
    """
    Generic helper: send a tool_execution message to the Android device via WebSocket
    and wait for the result.
    """
    tool_request_id = f"tool_{uuid.uuid4().hex[:8]}"

    logger.info(f"Device control tool '{tool_name}' (user: {user_id}, request: {tool_request_id})")

    if not websocket:
        logger.error(f"No WebSocket connection available for {tool_name}")
        return {"error": "No connection to device available", "success": False}

    tool_execution_message = {
        "type": "tool_execution",
        "tool": tool_name,
        "request_id": tool_request_id,
        "params": params,
        "conversation_id": conversation_id
    }

    try:
        await websocket.send_text(json.dumps(tool_execution_message))
        logger.info(f"Successfully sent {tool_name} command")
    except Exception as e:
        logger.error(f"Failed to send WebSocket message for {tool_name}: {e}")
        return {"status": "error", "error": f"Failed to send command to device: {e}", "success": False}

    if tool_result_handler:
        try:
            result = await tool_result_handler.wait_for_tool_result(
                request_id=tool_request_id, timeout=timeout
            )
            logger.info(f"Device control result for {tool_request_id}: {result}")
            return result
        except Exception as e:
            logger.error(f"Error waiting for {tool_name} result: {e}")
            return {"status": "error", "error": f"Error waiting for device response: {e}", "success": False}
    else:
        return {"status": "sent", "message": f"Command sent to device", "request_id": tool_request_id}


# ========== Alarm / Timer tools ==========

async def agent_set_alarm(hour: int, minute: int, label: str = None,
                          user_id: str = None, websocket=None,
                          tool_result_handler=None, conversation_id: str = None) -> dict:
    """Set an alarm on the user's device."""
    params = {"hour": hour, "minute": minute}
    if label:
        params["label"] = label
    return await _send_device_tool(
        "agent_set_alarm", params, user_id, websocket, tool_result_handler, conversation_id
    )


async def agent_set_timer(seconds: int, label: str = None,
                          user_id: str = None, websocket=None,
                          tool_result_handler=None, conversation_id: str = None) -> dict:
    """Set a countdown timer on the user's device."""
    params = {"seconds": seconds}
    if label:
        params["label"] = label
    return await _send_device_tool(
        "agent_set_timer", params, user_id, websocket, tool_result_handler, conversation_id
    )


async def agent_dismiss_alarm(user_id: str = None, websocket=None,
                              tool_result_handler=None, conversation_id: str = None) -> dict:
    """Dismiss the currently ringing alarm."""
    return await _send_device_tool(
        "agent_dismiss_alarm", {}, user_id, websocket, tool_result_handler, conversation_id
    )


async def agent_get_next_alarm(user_id: str = None, websocket=None,
                               tool_result_handler=None, conversation_id: str = None) -> dict:
    """Get the time of the next scheduled alarm."""
    return await _send_device_tool(
        "agent_get_next_alarm", {}, user_id, websocket, tool_result_handler, conversation_id
    )


# ========== Flashlight ==========

async def agent_toggle_flashlight(turn_on: bool, user_id: str = None, websocket=None,
                                  tool_result_handler=None, conversation_id: str = None) -> dict:
    """Toggle the device flashlight on or off."""
    return await _send_device_tool(
        "agent_toggle_flashlight", {"turn_on": turn_on},
        user_id, websocket, tool_result_handler, conversation_id
    )


# ========== Calendar ==========

async def agent_add_calendar_event(title: str, begin_time: str, end_time: str = None,
                                   description: str = None, location: str = None,
                                   all_day: bool = False,
                                   user_id: str = None, websocket=None,
                                   tool_result_handler=None, conversation_id: str = None) -> dict:
    """Add a calendar event (opens calendar app pre-filled)."""
    params = {"title": title, "begin_time": begin_time, "all_day": all_day}
    if end_time:
        params["end_time"] = end_time
    if description:
        params["description"] = description
    if location:
        params["location"] = location
    return await _send_device_tool(
        "agent_add_calendar_event", params, user_id, websocket, tool_result_handler, conversation_id
    )


# ========== Phone ==========

async def agent_dial_phone_number(phone_number: str, user_id: str = None, websocket=None,
                                  tool_result_handler=None, conversation_id: str = None) -> dict:
    """Open the phone dialer with a number pre-filled (user must tap call)."""
    return await _send_device_tool(
        "agent_dial_phone_number", {"phone_number": phone_number},
        user_id, websocket, tool_result_handler, conversation_id
    )


async def agent_press_call_button(expected_number: str = None, user_id: str = None,
                                  websocket=None, tool_result_handler=None,
                                  conversation_id: str = None) -> dict:
    """Press the call button in the dialer via accessibility service."""
    params = {}
    if expected_number:
        params["expected_number"] = expected_number
    return await _send_device_tool(
        "agent_press_call_button", params,
        user_id, websocket, tool_result_handler, conversation_id
    )


# ========== Volume ==========

async def agent_set_volume(volume_level: int, stream: str = "music",
                           user_id: str = None, websocket=None,
                           tool_result_handler=None, conversation_id: str = None) -> dict:
    """Set the device volume for a given stream."""
    return await _send_device_tool(
        "agent_set_volume", {"volume_level": volume_level, "stream": stream},
        user_id, websocket, tool_result_handler, conversation_id
    )


# ========== Contacts Lookup ==========

async def agent_lookup_phone_contacts(name: str, user_id: str = None, websocket=None,
                                       tool_result_handler=None, conversation_id: str = None) -> dict:
    """Search the device's native phone contacts by name."""
    return await _send_device_tool(
        "agent_lookup_phone_contacts", {"name": name},
        user_id, websocket, tool_result_handler, conversation_id
    )


# ========== Tool definitions for Claude ==========

device_control_tools = [
    {
        "type": "custom",
        "name": "agent_set_alarm",
        "description": "Set an alarm on the user's Android device. The alarm will be created silently without opening the Clock app. Use this when the user asks to set an alarm, wake-up alarm, etc. Use the get_current_datetime tool to get the current date time, in order to calculate the date time the alarm needs to be set for.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hour": {
                    "type": "integer",
                    "description": "The hour of the alarm in 24-hour format (0-23). For example, 7 for 7:00 AM, 14 for 2:00 PM."
                },
                "minute": {
                    "type": "integer",
                    "description": "The minute of the alarm (0-59)."
                },
                "label": {
                    "type": "string",
                    "description": "Optional label/name for the alarm (e.g., 'Wake up', 'Meeting')."
                }
            },
            "required": ["hour", "minute"]
        }
    },
    {
        "type": "custom",
        "name": "agent_set_timer",
        "description": "Set a countdown timer on the user's Android device. The timer starts immediately without opening the Clock app. Use this when the user asks to set a timer, countdown, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "The total duration of the timer in seconds. For example, 300 for 5 minutes, 3600 for 1 hour."
                },
                "label": {
                    "type": "string",
                    "description": "Optional label/name for the timer (e.g., 'Cooking', 'Break')."
                }
            },
            "required": ["seconds"]
        }
    },
    {
        "type": "custom",
        "name": "agent_dismiss_alarm",
        "description": "Dismiss the currently ringing alarm on the user's Android device. Use this when the user asks to stop, dismiss, or turn off a ringing alarm.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_get_next_alarm",
        "description": "Get the time of the next scheduled alarm on the user's Android device. Note: Android only provides the next alarm time, not a full list of all alarms. Use this when the user asks what alarm they have set or when their next alarm is.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_toggle_flashlight",
        "description": "Turn the device flashlight (torch) on or off. Use this when the user asks to turn on/off the flashlight or torch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "turn_on": {
                    "type": "boolean",
                    "description": "true to turn the flashlight on, false to turn it off."
                }
            },
            "required": ["turn_on"]
        }
    },
    {
        "type": "custom",
        "name": "agent_add_calendar_event",
        "description": "Add an event to the user's calendar. This opens the calendar app pre-filled with the event details. Use this when the user asks to add a meeting, event, appointment, or reminder to their calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title/name of the calendar event."
                },
                "begin_time": {
                    "type": "string",
                    "description": "The start time in ISO 8601 format (e.g., '2025-01-15T14:00:00'). Use the user's local timezone."
                },
                "end_time": {
                    "type": "string",
                    "description": "The end time in ISO 8601 format. If not provided, defaults to 1 hour after begin_time."
                },
                "description": {
                    "type": "string",
                    "description": "Optional description/notes for the event."
                },
                "location": {
                    "type": "string",
                    "description": "Optional location for the event."
                },
                "all_day": {
                    "type": "boolean",
                    "description": "Whether this is an all-day event. Defaults to false."
                }
            },
            "required": ["title", "begin_time"]
        }
    },
    {
        "type": "custom",
        "name": "agent_dial_phone_number",
        "description": "Open the phone dialer with a number pre-filled. If the number is for a saved contact, you can go ahead and call the agent_press_call_button tool after to make the call. Otherwise, confirm the number with the user before calling agent_press_call_button to make the call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": "The phone number to dial (e.g., '+1234567890', '555-1234')."
                }
            },
            "required": ["phone_number"]
        }
    },
    {
        "type": "custom",
        "name": "agent_press_call_button",
        "description": "Press the call button in the phone dialer to place a call. The dialer must already be open with a number entered (use agent_dial_phone_number first). If the number is for a saved contact, go ahead and press the call button without asking for confirmation. Only ask for verbal confirmation if the number is NOT from a saved contact. Speakerphone is automatically enabled by default after the call is placed. After this tool succeeds, call agent_close_app right away to dismiss Whiz so the user can take the phone call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expected_number": {
                    "type": "string",
                    "description": "Optional safety check: the phone number you expect to be dialed. If provided, the tool verifies the displayed number matches before pressing call."
                },
                "speakerphone": {
                    "type": "boolean",
                    "description": "Don't specify this parameter unless user specifically requested a speakerphone state."
                }
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "agent_set_volume",
        "description": "Set the device volume level. Use this when the user asks to change the volume, turn volume up/down, set volume to a specific level, or mute/unmute.",
        "input_schema": {
            "type": "object",
            "properties": {
                "volume_level": {
                    "type": "integer",
                    "description": "The volume level to set (0 to 25, where 0 is mute). The tool response will include the actual max_volume for the device."
                },
                "stream": {
                    "type": "string",
                    "description": "The audio stream to adjust. Options: 'music' (media/music volume), 'ring' (ringtone volume), 'notification' (notification volume), 'alarm' (alarm volume). Defaults to 'music'.",
                    "enum": ["music", "ring", "notification", "alarm"]
                }
            },
            "required": ["volume_level"]
        }
    },
    {
        "type": "custom",
        "name": "agent_lookup_phone_contacts",
        "description": "Unless user specifies phone contacts or Android contacts, ALWAYS use get_contact_preference and NOT THIS TOOL since get_contact_preference will fallback to phone contacts. Otherwise, use this tool to search the device's native phone contacts by name. Returns matching contacts with their phone numbers, email addresses, and postal addresses, each labeled by type (mobile, work, home, personal, etc.). If the device hasn't granted contacts permission, returns an empty list — ask the user for the info directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name to search for in the device's phone contacts"
                }
            },
            "required": ["name"]
        }
    }
]
