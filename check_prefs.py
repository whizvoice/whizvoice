import os
import json
from supabase import create_client, Client
from constants import SUPABASE_URL, SUPABASE_SERVICE_ROLE, PGCRYPTO_KEY

# --- Configuration ---
USER_ID_TO_CHECK = "106733543872425807168"

# --- Supabase Client Initialization ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)
    print("Successfully connected to Supabase.")
except Exception as e:
    print(f"Error connecting to Supabase: {e}")
    exit()

# --- Function to Get and Decrypt Preferences ---
def get_and_decrypt_preferences(user_id: str, encryption_key: str):
    try:
        # Call the Supabase RPC function get_decrypted_preferences
        # Make sure the RPC function name matches exactly what's in your Supabase SQL editor
        response = supabase.rpc('get_decrypted_preferences', {
            'p_user_id': user_id,
            'p_encryption_key': encryption_key
        }).execute()

        print(f"\nRPC Response for user {user_id}:")
        print(f"  Status: {response.status_code}") # Expected: 200 for success
        print(f"  Data: {response.data}")
        # print(f"  Error: {response.error}") # Check if there's an RPC error message

        if response.data:
            decrypted_prefs_json_string = response.data
            if decrypted_prefs_json_string:
                print("\nSuccessfully decrypted preferences.")
                try:
                    decrypted_prefs_object = json.loads(decrypted_prefs_json_string)
                    print("Decrypted preferences object:")
                    print(json.dumps(decrypted_prefs_object, indent=4))

                    # Specifically check for claude_api_key
                    claude_key = decrypted_prefs_object.get('claude_api_key')
                    if claude_key:
                        print(f"\nFound 'claude_api_key': {claude_key}")
                    else:
                        print("\n'claude_api_key' NOT FOUND in decrypted preferences.")
                    
                    asana_token = decrypted_prefs_object.get('asana_access_token')
                    if asana_token:
                        print(f"Found 'asana_access_token': {asana_token}")
                    else:
                        print("'asana_access_token' NOT FOUND in decrypted preferences.")

                except json.JSONDecodeError as e:
                    print(f"Error decoding decrypted JSON string: {e}")
                    print("The decrypted data might not be valid JSON.")
                except Exception as e:
                    print(f"An error occurred while processing decrypted preferences: {e}")
            else:
                print("\nDecryption returned no data (NULL). This could mean:")
                print("  - The user has no encrypted_preferences entry.")
                print("  - Decryption failed (e.g., wrong key, corrupted data).")
        else:
            print("\nRPC call did not return any data.")
            if response.error:
                 print(f"RPC Error Details: {response.error}")


    except Exception as e:
        print(f"An error occurred during the RPC call: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    print(f"Attempting to fetch and decrypt preferences for user: {USER_ID_TO_CHECK}")
    print(f"Using PGCRYPTO_KEY: {PGCRYPTO_KEY[:5]}...{PGCRYPTO_KEY[-5:]}") # Print partial key for confirmation
    get_and_decrypt_preferences(USER_ID_TO_CHECK, PGCRYPTO_KEY) 