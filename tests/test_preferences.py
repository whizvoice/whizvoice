import unittest
import os
from unittest.mock import patch, MagicMock
from preferences import get_preference, set_preference

class TestPreferences(unittest.TestCase):
    def setUp(self):
        # Use a test user ID for all tests
        self.test_user_id = "test_user_123"
    
    @patch('preferences.supabase')
    def test_default_preferences(self, mock_supabase):
        """Test getting default preference value when not set"""
        # Mock RPC to return None for unset preference
        mock_result = MagicMock()
        mock_result.data = None
        mock_supabase.rpc.return_value.execute.return_value = mock_result
        
        # For a key that doesn't exist, it should return None or the default
        pref_value = get_preference(self.test_user_id, 'asana_workspace_preference')
        # The function might return None for unset preferences
        self.assertIsNone(pref_value)
        
        # Verify RPC was called correctly
        mock_supabase.rpc.assert_called_once_with('get_preference_value', {
            'p_user_id': self.test_user_id,
            'p_target_key': 'asana_workspace_preference'
        })
    
    @patch('preferences.supabase')
    def test_save_and_load_preferences(self, mock_supabase):
        """Test setting and getting preferences"""
        test_workspace = "workspace123"
        
        # Mock successful set operation
        mock_supabase.rpc.return_value.execute.return_value = MagicMock()
        
        # Set preference
        result = set_preference(self.test_user_id, 'asana_workspace_preference', test_workspace)
        self.assertTrue(result, "Setting preference should return True on success")
        
        # Verify RPC was called for setting
        mock_supabase.rpc.assert_called_with('set_preference_value', {
            'p_user_id': self.test_user_id,
            'p_target_key': 'asana_workspace_preference',
            'p_value': test_workspace
        })
        
        # Reset mock and set up for get operation
        mock_supabase.reset_mock()
        mock_result = MagicMock()
        mock_result.data = test_workspace
        mock_supabase.rpc.return_value.execute.return_value = mock_result
        
        # Get preference and verify
        loaded_workspace = get_preference(self.test_user_id, 'asana_workspace_preference')
        self.assertEqual(loaded_workspace, test_workspace)
        
        # Verify RPC was called for getting
        mock_supabase.rpc.assert_called_with('get_preference_value', {
            'p_user_id': self.test_user_id,
            'p_target_key': 'asana_workspace_preference'
        })
    
    @patch('preferences.supabase')
    def test_preference_persistence(self, mock_supabase):
        """Test that preferences persist between get/set calls"""
        test_workspace = "workspace456"
        
        # Mock successful set operation
        mock_supabase.rpc.return_value.execute.return_value = MagicMock()
        
        # Set preference
        set_result = set_preference(self.test_user_id, 'asana_workspace_preference', test_workspace)
        self.assertTrue(set_result)
        
        # Reset mock and set up for get operations
        mock_supabase.reset_mock()
        mock_result = MagicMock()
        mock_result.data = test_workspace
        mock_supabase.rpc.return_value.execute.return_value = mock_result
        
        # Get preference multiple times to ensure persistence
        first_read = get_preference(self.test_user_id, 'asana_workspace_preference')
        second_read = get_preference(self.test_user_id, 'asana_workspace_preference')
        
        self.assertEqual(first_read, test_workspace)
        self.assertEqual(second_read, test_workspace)
        self.assertEqual(first_read, second_read)
        
        # Verify RPC was called twice for getting
        self.assertEqual(mock_supabase.rpc.call_count, 2)

if __name__ == '__main__':
    unittest.main() 