import unittest
import os
from preferences import load_preferences, save_preferences, get_preference, set_preference

class TestPreferences(unittest.TestCase):
    def setUp(self):
        # Ensure clean state
        if os.path.exists('user_preferences.json'):
            os.remove('user_preferences.json')
    
    def tearDown(self):
        # Clean up
        if os.path.exists('user_preferences.json'):
            os.remove('user_preferences.json')
    
    def test_default_preferences(self):
        """Test loading default preferences"""
        prefs = load_preferences()
        self.assertIsNone(prefs['asana_workspace_preference'])
    
    def test_save_and_load_preferences(self):
        """Test saving and loading preferences"""
        test_workspace = "workspace123"
        set_preference('asana_workspace_preference', test_workspace)
        
        # Load and verify
        loaded_workspace = get_preference('asana_workspace_preference')
        self.assertEqual(loaded_workspace, test_workspace)
    
    def test_persistence(self):
        """Test preferences persist to file"""
        test_workspace = "workspace123"
        set_preference('asana_workspace_preference', test_workspace)
        
        # Load fresh from file
        prefs = load_preferences()
        self.assertEqual(prefs['asana_workspace_preference'], test_workspace)

if __name__ == '__main__':
    unittest.main() 