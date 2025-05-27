import os
import logging

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

# Define the about me tool
about_me_tools = [
    {
        "type": "custom",
        "name": "get_app_info",
        "description": "Get information about the Whiz Voice app, including its features, functionality, and how to use it. Use this tool when users ask questions about what the app can do, how it works, or need general information about Whiz Voice.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
] 