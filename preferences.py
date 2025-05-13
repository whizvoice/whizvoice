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

def get_encrypted_preference(user_id, key):
    """Get a specific decrypted preference value using the RPC function."""
    try:
        rpc_params = {
            'p_user_id': user_id,
            'p_encryption_key': PGCRYPTO_KEY,
            'p_target_key': key
        }
        result = supabase.rpc('get_decrypted_preference_key', rpc_params).execute()
        
        # The RPC returns the value directly (or null)
        if result.data:
            # Check if data is not explicitly None, handle empty strings if needed
            return result.data
        else:
            # Handles cases where user/key not found, or null value stored
            return None
    except Exception as e:
        logger.error(f"Error calling get_decrypted_preference_key RPC for user {user_id}, key {key}: {e}", exc_info=True)
        return None

def set_encrypted_preference(user_id, key, value):
    """Set an encrypted preference value using RPC for reading and writing."""
    current_prefs = {}
    try:
        # Step 1: Read and decrypt current preferences using RPC
        read_rpc_params = {
            'p_user_id': user_id,
            'p_encryption_key': PGCRYPTO_KEY
        }
        result = supabase.rpc('get_decrypted_preferences', read_rpc_params).execute()

        if result.data:
            decrypted_json_string = result.data
            if decrypted_json_string and isinstance(decrypted_json_string, str):
                try:
                    current_prefs = json.loads(decrypted_json_string)
                    if not isinstance(current_prefs, dict):
                        logger.warning(f"Decrypted preferences for {user_id} is not a dict ({type(current_prefs)}). Resetting.")
                        current_prefs = {}
                except json.JSONDecodeError:
                    logger.error(f"Could not decode JSON from get_decrypted_preferences for {user_id}. Resetting.")
                    current_prefs = {}
            # If result.data is not a non-empty string, current_prefs remains {}
        # If no data returned by RPC, current_prefs remains {}

    except Exception as e:
        logger.error(f"Error calling get_decrypted_preferences RPC for {user_id}: {e}. Starting with empty prefs.", exc_info=True)
        current_prefs = {} # Start fresh if read/decrypt fails

    # Step 2: Update the specific key in the Python dictionary
    current_prefs[key] = value
    
    # Step 3: Call the insert/update RPC to encrypt and save the modified dictionary
    try:
        write_rpc_params = {
            'p_user_id': user_id,
            'p_preferences': current_prefs, # Pass the dictionary directly
            'p_encryption_key': PGCRYPTO_KEY
        }
        supabase.rpc('insert_user_preferences', write_rpc_params).execute()
        return True
    except Exception as e:
        logger.error(f"Error calling insert_user_preferences RPC for user {user_id}: {e}", exc_info=True)
        return False

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
            logger.info(f"Preferences not found, creating new preferences for user_id: {user_id}")
            # Insert new preferences with empty preferences object
            prefs_result = supabase.rpc('insert_user_preferences', {
                'p_user_id': user_id,
                'p_preferences': {},
                'p_encryption_key': PGCRYPTO_KEY
            }).execute()
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
    """Get a specific preference value directly from the unencrypted column."""
    result = supabase.table("user_preferences") \
        .select(f"preferences->>{key}") \
        .eq("user_id", user_id) \
        .execute()
    if result.data and f"preferences->>{key}" in result.data[0] and result.data[0][f"preferences->>{key}"] is not None:
        return result.data[0][f"preferences->>{key}"]
    return None

def set_preference_key(user_id, key, value):
    """Set a specific preference key in the unencrypted column."""
    # This implementation was incorrect, needs proper JSONB update
    # Recommended: Use set_preference for simplicity if performance isn't critical
    # For direct update: Use Supabase jsonb update operators if needed
    # Keeping set_preference call for now:
    return set_preference(user_id, key, value) 