from unittest.mock import patch, MagicMock
from datetime import datetime
import unittest
from anthropic import Anthropic
from constants import CLAUDE_API_KEY
from chat import ChatSession

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
    @patch('chat.get_preference')
    @patch('chat.set_preference')
    def test_haiku_then_asana_workflow(self, mock_set_pref, mock_get_pref, mock_get_tasks, mock_get_workspaces):
        """Test that Claude can switch between non-tool and tool responses appropriately"""
        # Setup mock returns
        mock_workspaces = [
            {'gid': 'workspace1', 'name': 'Personal Projects'},
            {'gid': 'workspace2', 'name': 'Work Tasks'}
        ]
        mock_tasks = [
            {'gid': 'task1', 'name': 'Task 1', 'due_on': '2024-03-15'},
            {'gid': 'task2', 'name': 'Task 2', 'due_on': '2024-03-16'},
            {'gid': 'task3', 'name': 'Task 3', 'due_on': '2024-03-17'}
        ]
        mock_get_workspaces.return_value = mock_workspaces
        mock_get_tasks.return_value = mock_tasks
        mock_get_pref.return_value = None  # No workspace preference set initially
        mock_set_pref.return_value = True  # Successfully set preference
        
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
        mock_get_pref.assert_called()  # Should check for workspace preference
        
        # Set workspace preference
        workspace_pref_response = session.handle_message("work task workspace please")
        
        mock_get_tasks.assert_called_once()
        mock_set_pref.assert_called_once_with('asana_workspace_preference', 'workspace2')  # Should set preference to Work Tasks workspace
        
        # Response should be text about tasks
        self.assertEqual(workspace_pref_response.content[0].type, 'text')
        tasks_text = workspace_pref_response.content[0].text.lower()
        self.assertIn('task', tasks_text)

if __name__ == '__main__':
    unittest.main() 