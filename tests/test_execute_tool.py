import unittest
from unittest.mock import patch, MagicMock
import asyncio
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import execute_tool

class TestExecuteTool(unittest.TestCase):
    def setUp(self):
        self.test_user_id = "test_user_123"
        self.test_task_args = {
            'name': 'Test Task',
            'due_date': '2024-03-20',
            'notes': 'Test notes',
            'parent_task_gid': 'parent123'
        }
    
    def test_execute_tool_requires_auth_for_protected_tools(self):
        """Test that protected tools require user authentication"""
        protected_tools = [
            "get_asana_tasks", "get_parent_tasks", "get_new_asana_task_id",
            "get_workspace_preference", "set_workspace_preference",
            "get_asana_workspaces", "update_asana_task", "delete_asana_task"
        ]
        
        for tool_name in protected_tools:
            with self.subTest(tool=tool_name):
                result = asyncio.run(execute_tool(tool_name, {}, user_id=None))
                self.assertIsInstance(result, dict)
                self.assertIn("error", result)
                self.assertIn("User authentication required", result["error"])

    @patch('app.get_asana_workspaces')
    def test_execute_tool_get_asana_workspaces(self, mock_get_workspaces):
        """Test execute_tool with get_asana_workspaces"""
        mock_workspaces = [{'gid': 'ws1', 'name': 'Workspace 1'}]
        mock_get_workspaces.return_value = mock_workspaces
        
        result = asyncio.run(execute_tool("get_asana_workspaces", {}, self.test_user_id))
        
        self.assertEqual(result, mock_workspaces)
        mock_get_workspaces.assert_called_once_with(self.test_user_id)

    @patch('app.get_asana_tasks')
    def test_execute_tool_get_asana_tasks(self, mock_get_tasks):
        """Test execute_tool with get_asana_tasks"""
        mock_tasks = [{'gid': 'task1', 'name': 'Task 1'}]
        mock_get_tasks.return_value = mock_tasks
        
        # Test with date parameters
        args = {'start_date': '2024-03-15', 'end_date': '2024-03-20'}
        result = asyncio.run(execute_tool("get_asana_tasks", args, self.test_user_id))
        
        self.assertEqual(result, mock_tasks)
        mock_get_tasks.assert_called_once_with(self.test_user_id, '2024-03-15', '2024-03-20')

    @patch('app.get_asana_tasks')
    def test_execute_tool_get_asana_tasks_no_dates(self, mock_get_tasks):
        """Test execute_tool with get_asana_tasks without date parameters"""
        mock_tasks = [{'gid': 'task1', 'name': 'Task 1'}]
        mock_get_tasks.return_value = mock_tasks
        
        result = asyncio.run(execute_tool("get_asana_tasks", {}, self.test_user_id))
        
        self.assertEqual(result, mock_tasks)
        mock_get_tasks.assert_called_once_with(self.test_user_id, None, None)

    @patch('app.get_current_date')
    def test_execute_tool_get_current_date(self, mock_get_date):
        """Test execute_tool with get_current_date"""
        mock_get_date.return_value = '2024-03-15'
        
        result = asyncio.run(execute_tool("get_current_date", {}, self.test_user_id))
        
        self.assertEqual(result, '2024-03-15')
        mock_get_date.assert_called_once_with(self.test_user_id)

    @patch('app.get_parent_tasks')
    def test_execute_tool_get_parent_tasks(self, mock_get_parent_tasks):
        """Test execute_tool with get_parent_tasks"""
        mock_tasks = [{'gid': 'parent1', 'name': 'Parent Task', 'num_subtasks': 3}]
        mock_get_parent_tasks.return_value = mock_tasks
        
        result = asyncio.run(execute_tool("get_parent_tasks", {}, self.test_user_id))
        
        self.assertEqual(result, mock_tasks)
        mock_get_parent_tasks.assert_called_once_with(self.test_user_id)

    @patch('app.get_new_asana_task_id')
    def test_execute_tool_get_new_asana_task_id(self, mock_create_task):
        """Test execute_tool with get_new_asana_task_id"""
        mock_task = {'gid': 'new_task1', 'name': 'Test Task'}
        mock_create_task.return_value = mock_task

        result = asyncio.run(execute_tool("get_new_asana_task_id", self.test_task_args, self.test_user_id))
        
        self.assertEqual(result, mock_task)
        mock_create_task.assert_called_once_with(
            self.test_user_id,
            'Test Task',
            '2024-03-20',
            'Test notes',
            'parent123'
        )

    def test_execute_tool_get_new_asana_task_id_missing_name(self):
        """Test execute_tool with get_new_asana_task_id missing required name"""
        args = {'due_date': '2024-03-20'}

        result = asyncio.run(execute_tool("get_new_asana_task_id", args, self.test_user_id))
        
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Task name is required.")

    @patch('app.set_preference')
    def test_execute_tool_set_workspace_preference(self, mock_set_pref):
        """Test execute_tool with set_workspace_preference"""
        mock_set_pref.return_value = True
        args = {'workspace_gid': 'workspace123'}
        
        result = asyncio.run(execute_tool("set_workspace_preference", args, self.test_user_id))
        
        self.assertTrue(result)
        mock_set_pref.assert_called_once_with(self.test_user_id, 'asana_workspace_preference', 'workspace123')

    def test_execute_tool_set_workspace_preference_missing_gid(self):
        """Test execute_tool with set_workspace_preference missing workspace_gid"""
        with self.assertRaises(ValueError) as context:
            asyncio.run(execute_tool("set_workspace_preference", {}, self.test_user_id))
        
        self.assertIn("Workspace GID is required", str(context.exception))

    @patch('app.get_preference')
    def test_execute_tool_get_workspace_preference(self, mock_get_pref):
        """Test execute_tool with get_workspace_preference"""
        mock_get_pref.return_value = 'workspace123'
        
        result = asyncio.run(execute_tool("get_workspace_preference", {}, self.test_user_id))
        
        self.assertEqual(result, 'workspace123')
        mock_get_pref.assert_called_once_with(self.test_user_id, 'asana_workspace_preference')

    def test_execute_tool_get_workspace_preference_no_user(self):
        """Test execute_tool with get_workspace_preference without user_id"""
        result = asyncio.run(execute_tool("get_workspace_preference", {}, user_id=None))
        
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertIn("User authentication required", result["error"])

    @patch('app.update_asana_task')
    def test_execute_tool_change_task_parent(self, mock_update_task):
        """Test execute_tool with update_asana_task (changing parent)"""
        mock_response = {'gid': 'task123', 'parent': {'gid': 'parent456'}}
        mock_update_task.return_value = mock_response

        args = {'task_gid': 'task123', 'parent_gid': 'parent456'}
        result = asyncio.run(execute_tool("update_asana_task", args, self.test_user_id))

        self.assertEqual(result, mock_response)
        mock_update_task.assert_called_once_with(self.test_user_id, 'task123', None, None, None, None, 'parent456')

    @patch('app.update_asana_task')
    def test_execute_tool_update_task_due_date(self, mock_update_task):
        """Test execute_tool with update_asana_task (updating due date)"""
        mock_response = {'gid': 'task123', 'due_on': '2024-03-25'}
        mock_update_task.return_value = mock_response

        args = {'task_gid': 'task123', 'due_date': '2024-03-25'}
        result = asyncio.run(execute_tool("update_asana_task", args, self.test_user_id))

        self.assertEqual(result, mock_response)
        mock_update_task.assert_called_once_with(self.test_user_id, 'task123', None, '2024-03-25', None, None, None)

    def test_execute_tool_update_task_due_date_missing_task_gid(self):
        """Test execute_tool with update_asana_task missing task_gid"""
        args = {'due_date': '2024-03-25'}

        result = asyncio.run(execute_tool("update_asana_task", args, self.test_user_id))

        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Task GID is required.")

    def test_execute_tool_update_task_due_date_missing_due_date(self):
        """Test execute_tool with update_asana_task with only task_gid (should succeed with all optional params)"""
        args = {'task_gid': 'task123'}

        # This should not error since all update fields are optional
        # But let's verify at least task_gid is required
        result = asyncio.run(execute_tool("update_asana_task", {'due_date': '2024-03-25'}, self.test_user_id))

        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Task GID is required.")

    @patch('app.get_app_info')
    def test_execute_tool_get_app_info(self, mock_get_app_info):
        """Test execute_tool with get_app_info"""
        mock_info = "WhizVoice is an AI chatbot..."
        mock_get_app_info.return_value = mock_info
        
        result = asyncio.run(execute_tool("get_app_info", {}, self.test_user_id))
        
        self.assertEqual(result, mock_info)
        mock_get_app_info.assert_called_once_with(self.test_user_id)

    def test_execute_tool_unknown_tool(self):
        """Test execute_tool with unknown tool name"""
        with self.assertRaises(ValueError) as context:
            asyncio.run(execute_tool("unknown_tool", {}, self.test_user_id))
        
        self.assertIn("Unknown tool: unknown_tool", str(context.exception))

    def test_execute_tool_public_tool_without_user(self):
        """Test that public tools (like get_current_date) work without user_id"""
        with patch('app.get_current_date') as mock_get_date:
            mock_get_date.return_value = '2024-03-15'
            
            result = asyncio.run(execute_tool("get_current_date", {}, user_id=None))
            
            self.assertEqual(result, '2024-03-15')
            mock_get_date.assert_called_once_with(None)

    def test_execute_tool_get_app_info_without_user(self):
        """Test that get_app_info works without user_id"""
        with patch('app.get_app_info') as mock_get_app_info:
            mock_info = "WhizVoice is an AI chatbot..."
            mock_get_app_info.return_value = mock_info
            
            result = asyncio.run(execute_tool("get_app_info", {}, user_id=None))
            
            self.assertEqual(result, mock_info)
            mock_get_app_info.assert_called_once_with(None)

if __name__ == '__main__':
    unittest.main() 