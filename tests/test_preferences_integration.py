import unittest
import os
import uuid
from preferences import (
    ensure_user_and_prefs, 
    get_preference, 
    set_preference,
    get_decrypted_preference_key,
    set_encrypted_preference_key,
    set_user_timezone,
    get_user_timezone
)
from supabase_client import supabase

class TestPreferencesIntegration(unittest.TestCase):
    """Integration tests for preferences system using real database operations."""
    
    def setUp(self):
        # Use consistent test user that we already fixed
        self.test_user_id = "test_user_123"
        self.test_email = f"{self.test_user_id}@test.example.com"
        
        # Use hardcoded test keys to avoid database pollution
        self.test_key = 'test_integration_key'
        self.secret_key = 'test_secret_key'
        self.special_key = 'test_special_chars'
        self.empty_key = 'test_empty_key'
        self.json_key = 'test_json_like'
        
    def tearDown(self):
        # No cleanup needed - we're just updating preferences on existing user
        pass
    
    def test_new_user_initialization(self):
        """Test that users have properly initialized preferences (JSONB, not string)."""
        # Ensure user and preferences exist
        result = ensure_user_and_prefs(self.test_user_id, self.test_email)
        self.assertTrue(result, "ensure_user_and_prefs should return True")
        
        # Verify user exists
        user_data = supabase.table("users").select("user_id").eq("user_id", self.test_user_id).execute().data
        self.assertEqual(len(user_data), 1, "User should exist after initialization")
        
        # Verify preferences row exists
        prefs_data = supabase.table("user_preferences").select("user_id, preferences, encrypted_preferences").eq("user_id", self.test_user_id).execute().data
        self.assertEqual(len(prefs_data), 1, "User preferences should exist after initialization")
        
        # Verify preferences is proper JSONB (dict), not string
        preferences = prefs_data[0]['preferences']
        self.assertIsInstance(preferences, dict, "Preferences should be a dict (JSONB), not a string")
        
        # Verify encrypted_preferences exists
        encrypted_prefs = prefs_data[0]['encrypted_preferences']
        self.assertIsNotNone(encrypted_prefs, "Encrypted preferences should be initialized")
    
    def test_regular_preference_operations(self):
        """Test setting and getting regular (unencrypted) preferences."""
        # Initialize user
        ensure_user_and_prefs(self.test_user_id, self.test_email)
        
        # Test setting a regular preference
        success = set_preference(self.test_user_id, self.test_key, 'test_value')
        self.assertTrue(success, "Setting preference should succeed")
        
        # Test getting the preference back
        value = get_preference(self.test_user_id, self.test_key)
        self.assertEqual(value, 'test_value', "Retrieved preference should match set value")
        
        # Test updating existing preference
        success = set_preference(self.test_user_id, self.test_key, 'updated_value')
        self.assertTrue(success, "Updating preference should succeed")
        
        value = get_preference(self.test_user_id, self.test_key)
        self.assertEqual(value, 'updated_value', "Updated preference should match new value")
        
        # Test getting non-existent preference
        value = get_preference(self.test_user_id, 'nonexistent_key')
        self.assertIsNone(value, "Non-existent preference should return None")
    
    def test_timezone_preferences(self):
        """Test timezone-specific preference operations."""
        # Initialize user
        ensure_user_and_prefs(self.test_user_id, self.test_email)
        
        # Test setting valid timezone
        success, message = set_user_timezone(self.test_user_id, 'America/New_York')
        self.assertTrue(success, f"Setting valid timezone should succeed: {message}")
        
        # Test getting timezone back
        success, timezone = get_user_timezone(self.test_user_id)
        self.assertTrue(success, "Getting timezone should succeed")
        self.assertEqual(str(timezone), 'America/New_York', "Retrieved timezone should match")
        
        # Test setting different valid timezone
        success, message = set_user_timezone(self.test_user_id, 'Europe/London')
        self.assertTrue(success, f"Setting different timezone should succeed: {message}")
        
        success, timezone = get_user_timezone(self.test_user_id)
        self.assertTrue(success, "Getting updated timezone should succeed")
        self.assertEqual(str(timezone), 'Europe/London', "Retrieved timezone should match updated value")
        
        # Test setting invalid timezone
        success, message = set_user_timezone(self.test_user_id, 'Invalid/Timezone')
        self.assertFalse(success, "Setting invalid timezone should fail")
        self.assertIn('Invalid timezone', message, "Error message should mention invalid timezone")
        
        # Test setting same timezone again (should be no-op)
        success, message = set_user_timezone(self.test_user_id, 'Europe/London')
        self.assertTrue(success, "Setting same timezone should succeed")
        self.assertIn('already set', message, "Message should indicate timezone already set")
    
    def test_encrypted_preference_operations(self):
        """Test setting and getting encrypted preferences."""
        # Initialize user
        ensure_user_and_prefs(self.test_user_id, self.test_email)
        
        # Test setting encrypted preference
        success = set_encrypted_preference_key(self.test_user_id, self.secret_key, 'secret_value')
        self.assertTrue(success, "Setting encrypted preference should succeed")
        
        # Test getting encrypted preference back
        value = get_decrypted_preference_key(self.test_user_id, self.secret_key)
        self.assertEqual(value, 'secret_value', "Retrieved encrypted preference should match set value")
        
        # Test updating encrypted preference
        success = set_encrypted_preference_key(self.test_user_id, self.secret_key, 'updated_secret')
        self.assertTrue(success, "Updating encrypted preference should succeed")
        
        value = get_decrypted_preference_key(self.test_user_id, self.secret_key)
        self.assertEqual(value, 'updated_secret', "Updated encrypted preference should match new value")
        
        # Test getting non-existent encrypted preference
        value = get_decrypted_preference_key(self.test_user_id, 'nonexistent_secret')
        self.assertIsNone(value, "Non-existent encrypted preference should return None")
    
    def test_mixed_preference_operations(self):
        """Test that regular and encrypted preferences don't interfere with each other."""
        # Initialize user
        ensure_user_and_prefs(self.test_user_id, self.test_email)
        
        # Set both regular and encrypted preferences
        set_preference(self.test_user_id, 'test_regular_key', 'regular_value')
        set_encrypted_preference_key(self.test_user_id, 'test_encrypted_key', 'encrypted_value')
        
        # Verify both can be retrieved correctly
        retrieved_regular = get_preference(self.test_user_id, 'test_regular_key')
        retrieved_encrypted = get_decrypted_preference_key(self.test_user_id, 'test_encrypted_key')
        
        self.assertEqual(retrieved_regular, 'regular_value', "Regular preference should be unaffected")
        self.assertEqual(retrieved_encrypted, 'encrypted_value', "Encrypted preference should be unaffected")
        
        # Verify cross-contamination doesn't occur
        regular_as_encrypted = get_decrypted_preference_key(self.test_user_id, 'test_regular_key')
        encrypted_as_regular = get_preference(self.test_user_id, 'test_encrypted_key')
        
        self.assertIsNone(regular_as_encrypted, "Regular key shouldn't exist in encrypted store")
        self.assertIsNone(encrypted_as_regular, "Encrypted key shouldn't exist in regular store")
        
    def test_concurrent_operations(self):
        """Test that multiple operations on the same user work correctly."""
        # Initialize user
        ensure_user_and_prefs(self.test_user_id, self.test_email)
        
        # Set multiple preferences in sequence
        test_data = {
            'test_key1': 'value1',
            'test_key2': 'value2',
            'test_key3': 'value3'
        }
        
        for key, value in test_data.items():
            success = set_preference(self.test_user_id, key, value)
            self.assertTrue(success, f"Setting {key} should succeed")
        
        # Verify all preferences are set correctly
        for key, expected_value in test_data.items():
            actual_value = get_preference(self.test_user_id, key)
            self.assertEqual(actual_value, expected_value, f"Value for {key} should match")

if __name__ == '__main__':
    # Set up test environment
    print("Running preferences integration tests...")
    print("Note: These tests use the real database with test_user_123.")
    
    unittest.main(verbosity=2) 