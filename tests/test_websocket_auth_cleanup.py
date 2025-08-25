import unittest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
import json
import asyncio
from jose import jwt, JWTError
import time

class TestWebSocketAuthCleanup(unittest.IsolatedAsyncioTestCase):
    """Test WebSocket authentication failure cleanup"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_websocket = AsyncMock()
        self.mock_websocket.accept = AsyncMock()
        self.mock_websocket.send_text = AsyncMock()
        self.mock_websocket.close = AsyncMock()
        self.mock_websocket.receive_text = AsyncMock()
        self.mock_websocket.headers = {}
        self.mock_websocket.query_params = {}
        
    @patch('app.remove_session_timestamp')
    @patch('app.delete_chat_messages')
    @patch('app.unsubscribe_from_conversation')
    @patch('app.clear_session_mappings')
    @patch('app.remove_user_session')
    @patch('app.redis_managers')
    @patch('app.jwt.decode')
    @patch('app.update_session_activity_redis')
    @patch('app.load_conversation_history')
    @patch('app.set_chat_messages')
    async def test_jwt_auth_failure_cleanup(
        self,
        mock_set_chat_messages,
        mock_load_history,
        mock_update_activity,
        mock_jwt_decode,
        mock_redis_managers,
        mock_remove_user_session,
        mock_clear_mappings,
        mock_unsubscribe,
        mock_delete_messages,
        mock_remove_timestamp
    ):
        """Test that resources are cleaned up when JWT authentication fails"""
        
        # Setup: Make JWT validation fail after some resources are allocated
        mock_jwt_decode.side_effect = JWTError("Invalid token")
        mock_load_history.return_value = []
        mock_redis_managers.__getitem__.return_value.unregister_conversation_websocket = AsyncMock()
        
        # Set up websocket with a token
        self.mock_websocket.headers = {"authorization": "Bearer invalid_token"}
        
        # Import the websocket_endpoint function
        from app import websocket_endpoint
        
        # Call the endpoint
        with patch('app.chat_sessions_lock', new_callable=asyncio.Lock):
            with patch('app.chat_sessions', {}):
                with patch('app.MAX_TOTAL_SESSIONS', 100):
                    await websocket_endpoint(self.mock_websocket)
        
        # Verify WebSocket was accepted then closed with proper error
        self.mock_websocket.accept.assert_called_once()
        
        # Verify error message was sent
        error_calls = self.mock_websocket.send_text.call_args_list
        self.assertEqual(len(error_calls), 1)
        error_msg = json.loads(error_calls[0][0][0])
        self.assertEqual(error_msg["code"], "AUTH_JWT_INVALID")
        
        # Verify WebSocket was closed with correct code
        self.mock_websocket.close.assert_called_once()
        close_call = self.mock_websocket.close.call_args
        self.assertEqual(close_call[1]["code"], 1008)  # Policy Violation
        
        # Verify NO cleanup was called since resources_allocated should be False
        # (JWT fails before resources are allocated)
        mock_remove_timestamp.assert_not_called()
        mock_delete_messages.assert_not_called()
        mock_unsubscribe.assert_not_called()
        mock_clear_mappings.assert_not_called()
        mock_remove_user_session.assert_not_called()
    
    @patch('app.remove_session_timestamp')
    @patch('app.delete_chat_messages')
    @patch('app.unsubscribe_from_conversation')
    @patch('app.clear_session_mappings')
    @patch('app.remove_user_session')
    @patch('app.redis_managers')
    @patch('app.jwt.decode')
    @patch('app.update_session_activity_redis')
    @patch('app.load_conversation_history')
    @patch('app.set_chat_messages')
    @patch('app.supabase')
    async def test_exception_during_resource_allocation_cleanup(
        self,
        mock_supabase,
        mock_set_chat_messages,
        mock_load_history,
        mock_update_activity,
        mock_jwt_decode,
        mock_redis_managers,
        mock_remove_user_session,
        mock_clear_mappings,
        mock_unsubscribe,
        mock_delete_messages,
        mock_remove_timestamp
    ):
        """Test cleanup when exception occurs after resources are allocated"""
        
        # Setup: JWT succeeds but loading history fails after session is tracked
        mock_jwt_decode.return_value = {
            "sub": "test_user_123",
            "email": "test@example.com",
            "name": "Test User"
        }
        
        # Make load_conversation_history fail AFTER session activity is updated
        mock_update_activity.return_value = None  # Success
        mock_load_history.side_effect = Exception("Database connection failed")
        
        mock_redis_managers.__getitem__.return_value.unregister_conversation_websocket = AsyncMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        # Set up websocket with a valid token
        self.mock_websocket.headers = {"authorization": "Bearer valid_token"}
        
        # Import the websocket_endpoint function
        from app import websocket_endpoint
        
        # Call the endpoint
        with patch('app.chat_sessions_lock', new_callable=asyncio.Lock):
            with patch('app.chat_sessions', {}):
                with patch('app.MAX_TOTAL_SESSIONS', 100):
                    await websocket_endpoint(self.mock_websocket)
        
        # Verify WebSocket was accepted then closed with error
        self.mock_websocket.accept.assert_called_once()
        
        # Verify error message was sent
        error_calls = self.mock_websocket.send_text.call_args_list
        self.assertEqual(len(error_calls), 1)
        error_msg = json.loads(error_calls[0][0][0])
        self.assertEqual(error_msg["code"], "AUTH_GENERAL_ERROR")
        
        # Verify WebSocket was closed
        self.mock_websocket.close.assert_called_once()
        
        # Verify cleanup WAS called since resources_allocated should be True
        # (Session activity was updated before the failure)
        mock_remove_timestamp.assert_called_once()
        mock_delete_messages.assert_called_once()
        mock_unsubscribe.assert_called_once()
        mock_clear_mappings.assert_called_once()
        mock_remove_user_session.assert_called_once()
        
        # Verify the session_id used for cleanup matches what was created
        session_id_arg = mock_remove_timestamp.call_args[0][0]
        self.assertIn("ws_test_user_123", session_id_arg)
    
    @patch('app.chat_sessions_lock', new_callable=asyncio.Lock)
    @patch('app.chat_sessions', {})
    @patch('app.MAX_TOTAL_SESSIONS', 0)  # Set to 0 to trigger capacity error
    @patch('app.jwt.decode')
    async def test_service_at_capacity_no_cleanup_needed(
        self,
        mock_jwt_decode
    ):
        """Test that no cleanup is needed when service is at capacity"""
        
        # Setup: JWT succeeds but service is at capacity
        mock_jwt_decode.return_value = {
            "sub": "test_user_123",
            "email": "test@example.com",
            "name": "Test User"
        }
        
        # Set up websocket with a valid token
        self.mock_websocket.headers = {"authorization": "Bearer valid_token"}
        
        # Import the websocket_endpoint function
        from app import websocket_endpoint
        
        # Call the endpoint
        await websocket_endpoint(self.mock_websocket)
        
        # Verify WebSocket was accepted then closed with capacity error
        self.mock_websocket.accept.assert_called_once()
        
        # Verify error message was sent
        error_calls = self.mock_websocket.send_text.call_args_list
        self.assertEqual(len(error_calls), 1)
        error_msg = json.loads(error_calls[0][0][0])
        self.assertEqual(error_msg["code"], "SERVICE_AT_CAPACITY")
        
        # Verify WebSocket was closed with correct code
        self.mock_websocket.close.assert_called_once()
        close_call = self.mock_websocket.close.call_args
        self.assertEqual(close_call[1]["code"], 1013)  # Try Again Later
        
        # No cleanup functions should be called since no resources were allocated

    async def test_no_token_provided_cleanup(self):
        """Test cleanup when no authentication token is provided"""
        
        # Set up websocket with no token
        self.mock_websocket.headers = {}
        self.mock_websocket.query_params = {}
        
        # Import the websocket_endpoint function
        from app import websocket_endpoint
        
        # Call the endpoint
        await websocket_endpoint(self.mock_websocket)
        
        # Verify WebSocket was accepted then immediately closed
        self.mock_websocket.accept.assert_called_once()
        
        # Verify error message was sent
        error_calls = self.mock_websocket.send_text.call_args_list
        self.assertEqual(len(error_calls), 1)
        self.assertIn("Authentication required", error_calls[0][0][0])
        
        # Verify WebSocket was closed with correct code
        self.mock_websocket.close.assert_called_once()
        close_call = self.mock_websocket.close.call_args
        self.assertEqual(close_call[1]["code"], 1008)  # Policy Violation
        
        # No cleanup needed since no resources were allocated


if __name__ == '__main__':
    unittest.main()