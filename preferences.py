from supabase_client import supabase
import os
from constants import PGCRYPTO_KEY
import logging
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Set httpx, httpcore, and hpack loggers to INFO to reduce verbosity
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("hpack").setLevel(logging.INFO)

DEFAULT_PREFERENCES = {
    'asana_workspace_preference': None
}

CLAUDE_API_KEY_PREF_NAME = "claude_api_key" 

def ensure_user_and_prefs(user_id, email=None):
    logger.info(f"Ensuring user and preferences exist for user_id: {user_id}, email: {email}")
    
    try:
        # Ensure user exists
        logger.debug(f"Checking if user exists in users table for user_id: {user_id}")
        user = supabase.table("users").select("*").eq("user_id", user_id).execute().data
        logger.debug(f"User query result: {user}")
        
        if not user:
            logger.info(f"User not found, creating new user with user_id: {user_id}")
            user_result = supabase.table("users").insert({"user_id": user_id, "email": email}).execute()
            logger.debug(f"User creation result: {user_result.data}")
        else:
            logger.info(f"User already exists: {user}")
            
        # Ensure preferences row exists
        logger.debug(f"Checking if preferences exist for user_id: {user_id}")
        prefs = supabase.table("user_preferences").select("*").eq("user_id", user_id).execute().data
        logger.debug(f"Preferences query result: {prefs}")
        
        if not prefs:
            logger.info(f"Preferences not found, creating new preferences for user_id: {user_id} using DEFAULT_PREFERENCES.")
            # Insert new preferences, ensuring to pass all required parameters for the RPC
            prefs_result = supabase.rpc('insert_user_preferences', {
                'p_user_id': user_id,
                'p_preferences': DEFAULT_PREFERENCES, # Pass default preferences JSON
                'p_encryption_key': PGCRYPTO_KEY     # Pass the encryption key
            }).execute()
            # It's good practice to check prefs_result for errors if the library provides a way
            # For example: if hasattr(prefs_result, 'error') and prefs_result.error:
            #    logger.error(f"RPC Error creating preferences for {user_id}: {prefs_result.error}")
            #    raise Exception(f"RPC Error creating preferences: {prefs_result.error}")
        else:
            logger.info(f"Preferences already exist: {prefs}")
            
        logger.info(f"Successfully ensured user and preferences for user_id: {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error in ensure_user_and_prefs: {str(e)}", exc_info=True)
        raise

def load_preferences(user_id):
    res = supabase.table("user_preferences").select("preferences").eq("user_id", user_id).execute()
    if res.data and res.data[0]["preferences"]:
        return {**DEFAULT_PREFERENCES, **res.data[0]["preferences"]}
    return DEFAULT_PREFERENCES.copy()

def save_preferences(user_id, preferences):
    supabase.table("user_preferences").update({"preferences": preferences}).eq("user_id", user_id).execute()
    return True

def get_preference(user_id, key):
    prefs = load_preferences(user_id)
    return prefs.get(key)

def set_preference(user_id, key, value):
    prefs = load_preferences(user_id)
    prefs[key] = value
    return save_preferences(user_id, prefs)

def get_preference_key(user_id, key):
    """Get a specific preference value directly from the UNENCRYPTED 'preferences' column."""
    result = supabase.table("user_preferences") \
        .select(f"preferences->>{key}") \
        .eq("user_id", user_id) \
        .execute()
    # Check if data is not None, is a list, is not empty, and the key exists
    if result.data and isinstance(result.data, list) and len(result.data) > 0 and f"preferences->>{key}" in result.data[0]:
        return result.data[0][f"preferences->>{key}"]
    elif result.data and isinstance(result.data, list) and len(result.data) > 0 and result.data[0].get(f"preferences->>{key}") is None:
        logger.debug(f"Key '{key}' found in unencrypted preferences for user {user_id}, but its value is NULL.")
        return None
    else:
        logger.debug(f"Key '{key}' not found or unexpected data structure in unencrypted preferences for user {user_id}. Data: {result.data}")
        return None

