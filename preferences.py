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
    """Get an encrypted preference value from the database."""
    result = supabase.table("user_preferences") \
        .select(f"encrypted_preferences->>{key}") \
        .eq("user_id", user_id) \
        .execute()
    if result.data and f"encrypted_preferences->>{key}" in result.data[0]:
        return result.data[0][f"encrypted_preferences->>{key}"]
    return None

def set_encrypted_preference(user_id, key, value):
    """Set an encrypted preference value in the database."""
    # First get current preferences
    result = supabase.table("user_preferences") \
        .select("preferences") \
        .eq("user_id", user_id) \
        .execute()
    
    if result.data:
        current_prefs = result.data[0].get("preferences", {})
    else:
        current_prefs = {}
    
    # Update the specific key
    current_prefs[key] = value
    
    # Call the database function to handle encryption
    supabase.rpc('insert_user_preferences', {
        'p_user_id': user_id,
        'p_preferences': current_prefs,
        'p_encryption_key': PGCRYPTO_KEY
    }).execute()
    
    return True

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
    """Get a specific preference value directly from the database."""
    result = supabase.table("user_preferences") \
        .select(f"preferences->>{key}") \
        .eq("user_id", user_id) \
        .execute()
    if result.data and f"preferences->>{key}" in result.data[0]:
        return result.data[0][f"preferences->>{key}"]
    return None

def set_preference_key(user_id, key, value):
    """Set a specific preference key in the database without loading the whole object."""
    prefs = load_preferences(user_id)
    prefs[key] = value
    return save_preferences(user_id, prefs) 