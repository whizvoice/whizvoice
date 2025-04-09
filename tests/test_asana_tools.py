import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime
from asana_tools import get_asana_workspaces, get_asana_tasks

class TestAsanaTools(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.today = datetime.now().strftime('%Y-%m-%d')
        
        # Mock workspace data
        self.mock_workspaces = [
            {'gid': 'workspace1', 'name': 'Personal Projects'},
            {'gid': 'workspace2', 'name': 'Work Tasks'}
        ]
        
        # Mock task data
        self.mock_tasks = [
            {'gid': 'task1', 'name': 'Test Task 1', 'due_on': self.today},
            {'gid': 'task2', 'name': 'Test Task 2', 'due_on': None},  # Should be filtered out
            {'gid': 'task3', 'name': 'Test Task 3', 'due_on': self.today}
        ]

    @patch('asana.WorkspacesApi')
    @patch('asana.ApiClient')
    def test_get_workspaces(self, mock_client, mock_workspaces_api):
        """Test getting workspaces"""
        # Setup mock
        mock_api = MagicMock()
        mock_workspaces_api.return_value = mock_api
        mock_api.get_workspaces.return_value = self.mock_workspaces
        
        # Call function
        result = get_asana_workspaces()
        
        # Assert
        self.assertEqual(result, self.mock_workspaces)
        mock_api.get_workspaces.assert_called_once_with({})

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    def test_get_tasks_with_workspace(self, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting tasks with specific workspace"""
        # Setup mocks
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = self.mock_tasks
        
        # Call function with specific workspace
        result = get_asana_tasks('workspace1')
        
        # Assert
        expected_tasks = [task for task in self.mock_tasks if task['due_on'] == self.today]
        self.assertEqual(result, expected_tasks)
        
        # Verify correct API calls
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'workspace1',
            'assignee': 'user1',
            'completed_since': 'now',
            'due_on': self.today,
            'due_on.exists': 'true',
            'opt_fields': 'name,due_on,completed,projects.name'
        })

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    def test_get_tasks_default_workspace(self, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting tasks using default (second) workspace"""
        # Setup mocks
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        mock_workspace_api = MagicMock()
        mock_workspaces_api.return_value = mock_workspace_api
        mock_workspace_api.get_workspaces.return_value = self.mock_workspaces
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = self.mock_tasks
        
        # Call function without workspace
        result = get_asana_tasks()
        
        # Assert
        expected_tasks = [task for task in self.mock_tasks if task['due_on'] == self.today]
        self.assertEqual(result, expected_tasks)
        
        # Verify it used the second workspace
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'workspace2',
            'assignee': 'user1',
            'completed_since': 'now',
            'due_on': self.today,
            'due_on.exists': 'true',
            'opt_fields': 'name,due_on,completed,projects.name'
        })

    @patch('asana.WorkspacesApi')
    @patch('asana.ApiClient')
    def test_get_tasks_no_workspaces(self, mock_client, mock_workspaces_api):
        """Test handling no workspaces case"""
        # Setup mock to return empty workspace list
        mock_api = MagicMock()
        mock_workspaces_api.return_value = mock_api
        mock_api.get_workspaces.return_value = []
        
        # Call function
        result = get_asana_tasks()
        
        # Assert
        self.assertEqual(result, "No workspaces found")

if __name__ == '__main__':
    unittest.main() 