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
                "preferences": json.dumps({}), # Initial empty object for unencrypted, will be set by RPC
                # encrypted_preferences will be set by its RPC too
            }).execute()
            logger.info(f"Inserted base user_preferences row for {user_id}.")

            # 2. Set default unencrypted preferences using RPC
            logger.info(f"Setting default unencrypted preferences for {user_id} via RPC.")
            supabase.rpc('set_entire_unencrypted_preferences', {
                'p_user_id': user_id,
                'p_preferences_payload': DEFAULT_PREFERENCES
            }).execute()
            logger.info(f"Successfully set default unencrypted preferences for {user_id}.")

            # 3. Set initial empty encrypted preferences using RPC
            logger.info(f"Setting initial empty encrypted preferences for {user_id} via RPC.")
            supabase.rpc('set_entire_encrypted_preferences', {
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
        logger.debug(f"Calling RPC get_preference_value for user {user_id}, key {key}")
        result = supabase.rpc('get_preference_value', {
            'p_user_id': user_id,
            'p_target_key': key
        }).execute()
        # RPC functions in Supabase might return the value directly in result.data
        # If the key doesn't exist or value is null, the SQL function should return NULL, which might be None in Python.
        logger.debug(f"RPC get_preference_value result for user {user_id}, key {key}: {result.data}")
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