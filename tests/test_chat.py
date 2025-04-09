from unittest.mock import MagicMock
from chat import ChatSession, execute_tool
import unittest

class TestChat(unittest.TestCase):
    def test_execute_tool(self):
        result = execute_tool('get_asana_workspaces', {})
        assert isinstance(result, list)

    def test_chat_session(self):
        # Create mock response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type='text', text='Here are your workspaces')]
        mock_response.stop_reason = None
        
        # Create mock client with beta messages
        mock_client = MagicMock()
        mock_client.beta.messages.create.return_value = mock_response
        
        # Test chat session
        session = ChatSession(mock_client)
        response = session.handle_message("show my workspaces")
        
        # Verify response
        self.assertEqual(response.content[0].type, 'text')
        
        # Verify client was called correctly
        mock_client.beta.messages.create.assert_called_once()

if __name__ == '__main__':
    unittest.main() 