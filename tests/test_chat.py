from unittest.mock import MagicMock
from chat import ChatSession, execute_tool
import unittest
from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from unittest.mock import patch

class TestChat(unittest.TestCase):
    def test_execute_tool(self):
        result = execute_tool('get_asana_workspaces', {})
        assert isinstance(result, list)

    def test_chat_session(self):
        # Create mock response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type='text', text='Here are your workspaces')]
        mock_response.stop_reason = None
        
        # Create mock client
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        
        # Test chat session
        session = ChatSession(mock_client)
        response = session.handle_message("show my workspaces")
        
        # Verify response
        self.assertEqual(response.content[0].type, 'text')
        
        # Verify client was called correctly
        mock_client.messages.create.assert_called_once()

class TestChatIntegration(unittest.TestCase):
    def test_haiku_request(self):
        """Test that asking for a haiku gets a real response from Claude without Asana mentions"""
        # Create a real client (will use actual API key)
        client = Anthropic(api_key=CLAUDE_API_KEY)
        
        # Create chat session
        session = ChatSession(client)
        
        # Ask for a haiku
        response = session.handle_message("Write me a haiku about nature")
        
        # Verify response
        self.assertEqual(response.content[0].type, 'text')
        response_text = response.content[0].text.lower()
        
        # Check that response doesn't mention Asana or tasks
        self.assertNotIn('asana', response_text)
        self.assertNotIn('task', response_text)
        self.assertNotIn('workspace', response_text)

    @patch('chat.get_asana_workspaces')
    @patch('chat.get_asana_tasks')
    def test_haiku_then_asana_workflow(self, mock_get_tasks, mock_get_workspaces):
        """Test that Claude can switch between non-tool and tool responses appropriately"""
        # Setup mock returns
        mock_workspaces = [
            {'gid': 'workspace1', 'name': 'Personal Projects'},
            {'gid': 'workspace2', 'name': 'Work Tasks'}
        ]
        mock_tasks = [
            {'gid': 'task1', 'name': 'Test Task 1', 'due_on': '2024-03-15'},
            {'gid': 'task2', 'name': 'Test Task 2', 'due_on': '2024-03-15'}
        ]
        mock_get_workspaces.return_value = mock_workspaces
        mock_get_tasks.return_value = mock_tasks
        
        # Create real Claude client (only mock Asana)
        client = Anthropic(api_key=CLAUDE_API_KEY)
        session = ChatSession(client)
        
        # First ask for a haiku
        haiku_response = session.handle_message("Write me a haiku about nature")
        
        # Verify haiku response
        self.assertEqual(haiku_response.content[0].type, 'text')
        haiku_text = haiku_response.content[0].text.lower()
        self.assertNotIn('asana', haiku_text)
        self.assertNotIn('task', haiku_text)
        self.assertNotIn('workspace', haiku_text)
        
        # Then ask about Asana tasks
        tasks_response = session.handle_message("What tasks do I have in Asana?")
        
        mock_get_workspaces.assert_called_once()
        mock_get_tasks.assert_called_once()
        
        # Response should be text about tasks
        self.assertEqual(tasks_response.content[0].type, 'text')
        tasks_text = tasks_response.content[0].text.lower()
        self.assertIn('task', tasks_text)

if __name__ == '__main__':
    unittest.main() 