def get_decrypted_preference_key(user_id, key):
    """Get a specific preference value by decrypting from encrypted_preferences via RPC."""
    try:
        decryption_response = supabase.rpc('get_decrypted_preferences', {
            'p_user_id': user_id,
            'p_encryption_key': PGCRYPTO_KEY
        }).execute()

        if decryption_response.data:
            decrypted_prefs_json_string = decryption_response.data
            if decrypted_prefs_json_string and isinstance(decrypted_prefs_json_string, str):
                try:
                    decrypted_prefs_object = json.loads(decrypted_prefs_json_string)
                    return decrypted_prefs_object.get(key)
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding decrypted JSON for user {user_id} in get_decrypted_preference_key: {e}")
                    return None
            elif decrypted_prefs_json_string:
                 logger.warning(f"get_decrypted_preferences for user {user_id} did not return a string: {type(decrypted_prefs_json_string)}")
                 return None
        # else: (data is None or RPC error)
        #    logger.warning(f"get_decrypted_preferences returned no data or error for user {user_id}. Error: {decryption_response.error}")

    except Exception as e:
        logger.error(f"Exception in get_decrypted_preference_key for user {user_id}, key {key}: {str(e)}")
    
    return None

def set_preference_key(user_id, key, value):
    """Set a specific preference key in the UNENCRYPTED 'preferences' column."""
    prefs = load_preferences(user_id)
    prefs[key] = value
    return save_preferences(user_id, prefs)

def set_encrypted_preference_key(user_id, key, value):
    """Sets a key-value pair, ensuring it's saved to encrypted_preferences via RPC."""
    current_decrypted_prefs = {}
    try:
        # 1. Fetch current decrypted preferences
        decryption_response = supabase.rpc('get_decrypted_preferences', {
            'p_user_id': user_id,
            'p_encryption_key': PGCRYPTO_KEY
        }).execute()

        if decryption_response.data and isinstance(decryption_response.data, str):
            try:
                current_decrypted_prefs = json.loads(decryption_response.data)
                if not isinstance(current_decrypted_prefs, dict): # Ensure it's a dict
                    logger.warning(f"Decrypted preferences for user {user_id} was not a dict, re-initializing. Type: {type(current_decrypted_prefs)}")
                    current_decrypted_prefs = {}
            except json.JSONDecodeError:
                logger.warning(f"Could not parse existing decrypted preferences for user {user_id}. Starting fresh.")
                current_decrypted_prefs = {} # Start with an empty dict if parsing fails
        elif decryption_response.data: # It's not a string, or some other non-empty, non-None type
            logger.warning(f"get_decrypted_preferences for user {user_id} did not return a string or None. Data: {decryption_response.data}. Re-initializing prefs.")
            current_decrypted_prefs = {}
        # If decryption_response.data is None, current_decrypted_prefs remains {}

        # 2. Update the key in the Python dictionary
        current_decrypted_prefs[key] = value

        # 3. Call insert_user_preferences RPC to save and re-encrypt all preferences
        # The p_preferences argument in the SQL function expects JSONB.
        # supabase-py should handle converting the dict to JSON for the RPC call.
        save_response = supabase.rpc('insert_user_preferences', {
            'p_user_id': user_id,
            'p_preferences': current_decrypted_prefs, # Pass the whole updated dict
            'p_encryption_key': PGCRYPTO_KEY
        }).execute()
        
        # Basic check, though RPCs might not have typical data/error attributes like table queries
        # if hasattr(save_response, 'error') and save_response.error:
        #    logger.error(f"Error calling insert_user_preferences for user {user_id}: {save_response.error}")
        #    return False
        # Consider the call successful if no exception was raised by execute()
        logger.info(f"Successfully called insert_user_preferences for user {user_id} to set key '{key}'.")
        return True

    except Exception as e:
        logger.error(f"Exception in set_encrypted_preference_key for user {user_id}, key {key}: {str(e)}", exc_info=True)
        return False 