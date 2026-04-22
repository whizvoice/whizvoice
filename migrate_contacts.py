"""
One-time migration: convert contacts from nickname-keyed format to UUID-keyed format
with a nickname_index.

Old format:
  {"husband": {"real_name": "Robin Pham", "preferred_app": "whatsapp", ...}}

New format:
  {
    "contacts": {"<uuid>": {"nicknames": ["husband"], "real_name": "Robin Pham", ...}},
    "nickname_index": {"husband": "<uuid>"}
  }
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supabase_client import supabase
from contacts_tools import _migrate_contacts_data


def migrate_all_users():
    # Get all user_preferences rows that have a non-null contacts key
    result = supabase.table("user_preferences").select("user_id, preferences").execute()

    migrated_count = 0
    skipped_count = 0
    error_count = 0

    for row in result.data:
        user_id = row["user_id"]
        preferences = row.get("preferences")

        if not preferences:
            skipped_count += 1
            continue

        # preferences is a JSONB column, so it comes back as a dict
        contacts_raw = preferences.get("contacts")
        if not contacts_raw:
            skipped_count += 1
            continue

        # Parse contacts if stored as a string
        if isinstance(contacts_raw, str):
            try:
                contacts_data = json.loads(contacts_raw)
            except json.JSONDecodeError:
                print(f"  ERROR: Could not parse contacts JSON for user {user_id}")
                error_count += 1
                continue
        else:
            contacts_data = contacts_raw

        # Check if already in new format
        if "contacts" in contacts_data and "nickname_index" in contacts_data:
            print(f"  SKIP: User {user_id} already migrated")
            skipped_count += 1
            continue

        # Migrate
        migrated = _migrate_contacts_data(contacts_data)
        print(f"  MIGRATE: User {user_id} - {len(migrated['contacts'])} contacts, {len(migrated['nickname_index'])} nicknames")

        # Write back using the RPC function (same as set_preference)
        try:
            supabase.rpc('set_preference_value', {
                'p_user_id': user_id,
                'p_target_key': 'contacts',
                'p_value': json.dumps(migrated)
            }).execute()
            migrated_count += 1
        except Exception as e:
            print(f"  ERROR: Failed to save migrated contacts for user {user_id}: {e}")
            error_count += 1

    print(f"\nDone. Migrated: {migrated_count}, Skipped: {skipped_count}, Errors: {error_count}")


if __name__ == "__main__":
    migrate_all_users()
