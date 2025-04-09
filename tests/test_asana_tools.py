import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from asana_tools import get_asana_workspaces, get_asana_tasks, get_date_range, get_current_date, get_parent_tasks

class TestAsanaTools(unittest.TestCase):
    @patch('asana_tools.datetime')
    def setUp(self, mock_datetime):
        """Set up test fixtures"""
        # Mock datetime.now() to return a fixed date
        self.fixed_date = datetime(2024, 3, 15)
        mock_datetime.now.return_value = self.fixed_date
        self.today = self.fixed_date.strftime('%Y-%m-%d')
        
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
    @patch('asana_tools.datetime')
    def test_get_tasks_with_workspace(self, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting tasks with specific workspace"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
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
            'due_on.after': self.today,
            'due_on.before': self.today,
            'opt_fields': 'name,due_on,completed,projects.name'
        })

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.datetime')
    @patch('asana_tools.get_preference')
    def test_get_tasks_default_workspace(self, mock_get_pref, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting tasks using default (second) workspace"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock preference to return None (no preference set)
        mock_get_pref.return_value = None
        
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
            'due_on.after': self.today,
            'due_on.before': self.today,
            'opt_fields': 'name,due_on,completed,projects.name'
        })
        
        # Verify get_preference was called
        mock_get_pref.assert_called_once_with('asana_workspace_preference')

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

    @patch('asana_tools.datetime')
    def test_get_date_range(self, mock_datetime):
        """Test date range parsing"""
        mock_datetime.now.return_value = self.fixed_date
        today = self.fixed_date.date()
        
        # Test default (no range)
        start, end = get_date_range()
        self.assertEqual(start, today)
        self.assertEqual(end, today)
        
        # Test week range
        start, end = get_date_range('week')
        self.assertEqual(start, today)
        self.assertEqual(end, today + timedelta(days=7))
        
        # Test month range
        start, end = get_date_range('month')
        self.assertEqual(start, today)
        self.assertEqual(end, today + timedelta(days=30))
        
        # Test invalid range defaults to today
        start, end = get_date_range('invalid')
        self.assertEqual(start, today)
        self.assertEqual(end, today)

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    def test_get_tasks_with_date_range(self, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting tasks with date range"""
        # Setup mocks
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        mock_tasks = [
            {'gid': 'task1', 'name': 'Task 1', 'due_on': '2024-03-15'},
            {'gid': 'task2', 'name': 'Task 2', 'due_on': '2024-03-16'},
            {'gid': 'task3', 'name': 'Task 3', 'due_on': '2024-03-17'}
        ]
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = mock_tasks
        
        # Test getting tasks for specific date range
        result = get_asana_tasks('workspace1', start_date='2024-03-15', end_date='2024-03-16')
        
        # Should get first two tasks
        self.assertEqual(len(result), 2)
        
        # Verify correct API parameters
        mock_task_api.get_tasks.assert_called_with({
            'workspace': 'workspace1',
            'assignee': 'user1',
            'completed_since': 'now',
            'due_on.after': '2024-03-15',
            'due_on.before': '2024-03-16',
            'opt_fields': 'name,due_on,completed,projects.name'
        })

    @patch('asana_tools.datetime')
    def test_get_current_date(self, mock_datetime):
        """Test getting current date"""
        mock_datetime.now.return_value = self.fixed_date
        result = get_current_date()
        
        # Should return mocked date in YYYY-MM-DD format
        self.assertEqual(result, self.today)
        
        # Should be in correct format
        try:
            datetime.strptime(result, '%Y-%m-%d')
        except ValueError:
            self.fail("Date not in YYYY-MM-DD format")

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.datetime')
    @patch('asana_tools.get_preference')
    def test_get_parent_tasks(self, mock_get_pref, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting parent tasks (tasks with subtasks)"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock preference to return None (no preference set)
        mock_get_pref.return_value = None
        
        # Create mock tasks with some having subtasks
        mock_tasks = [
            {'gid': 'task1', 'name': 'Parent Task 1', 'num_subtasks': 3, 'completed': False},
            {'gid': 'task2', 'name': 'Regular Task', 'num_subtasks': 0, 'completed': False},
            {'gid': 'task3', 'name': 'Parent Task 2', 'num_subtasks': 2, 'completed': False},
            {'gid': 'task4', 'name': 'Completed Parent', 'num_subtasks': 5, 'completed': True}
        ]
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = mock_tasks
        
        # Call function with specific workspace
        result = get_parent_tasks('workspace1')
        
        # Assert - should only get tasks with subtasks that are not completed
        expected_tasks = [task for task in mock_tasks 
                         if task.get('num_subtasks', 0) > 0 and 
                         not task.get('completed', False)]
        self.assertEqual(result, expected_tasks)
        self.assertEqual(len(result), 2)  # Should only get 2 parent tasks
        
        # Verify correct API calls
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'workspace1',
            'assignee': 'user1',
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name,num_subtasks'
        })
        
        # Verify get_preference was not called since we provided a workspace
        mock_get_pref.assert_not_called()

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.datetime')
    @patch('asana_tools.get_preference')
    def test_get_parent_tasks_with_preference(self, mock_get_pref, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test getting parent tasks with a workspace preference set"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock preference to return a workspace
        mock_get_pref.return_value = 'preferred_workspace'
        
        # Create mock tasks with some having subtasks
        mock_tasks = [
            {'gid': 'task1', 'name': 'Parent Task 1', 'num_subtasks': 3, 'completed': False},
            {'gid': 'task2', 'name': 'Regular Task', 'num_subtasks': 0, 'completed': False},
            {'gid': 'task3', 'name': 'Parent Task 2', 'num_subtasks': 2, 'completed': False},
            {'gid': 'task4', 'name': 'Completed Parent', 'num_subtasks': 5, 'completed': True}
        ]
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = mock_tasks
        
        # Call function without specifying a workspace
        result = get_parent_tasks()
        
        # Assert - should only get tasks with subtasks that are not completed
        expected_tasks = [task for task in mock_tasks 
                         if task.get('num_subtasks', 0) > 0 and 
                         not task.get('completed', False)]
        self.assertEqual(result, expected_tasks)
        self.assertEqual(len(result), 2)  # Should only get 2 parent tasks
        
        # Verify correct API calls
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'preferred_workspace',
            'assignee': 'user1',
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name,num_subtasks'
        })
        
        # Verify get_preference was called
        mock_get_pref.assert_called_once_with('asana_workspace_preference')

if __name__ == '__main__':
    unittest.main() 