from supabase_client import supabase
import os

try:
    from constants import PGCRYPTO_KEY
except ImportError:
    # For testing environments where constants.py might not exist
    PGCRYPTO_KEY = os.getenv("PGCRYPTO_KEY", "")
import logging
import json
import pytz

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Set httpx, httpcore, hpack, and anthropic loggers to INFO to reduce verbosity
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("hpack").setLevel(logging.INFO)
logging.getLogger("anthropic").setLevel(logging.INFO)

DEFAULT_PREFERENCES = {
    'asana_workspace_preference': None
}

CLAUDE_API_KEY_PREF_NAME = "claude_api_key" 

def ensure_user_and_prefs(user_id, email=None):
    logger.info(f"Ensuring user and preferences exist for user_id: {user_id}, email: {email}")
    
    try:
        # Ensure user exists in 'users' table
        logger.debug(f"Checking if user exists in users table for user_id: {user_id}")
        user_data = supabase.table("users").select("user_id").eq("user_id", user_id).execute().data
        logger.debug(f"User query result: {user_data}")
        
        if not user_data:
            logger.info(f"User not found in 'users' table, creating new user with user_id: {user_id}")
            # Assuming email is optional for user creation or handled elsewhere if mandatory
            supabase.table("users").insert({"user_id": user_id, "email": email}).execute()
        else:
            logger.info(f"User {user_id} already exists in 'users' table.")
            
        # Ensure preferences row exists in 'user_preferences' table
        logger.debug(f"Checking if preferences row exists for user_id: {user_id}")
        prefs_row = supabase.table("user_preferences").select("user_id").eq("user_id", user_id).execute().data
        logger.debug(f"Preferences row query result: {prefs_row}")
        
        if not prefs_row:
            logger.info(f"Preferences row not found for {user_id}. Creating default preferences.")
            # 1. Create the base row in user_preferences if it doesn't exist.
            #    This ensures the subsequent RPC calls (which are UPDATEs) will find the row.
            #    The actual preference data will be set by the RPC calls.
            #    An empty preferences JSON is fine here as it will be overwritten.
            supabase.table("user_preferences").insert({
                "user_id": user_id,
                "preferences": {}, # Initial empty object for unencrypted, will be set by RPC
                # encrypted_preferences will be set by its RPC too
            }).execute()
            logger.info(f"Inserted base user_preferences row for {user_id}.")

            # 2. Set default unencrypted preferences using RPC
            logger.info(f"Setting default unencrypted preferences for {user_id} via direct update.")
            supabase.table("user_preferences").update({
                "preferences": DEFAULT_PREFERENCES
            }).eq("user_id", user_id).execute()
            logger.info(f"Successfully set default unencrypted preferences for {user_id}.")

            # 3. Set initial empty encrypted preferences using RPC
            logger.info(f"Setting initial empty encrypted preferences for {user_id} via RPC.")
            supabase.rpc('set_encrypted_preferences', {
                'p_user_id': user_id,
                'p_preferences_payload': {}, # Empty JSON object for initial encrypted store
                'p_encryption_key': PGCRYPTO_KEY
            }).execute()
            logger.info(f"Successfully set initial empty encrypted preferences for {user_id}.")
        else:
            logger.info(f"Preferences row already exists for {user_id}.")
            
        logger.info(f"Successfully ensured user and preferences for user_id: {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error in ensure_user_and_prefs: {str(e)}", exc_info=True)
        raise

def get_preference(user_id, key):
    """Get a specific unencrypted preference value using RPC."""
    try:
        result = supabase.rpc('get_preference_value', {
            'p_user_id': user_id,
            'p_target_key': key
        }).execute()
        # RPC functions in Supabase might return the value directly in result.data
        # If the key doesn't exist or value is null, the SQL function should return NULL, which might be None in Python.
        return result.data # Assuming direct data is the value or None
    except Exception as e:
        logger.error(f"Error calling RPC get_preference_value for user {user_id}, key {key}: {str(e)}", exc_info=True)
        # Depending on desired behavior, you might return None or re-raise
        return DEFAULT_PREFERENCES.get(key) # Fallback to default from DEFAULT_PREFERENCES dict if defined

def set_preference(user_id, key, value):
    """Set a specific unencrypted preference value using RPC."""
    try:
        logger.debug(f"Calling RPC set_preference_value for user {user_id}, key {key}, value {value}")
        supabase.rpc('set_preference_value', {
            'p_user_id': user_id,
            'p_target_key': key,
            'p_value': str(value) # Ensure value is passed as text, as per RPC definition
        }).execute()
        logger.info(f"Successfully called RPC set_preference_value for user {user_id}, key {key}.")
        return True
    except Exception as e:
        logger.error(f"Error calling RPC set_preference_value for user {user_id}, key {key}: {str(e)}", exc_info=True)
        return False

def get_decrypted_preference_key(user_id, key):
    """Get a specific preference value by decrypting from encrypted_preferences via RPC."""
    try:
        # Assuming you have a SQL RPC function named 'get_decrypted_preference_key'
        # that takes p_user_id, p_encryption_key, and p_target_key
        logger.debug(f"Calling RPC get_decrypted_preference_key for user {user_id}, key {key}")
        result = supabase.rpc('get_decrypted_preference_key', {
            'p_user_id': user_id,
            'p_encryption_key': PGCRYPTO_KEY,
            'p_target_key': key
        }).execute()
        logger.debug(f"RPC get_decrypted_preference_key result for user {user_id}, key {key}: {result.data}")
        return result.data # Assuming the RPC returns the value directly or None
    except Exception as e:
        logger.error(f"Exception in get_decrypted_preference_key for user {user_id}, key {key}: {str(e)}", exc_info=True)
        return None


