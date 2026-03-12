import os
import logging
from preferences import get_preference

# Configure logging
logger = logging.getLogger(__name__)

def get_app_info(user_id: str = None) -> str:
    """
    Read and return the contents of the ABOUTME.md file to provide information about the WhizVoice app.
    This allows Claude to answer questions about the app's functionality and features.
    """
    try:
        # Get the path to the ABOUTME.md file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        about_me_path = os.path.join(current_dir, "ABOUTME.md")
        
        # Read the file contents
        with open(about_me_path, 'r', encoding='utf-8') as file:
            content = file.read()
        
        logger.info(f"Successfully read ABOUTME.md file for user {user_id}")
        return content
        
    except FileNotFoundError:
        logger.error(f"ABOUTME.md file not found at expected location for user {user_id}")
        return "Error: App information file not found. Please contact support for information about WhizVoice features."
    except Exception as e:
        logger.error(f"Error reading ABOUTME.md file for user {user_id}: {str(e)}")
        return f"Error reading app information: {str(e)}"

def get_user_data(user_id: str) -> str:
    """
    Retrieve and summarize what data we have about the user. This includes their non-encrypted preferences.
    This tool should be used when the user asks what information/data we have about them.

    NOTE: This only returns non-encrypted preferences. Encrypted preferences (like API keys) are not included.
    """
    try:
        logger.info(f"Retrieving user data for user {user_id}")

        # Get all non-encrypted preferences that we track
        # Based on the codebase, these are the current non-encrypted preferences:
        preferences = {}

        # Get each preference if it exists
        asana_workspace = get_preference(user_id, 'asana_workspace_preference')
        if asana_workspace:
            preferences['asana_workspace_preference'] = asana_workspace

        music_app = get_preference(user_id, 'music_app_preference')
        if music_app:
            preferences['music_app_preference'] = music_app

        user_timezone = get_preference(user_id, 'user_timezone')
        if user_timezone:
            preferences['user_timezone'] = user_timezone

        parent_task_pref = get_preference(user_id, 'asana_parent_task_preference')
        if parent_task_pref:
            preferences['asana_parent_task_preference'] = parent_task_pref

        # Format the response
        if not preferences:
            return "We currently have no stored preferences for you."

        response_parts = ["Here's what we know about you:"]

        if 'asana_workspace_preference' in preferences:
            response_parts.append(f"- Preferred Asana workspace: {preferences['asana_workspace_preference']}")

        if 'music_app_preference' in preferences:
            response_parts.append(f"- Preferred music app: {preferences['music_app_preference']}")

        if 'user_timezone' in preferences:
            response_parts.append(f"- Timezone: {preferences['user_timezone']}")

        if 'asana_parent_task_preference' in preferences:
            response_parts.append(f"- Asana parent task preference: {preferences['asana_parent_task_preference']}")

        logger.info(f"Successfully retrieved user data for user {user_id}")
        return "\n".join(response_parts)

    except Exception as e:
        logger.error(f"Error retrieving user data for user {user_id}: {str(e)}", exc_info=True)
        return f"Error retrieving user data: {str(e)}"

# Define the about me tool
about_me_tools = [
    {
        "type": "custom",
        "name": "get_info",
        "description": "Get information about the app or the user's stored data. Use type 'app' when users ask about Whiz Voice features, functionality, or how to use the app. Use type 'user_data' when the user asks what information/data you have stored about them, or wants to see their preferences (timezone, music app, Asana workspace, etc.). NOTE: user_data only returns non-encrypted preferences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["app", "user_data"],
                    "description": "What information to retrieve: 'app' for app features/functionality, 'user_data' for stored user preferences"
                }
            },
            "required": ["type"]
        }
    }
] 