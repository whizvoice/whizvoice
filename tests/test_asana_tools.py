import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asana_tools import (
    get_asana_workspaces, get_asana_tasks, get_date_range, get_current_date,
    get_parent_tasks, get_new_asana_task_id, delete_asana_task,
    _asana_client_cache, _user_gid_cache, _workspace_pref_cache
)

class TestAsanaTools(unittest.TestCase):
    def setUp(self):
        self.test_user_id = "test_user_123"
        # Clear caches before each test to avoid cross-test pollution
        _asana_client_cache.clear()
        _user_gid_cache.clear()
        _workspace_pref_cache.clear()
        # Mock datetime to control time-based tests
        self.fixed_date = datetime(2024, 3, 15, 10, 0, 0)
        self.today = '2024-03-15'
        
        # Sample workspaces for testing
        self.mock_workspaces = [
            {'gid': 'workspace1', 'name': 'Personal'},
            {'gid': 'workspace2', 'name': 'Work'}
        ]
        
        # Sample tasks for testing
        self.mock_tasks = [
            {'gid': 'task1', 'name': 'Task 1', 'due_on': '2024-03-15'},
            {'gid': 'task2', 'name': 'Task 2', 'due_on': '2024-03-16'},
            {'gid': 'task3', 'name': 'Task 3', 'due_on': None},  # No due date
            {'gid': 'task4', 'name': 'Task 4', 'due_on': '2024-03-17'}
        ]

    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana.WorkspacesApi')
    @patch('asana.ApiClient')
    def test_get_workspaces(self, mock_client, mock_workspaces_api, mock_get_token):
        """Test getting workspaces"""
        # Mock the access token
        mock_get_token.return_value = "fake_token"
        
        # Setup mock
        mock_api = MagicMock()
        mock_workspaces_api.return_value = mock_api
        mock_api.get_workspaces.return_value = self.mock_workspaces
        
        # Call function
        result = get_asana_workspaces(self.test_user_id)
        
        # Assert
        self.assertEqual(result, self.mock_workspaces)
        mock_get_token.assert_called_once_with(self.test_user_id, 'asana_access_token')
        mock_api.get_workspaces.assert_called_once_with(opts={})

    @patch('asana_tools.get_decrypted_preference_key')
    def test_get_workspaces_no_token(self, mock_get_token):
        """Test getting workspaces when no access token is available"""
        # Mock no token
        mock_get_token.return_value = None

        # Call function and expect ValueError
        with self.assertRaises(ValueError) as context:
            get_asana_workspaces('test_user_no_token_workspaces')

        # Assert error message (from asana_tools.py:27)
        self.assertEqual(str(context.exception), "Asana access token not found. Please go to Settings and add your Asana access token to use Asana features.")

    @patch('asana_tools.get_current_date')
    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.TasksApi')
    def test_get_tasks_with_workspace_preference(self, mock_tasks_api, mock_users_api, mock_get_client, mock_get_token, mock_get_pref, mock_get_date):
        """Test getting tasks with workspace preference set"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = "workspace1"
        mock_get_date.return_value = self.today
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Filter tasks to only those with due dates
        filtered_tasks = [task for task in self.mock_tasks if task['due_on'] is not None]
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = filtered_tasks
        
        # Call function
        result = get_asana_tasks(self.test_user_id)
        
        # Should return filtered tasks for today's date
        expected_tasks = [task for task in filtered_tasks if task['due_on'] == self.today]
        self.assertEqual(result, expected_tasks)
        
        # Verify correct API calls
        mock_get_pref.assert_called_once_with(self.test_user_id, 'asana_workspace_preference')
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'workspace1',
            'assignee': 'user1',
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name'
        })

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    def test_get_tasks_no_workspace_preference(self, mock_get_token, mock_get_pref):
        """Test getting tasks when no workspace preference is set"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = None
        
        # Call function
        result = get_asana_tasks(self.test_user_id)
        
        # Assert
        expected_error = "Error identifying user's preferred workspace to get tasks from. Please set a preferred workspace using the set_workspace_preference tool."
        self.assertEqual(result, expected_error)

    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_preference')
    def test_get_tasks_no_token(self, mock_get_preference, mock_get_token):
        """Test getting tasks when no access token is available"""
        # Mock workspace preference to pass that check
        mock_get_preference.return_value = 'workspace123'
        # Mock no token
        mock_get_token.return_value = None

        # Call function and expect ValueError
        with self.assertRaises(ValueError) as context:
            get_asana_tasks('test_user_no_token')

        # Assert error message (from asana_tools.py:27)
        self.assertEqual(str(context.exception), "Asana access token not found. Please go to Settings and add your Asana access token to use Asana features.")

    def test_get_date_range(self):
        """Test date range parsing"""
        with patch('asana_tools.datetime') as mock_datetime:
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

    @patch('asana_tools.get_user_timezone')
    @patch('asana_tools.datetime')
    @patch('asana_tools.pytz')
    def test_get_current_date(self, mock_pytz, mock_datetime, mock_get_timezone):
        """Test getting current date"""
        # Test with user timezone - success case
        mock_tz = MagicMock()
        mock_get_timezone.return_value = (True, mock_tz)
        mock_now_with_tz = MagicMock()
        mock_now_with_tz.strftime.return_value = '2024-03-15'
        mock_datetime.now.return_value = mock_now_with_tz
        
        result = get_current_date(self.test_user_id)
        self.assertEqual(result, '2024-03-15')
        mock_datetime.now.assert_called_with(mock_tz)
        
        # Test with user timezone - failure case
        mock_datetime.reset_mock()
        mock_get_timezone.return_value = (False, "Error message")
        
        result = get_current_date(self.test_user_id)
        self.assertIn("Error using timezone", result)
        self.assertIn("Error message", result)
        
        # Test without user_id (fallback to PST)
        mock_datetime.reset_mock()
        mock_pst_tz = MagicMock()
        mock_pytz.timezone.return_value = mock_pst_tz
        mock_now_pst = MagicMock()
        mock_now_pst.strftime.return_value = '2024-03-15'
        mock_datetime.now.return_value = mock_now_pst
        
        result = get_current_date()
        mock_pytz.timezone.assert_called_with('America/Los_Angeles')
        mock_datetime.now.assert_called_with(mock_pst_tz)

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.TasksApi')
    def test_get_parent_tasks(self, mock_tasks_api, mock_users_api, mock_get_client, mock_get_token, mock_get_pref):
        """Test getting parent tasks (tasks with subtasks)"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = "workspace1"
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock tasks with subtasks
        mock_tasks_with_subtasks = [
            {'gid': 'task1', 'name': 'Parent Task 1', 'num_subtasks': 2, 'completed': False},
            {'gid': 'task2', 'name': 'Regular Task', 'num_subtasks': 0, 'completed': False},
            {'gid': 'task3', 'name': 'Completed Parent', 'num_subtasks': 1, 'completed': True}
        ]
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = mock_tasks_with_subtasks
        
        # Call function
        result = get_parent_tasks(self.test_user_id)
        
        # Should only return uncompleted tasks with subtasks
        expected_tasks = [mock_tasks_with_subtasks[0]]  # Only task1
        self.assertEqual(result, expected_tasks)
        
        # Verify correct API calls
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'workspace1',
            'assignee': 'user1',
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name,num_subtasks'
        })

    @patch('asana_tools.get_current_date')
    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.TasksApi')
    def test_get_new_asana_task_id(self, mock_tasks_api, mock_users_api, mock_get_client, mock_get_token, mock_get_pref, mock_get_date):
        """Test creating a task in Asana"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = "workspace1"
        mock_get_date.return_value = self.today
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_task.return_value = {'gid': 'new_task1', 'name': 'New Task'}
        
        # Test creating task
        result = get_new_asana_task_id(self.test_user_id, 'New Task', due_date='2024-03-20', notes='Task notes')
        
        # Assert
        self.assertEqual(result['gid'], 'new_task1')
        self.assertEqual(result['name'], 'New Task')
        
        # Verify correct API calls
        mock_task_api.create_task.assert_called_once_with(
            body={'data': {
                'name': 'New Task',
                'workspace': 'workspace1',
                'assignee': 'user1',
                'due_on': '2024-03-20',
                'notes': 'Task notes'
            }},
            opts={'opt_fields': 'gid,name,due_on,completed,projects.name'}
        )

    @patch('asana_tools.get_current_date')
    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.TasksApi')
    def test_create_asana_subtask(self, mock_tasks_api, mock_users_api, mock_get_client, mock_get_token, mock_get_pref, mock_get_date):
        """Test creating a subtask in Asana"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = "workspace1"
        mock_get_date.return_value = self.today
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_subtask_for_task.return_value = {'gid': 'new_subtask1', 'name': 'New Subtask'}
        
        # Test creating subtask
        result = get_new_asana_task_id(self.test_user_id, 'New Subtask', due_date='2024-03-20', notes='Subtask notes', parent_task_gid='parent_task1')
        
        # Assert
        self.assertEqual(result['gid'], 'new_subtask1')
        self.assertEqual(result['name'], 'New Subtask')
        
        # Verify correct API calls
        mock_task_api.create_subtask_for_task.assert_called_once_with(
            body={'data': {
                'name': 'New Subtask',
                'workspace': 'workspace1',
                'assignee': 'user1',
                'due_on': '2024-03-20',
                'notes': 'Subtask notes'
            }},
            task_gid='parent_task1',
            opts={'opt_fields': 'gid,name,due_on,completed,projects.name'}
        )

    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana.TasksApi')
    @patch('asana.ApiClient')
    def test_delete_asana_task(self, mock_client, mock_tasks_api, mock_get_token):
        """Test deleting an Asana task"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"

        # Mock task API
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api

        # Mock delete_task to return None (successful deletion)
        mock_task_api.delete_task.return_value = None

        # Test deleting a task
        result = delete_asana_task(self.test_user_id, 'task123')

        # Assert - The function returns a success message
        self.assertTrue(result['success'])
        self.assertIn('deleted successfully', result['message'])
        self.assertIn('task123', result['message'])

        # Verify correct API calls
        mock_task_api.delete_task.assert_called_once_with(task_gid='task123')

    @patch('asana_tools.get_decrypted_preference_key')
    def test_delete_asana_task_no_token(self, mock_get_token):
        """Test deleting a task when no access token is available"""
        # Mock no token
        mock_get_token.return_value = None

        # Call function and expect ValueError
        with self.assertRaises(ValueError) as context:
            delete_asana_task('test_user_no_token', 'task123')

        # Assert error message (from asana_tools.py:27)
        self.assertEqual(str(context.exception), "Asana access token not found. Please go to Settings and add your Asana access token to use Asana features.")

if __name__ == '__main__':
    unittest.main() 