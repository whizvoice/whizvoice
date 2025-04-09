import json
import os

DEFAULT_PREFERENCES = {
    'asana_workspace_preference': None
}

def load_preferences():
    """Load user preferences from JSON file"""
    if not os.path.exists('user_preferences.json'):
        return DEFAULT_PREFERENCES.copy()
    
    try:
        with open('user_preferences.json', 'r') as f:
            prefs = json.load(f)
            # Ensure all default keys exist
            return {**DEFAULT_PREFERENCES, **prefs}
    except Exception as e:
        print(f"Error loading preferences: {e}")
        return DEFAULT_PREFERENCES.copy()

def save_preferences(preferences):
    """Save user preferences to JSON file"""
    try:
        with open('user_preferences.json', 'w') as f:
            json.dump(preferences, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving preferences: {e}")
        return False

def get_preference(key):
    """Get a specific preference value"""
    prefs = load_preferences()
    return prefs.get(key)

def set_preference(key, value):
    """Set a specific preference value"""
    prefs = load_preferences()
    prefs[key] = value
    return save_preferences(prefs) 