def set_encrypted_preference_key(user_id, key, value):
    """Sets a specific key-value pair in the ENCRYPTED preferences store using RPC.
       This function ONLY updates the encrypted_preferences column.
    """
    try:
        logger.debug(f"Calling RPC set_encrypted_preference_key for user {user_id}, key '{key}'")
        supabase.rpc('set_encrypted_preference_key', {
            'p_user_id': user_id,
            'p_target_key': key,
            'p_value': str(value),  # Ensure value is passed as text for JSONB compatibility in SQL
            'p_encryption_key': PGCRYPTO_KEY
        }).execute()
        logger.info(f"Successfully called RPC set_encrypted_preference_key for user {user_id}, key '{key}'.")
        return True
    except Exception as e:
        logger.error(f"Exception in set_encrypted_preference_key for user {user_id}, key '{key}': {str(e)}", exc_info=True)
        return False

def clear_and_verify_encrypted_token(user_id, key_name):
    """Atomically clears an encrypted preference token and verifies it was cleared.
       Uses the new RPC function to ensure read-after-write consistency.
       Returns a dict with success status and verification results.
    """
    try:
        logger.debug(f"Calling RPC clear_and_verify_encrypted_token for user {user_id}, key '{key_name}'")
        result = supabase.rpc('clear_and_verify_encrypted_token', {
            'p_user_id': user_id,
            'p_key_name': key_name,
            'p_encryption_key': PGCRYPTO_KEY
        }).execute()
        
        # The RPC returns the result directly in data, which should be a dict
        if result and hasattr(result, 'data'):
            result_data = result.data
            # Check if data is a list with one element (Supabase sometimes returns list)
            if isinstance(result_data, list) and len(result_data) > 0:
                result_data = result_data[0]
            
            logger.info(f"RPC clear_and_verify_encrypted_token result: {result_data}")
            
            # The RPC returns success=true when it works
            if isinstance(result_data, dict) and result_data.get('success'):
                return result_data
            else:
                # If the result indicates failure, return it as-is
                return result_data if isinstance(result_data, dict) else {
                    'success': False,
                    'token_cleared': False,
                    'message': f'Unexpected result format: {result_data}'
                }
        else:
            logger.warning(f"RPC clear_and_verify_encrypted_token returned no data for user {user_id}, key '{key_name}'")
            return {
                'success': False,
                'token_cleared': False,
                'message': 'No data returned from RPC'
            }
    except Exception as e:
        # Don't treat successful responses as exceptions
        error_str = str(e)
        # Check if this is actually a successful response being misinterpreted
        if "{'success': True" in error_str:
            # Extract the dict from the error message
            import ast
            try:
                # Find the dict part of the error message
                dict_start = error_str.find("{'success':")
                if dict_start != -1:
                    dict_str = error_str[dict_start:]
                    # Find the end of the dict
                    dict_end = dict_str.find('}') + 1
                    dict_str = dict_str[:dict_end]
                    result_dict = ast.literal_eval(dict_str)
                    logger.info(f"Extracted successful result from exception: {result_dict}")
                    return result_dict
            except:
                pass
        
        logger.error(f"Exception in clear_and_verify_encrypted_token for user {user_id}, key '{key_name}': {error_str}")
        return {
            'success': False,
            'token_cleared': False,
            'message': f'Error: {error_str}'
        }

def get_user_timezone(user_id: str) -> tuple[bool, pytz.timezone]:
    """Get the user's timezone from preferences. Returns a tuple of (success, timezone).
    If no timezone is set or invalid, returns (False, Pacific Time)."""
    timezone_str = get_preference(user_id, 'user_timezone')
    if not timezone_str:
        return False, "ERROR: no timezone set. Please ask user for their preferred timezone and set it using the set_user_timezone tool."
    else:
        try:
            return True, pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            logger.warning(f"Unknown timezone '{timezone_str}' for user {user_id}, falling back to Pacific Time")
            return False, "ERROR: unknown timezone. Please ask user for their preferred timezone and set it using the set_user_timezone tool."

def set_user_timezone(user_id: str, timezone_str: str) -> tuple[bool, str]:
    """Set the user's timezone in preferences. Returns a tuple of (success, message).
    Validates the timezone string before setting it. If the value is already set, does nothing."""
    try:
        # Validate the timezone string
        pytz.timezone(timezone_str)  # This will raise UnknownTimeZoneError if invalid

        # Check if the value is already set
        current = get_preference(user_id, 'user_timezone')
        if current == timezone_str:
            return True, "Timezone already set to this value. No update needed."

        # If we get here, the timezone is valid and needs updating
        if set_preference(user_id, 'user_timezone', timezone_str):
            return True, "Successfully set user timezone."
        else:
            return False, "Failed to save timezone preference."
    except pytz.exceptions.UnknownTimeZoneError:
        logger.warning(f"Invalid timezone string '{timezone_str}' provided for user {user_id}")
        return False, f"Invalid timezone: '{timezone_str}'. Please provide a valid IANA timezone name (e.g., 'America/Los_Angeles', 'Europe/London')."
    except Exception as e:
        logger.error(f"Error setting user timezone for user {user_id}: {str(e)}", exc_info=True)
        return False, f"Error setting user timezone: {str(e)}"
    
preferences_tools = [
    {
        "type": "custom",
        "name": "set_user_timezone",
        "description": "Set the user's timezone in preferences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "The IANA timezone name to set for the user."
                }
            },
            "required": ["timezone"]
        }
    },
    {
        "type": "custom",
        "name": "get_user_timezone",
        "description": "Get the user's timezone from preferences.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]