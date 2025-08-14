import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import get_current_claude_api_key, get_anthropic_client, _anthropic_clients_cache

class TestAppHelpers(unittest.TestCase):
    def setUp(self):
        self.test_user_id = "test_user_123"
        self.test_api_key = "sk-ant-api03-test-key-123"
        # Clear the cache before each test
        _anthropic_clients_cache.clear()

    def tearDown(self):
        # Clear the cache after each test
        _anthropic_clients_cache.clear()

    @patch('app.get_decrypted_preference_key')
    def test_get_current_claude_api_key_success(self, mock_get_key):
        """Test successful retrieval of Claude API key"""
        mock_get_key.return_value = self.test_api_key
        
        result = get_current_claude_api_key(self.test_user_id)
        
        self.assertEqual(result, self.test_api_key)
        mock_get_key.assert_called_once_with(self.test_user_id, 'claude_api_key')

    @patch('app.get_decrypted_preference_key')
    def test_get_current_claude_api_key_not_found(self, mock_get_key):
        """Test when Claude API key is not found"""
        mock_get_key.return_value = None
        
        result = get_current_claude_api_key(self.test_user_id)
        
        self.assertIsNone(result)
        mock_get_key.assert_called_once_with(self.test_user_id, 'claude_api_key')

    def test_get_current_claude_api_key_no_user_id(self):
        """Test when no user_id is provided"""
        result = get_current_claude_api_key(None)
        
        self.assertIsNone(result)

    @patch('app.get_decrypted_preference_key')
    def test_get_current_claude_api_key_exception(self, mock_get_key):
        """Test exception handling in get_current_claude_api_key"""
        mock_get_key.side_effect = Exception("Database error")
        
        result = get_current_claude_api_key(self.test_user_id)
        
        self.assertIsNone(result)

    @patch('app.AsyncAnthropic')
    @patch('app.get_current_claude_api_key')
    def test_get_anthropic_client_success(self, mock_get_key, mock_anthropic):
        """Test successful creation of Anthropic client"""
        mock_get_key.return_value = self.test_api_key
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        
        result = get_anthropic_client(self.test_user_id)
        
        self.assertEqual(result, mock_client)
        mock_get_key.assert_called_once_with(self.test_user_id)
        mock_anthropic.assert_called_once_with(api_key=self.test_api_key)
        
        # Verify client is cached
        self.assertIn(self.test_api_key, _anthropic_clients_cache)
        self.assertEqual(_anthropic_clients_cache[self.test_api_key], mock_client)

    @patch('app.get_current_claude_api_key')
    def test_get_anthropic_client_no_api_key(self, mock_get_key):
        """Test when no API key is available"""
        mock_get_key.return_value = None
        
        result = get_anthropic_client(self.test_user_id)
        
        self.assertIsNone(result)
        mock_get_key.assert_called_once_with(self.test_user_id)

    @patch('app.AsyncAnthropic')
    @patch('app.get_current_claude_api_key')
    def test_get_anthropic_client_cache_hit(self, mock_get_key, mock_anthropic):
        """Test that cached client is returned on subsequent calls"""
        mock_get_key.return_value = self.test_api_key
        mock_client = MagicMock()
        
        # Manually add to cache
        _anthropic_clients_cache[self.test_api_key] = mock_client
        
        result = get_anthropic_client(self.test_user_id)
        
        self.assertEqual(result, mock_client)
        mock_get_key.assert_called_once_with(self.test_user_id)
        # Anthropic should not be called since we have cached client
        mock_anthropic.assert_not_called()

    def test_get_anthropic_client_no_user_id(self):
        """Test when no user_id is provided"""
        result = get_anthropic_client(None)
        
        self.assertIsNone(result)

    @patch('app.AsyncAnthropic')
    @patch('app.get_current_claude_api_key')
    def test_get_anthropic_client_multiple_users(self, mock_get_key, mock_anthropic):
        """Test caching works correctly for multiple users with different API keys"""
        user1_id = "user1"
        user2_id = "user2"
        api_key1 = "sk-ant-key1"
        api_key2 = "sk-ant-key2"
        
        mock_client1 = MagicMock()
        mock_client2 = MagicMock()
        
        # Mock different API keys for different users
        def side_effect(user_id):
            if user_id == user1_id:
                return api_key1
            elif user_id == user2_id:
                return api_key2
            return None
        
        mock_get_key.side_effect = side_effect
        
        # Mock different clients for different API keys
        def anthropic_side_effect(api_key):
            if api_key == api_key1:
                return mock_client1
            elif api_key == api_key2:
                return mock_client2
            return None
        
        mock_anthropic.side_effect = anthropic_side_effect
        
        # Get client for user1
        result1 = get_anthropic_client(user1_id)
        self.assertEqual(result1, mock_client1)
        
        # Get client for user2
        result2 = get_anthropic_client(user2_id)
        self.assertEqual(result2, mock_client2)
        
        # Verify both are cached
        self.assertIn(api_key1, _anthropic_clients_cache)
        self.assertIn(api_key2, _anthropic_clients_cache)
        self.assertEqual(_anthropic_clients_cache[api_key1], mock_client1)
        self.assertEqual(_anthropic_clients_cache[api_key2], mock_client2)
        
        # Get client for user1 again (should use cache)
        mock_anthropic.reset_mock()
        result1_cached = get_anthropic_client(user1_id)
        self.assertEqual(result1_cached, mock_client1)
        mock_anthropic.assert_not_called()  # Should not create new client

    @patch('app.AsyncAnthropic')
    @patch('app.get_current_claude_api_key')
    def test_get_anthropic_client_same_key_different_users(self, mock_get_key, mock_anthropic):
        """Test that same API key shared by different users uses same cached client"""
        user1_id = "user1"
        user2_id = "user2"
        shared_api_key = "sk-ant-shared-key"
        
        mock_get_key.return_value = shared_api_key
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        
        # Get client for user1
        result1 = get_anthropic_client(user1_id)
        self.assertEqual(result1, mock_client)
        
        # Get client for user2 (should use cached client)
        mock_anthropic.reset_mock()
        result2 = get_anthropic_client(user2_id)
        self.assertEqual(result2, mock_client)
        
        # Should not create new client since key is cached
        mock_anthropic.assert_not_called()
        
        # Verify same client is returned
        self.assertEqual(result1, result2)

if __name__ == '__main__':
    unittest.main() 