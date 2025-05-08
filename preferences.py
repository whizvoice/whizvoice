from supabase_client import supabase
import os
from constants import PGCRYPTO_KEY

DEFAULT_PREFERENCES = {
    'asana_workspace_preference': None
}


def ensure_user_and_prefs(user_id, email=None):
    # Ensure user exists
    user = supabase.table("users").select("*").eq("user_id", user_id).execute().data
    if not user:
        supabase.table("users").insert({"user_id": user_id, "email": email}).execute()
    # Ensure preferences row exists
    prefs = supabase.table("user_preferences").select("*").eq("user_id", user_id).execute().data
    if not prefs:
        # Insert empty preferences and encrypted_preferences
        # Use a raw SQL query for pgp_sym_encrypt
        from supabase import create_client
        supabase.sql(f"""
            INSERT INTO user_preferences (user_id, preferences, encrypted_preferences)
            VALUES (
                '{user_id}',
                '{{}}',
                pgp_sym_encrypt('{{}}', '{PGCRYPTO_KEY}')
            )
        """).execute()

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