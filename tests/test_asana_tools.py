import asyncio
import unittest
from unittest.mock import patch, MagicMock
from asana.rest import ApiException as AsanaError
from datetime import datetime, timedelta
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asana_tools import (
    get_asana_workspaces, get_asana_tasks, get_date_range, get_current_date,
    get_parent_tasks, get_new_asana_task_id, delete_asana_task,
    get_asana_sections, update_asana_task,
    _asana_client_cache, _user_gid_cache, _workspace_pref_cache,
    _user_task_list_cache
)

class TestAsanaTools(unittest.TestCase):
    def setUp(self):
        self.test_user_id = "test_user_123"
        # Clear caches before each test to avoid cross-test pollution
        _asana_client_cache.clear()
        _user_gid_cache.clear()
        _workspace_pref_cache.clear()
        _user_task_list_cache.clear()
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
            'opt_fields': 'name,due_on,completed,projects.name,assignee_section.name'
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
        expected_error = "Error identifying user's preferred workspace to get tasks from. Please set a preferred workspace using the manage_workspace_preference tool."
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
            'opt_fields': 'name,due_on,completed,projects.name,assignee_section.name,num_subtasks'
        })

    @patch('asana_tools.get_parent_task_preference')
    @patch('asana_tools.get_current_date')
    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.TasksApi')
    def test_get_new_asana_task_id(self, mock_tasks_api, mock_users_api, mock_get_client, mock_get_token, mock_get_pref, mock_get_date, mock_get_parent_pref):
        """Test creating a task in Asana"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = "workspace1"
        mock_get_date.return_value = self.today
        mock_get_parent_pref.return_value = "false"

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}

        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_task.return_value = {'gid': 'new_task1', 'name': 'New Task'}

        # Test creating task
        result = asyncio.run(get_new_asana_task_id(self.test_user_id, 'New Task', due_date='2024-03-20', notes='Task notes'))

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
            opts={'opt_fields': 'gid,name,due_on,completed,projects.name,assignee_section.name'}
        )

    @patch('asana_tools.get_parent_task_preference')
    @patch('asana_tools.get_current_date')
    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.TasksApi')
    def test_create_asana_subtask(self, mock_tasks_api, mock_users_api, mock_get_client, mock_get_token, mock_get_pref, mock_get_date, mock_get_parent_pref):
        """Test creating a subtask in Asana"""
        # Setup mocks
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = "workspace1"
        mock_get_date.return_value = self.today
        mock_get_parent_pref.return_value = "false"

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}

        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_subtask_for_task.return_value = {'gid': 'new_subtask1', 'name': 'New Subtask'}

        # Test creating subtask
        result = asyncio.run(get_new_asana_task_id(self.test_user_id, 'New Subtask', due_date='2024-03-20', notes='Subtask notes', parent_task_gid='parent_task1'))

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
            opts={'opt_fields': 'gid,name,due_on,completed,projects.name,assignee_section.name'}
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

class TestAsanaSections(unittest.TestCase):
    """My Tasks sections: reading the section list and moving tasks between sections."""

    MY_TASKS_SECTIONS = [
        {'gid': 'sec_recent', 'name': 'Recently assigned'},
        {'gid': 'sec_today', 'name': 'Today'},
        {'gid': 'sec_upcoming', 'name': 'Upcoming'},
        {'gid': 'sec_later', 'name': 'Later'},
    ]

    def setUp(self):
        self.test_user_id = "test_user_123"
        _asana_client_cache.clear()
        _user_gid_cache.clear()
        _workspace_pref_cache.clear()
        _user_task_list_cache.clear()

    def _wire(self, mock_get_client, mock_users_api, mock_utl_api, mock_sections_api,
              sections=None):
        """Wire up the mocks shared by every section test. Returns the sections API mock."""
        mock_get_client.return_value = MagicMock()

        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}

        mock_utl = MagicMock()
        mock_utl_api.return_value = mock_utl
        mock_utl.get_user_task_list_for_user.return_value = {'gid': 'utl1'}

        mock_sections = MagicMock()
        mock_sections_api.return_value = mock_sections
        mock_sections.get_sections_for_project.return_value = (
            self.MY_TASKS_SECTIONS if sections is None else sections
        )
        return mock_sections

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.UsersApi')
    @patch('asana.UserTaskListsApi')
    @patch('asana.SectionsApi')
    def test_get_sections_returns_my_tasks_sections(
            self, mock_sections_api, mock_utl_api, mock_users_api, mock_get_client, mock_get_pref):
        """get_asana_sections lists the sections of the user's My Tasks list"""
        mock_get_pref.return_value = "workspace1"
        mock_sections = self._wire(mock_get_client, mock_users_api, mock_utl_api, mock_sections_api)

        result = get_asana_sections(self.test_user_id)

        self.assertEqual(result, self.MY_TASKS_SECTIONS)
        mock_sections.get_sections_for_project.assert_called_once_with(
            'utl1', opts={'opt_fields': 'name'})

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_decrypted_preference_key')
    def test_get_sections_no_workspace_preference(self, mock_get_token, mock_get_pref):
        """get_asana_sections reports the missing workspace preference"""
        mock_get_token.return_value = "fake_token"
        mock_get_pref.return_value = None

        result = get_asana_sections(self.test_user_id)

        self.assertIn("preferred workspace", result)


    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.TasksApi')
    @patch('asana.UsersApi')
    @patch('asana.UserTaskListsApi')
    @patch('asana.SectionsApi')
    def test_move_matches_section_name_ignoring_case(
            self, mock_sections_api, mock_utl_api, mock_users_api, mock_tasks_api,
            mock_get_client, mock_get_pref):
        """A section name is matched case-insensitively and sent as assignee_section"""
        mock_get_pref.return_value = "workspace1"
        self._wire(mock_get_client, mock_users_api, mock_utl_api, mock_sections_api)
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.update_task.return_value = {'gid': 'task1'}

        update_asana_task(self.test_user_id, 'task1', section='today')

        body = mock_task_api.update_task.call_args.kwargs['body']
        self.assertEqual(body['data']['assignee_section'], 'sec_today')

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.TasksApi')
    @patch('asana.UsersApi')
    @patch('asana.UserTaskListsApi')
    @patch('asana.SectionsApi')
    def test_move_matches_unique_substring(
            self, mock_sections_api, mock_utl_api, mock_users_api, mock_tasks_api,
            mock_get_client, mock_get_pref):
        """A partial name resolves when exactly one section contains it"""
        mock_get_pref.return_value = "workspace1"
        self._wire(mock_get_client, mock_users_api, mock_utl_api, mock_sections_api)
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.update_task.return_value = {'gid': 'task1'}

        update_asana_task(self.test_user_id, 'task1', section='recently')

        body = mock_task_api.update_task.call_args.kwargs['body']
        self.assertEqual(body['data']['assignee_section'], 'sec_recent')

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.TasksApi')
    @patch('asana.UsersApi')
    @patch('asana.UserTaskListsApi')
    @patch('asana.SectionsApi')
    def test_move_rejects_ambiguous_substring(
            self, mock_sections_api, mock_utl_api, mock_users_api, mock_tasks_api,
            mock_get_client, mock_get_pref):
        """An ambiguous partial name errors instead of guessing, and updates nothing"""
        mock_get_pref.return_value = "workspace1"
        self._wire(mock_get_client, mock_users_api, mock_utl_api, mock_sections_api, sections=[
            {'gid': 'sec_a', 'name': 'Work Today'},
            {'gid': 'sec_b', 'name': 'Home Today'},
        ])
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api

        result = update_asana_task(self.test_user_id, 'task1', name='Renamed', section='today')

        self.assertIn('Work Today', result['error'])
        self.assertIn('Home Today', result['error'])
        mock_task_api.update_task.assert_not_called()

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.TasksApi')
    @patch('asana.UsersApi')
    @patch('asana.UserTaskListsApi')
    @patch('asana.SectionsApi')
    def test_move_rejects_unknown_section(
            self, mock_sections_api, mock_utl_api, mock_users_api, mock_tasks_api,
            mock_get_client, mock_get_pref):
        """An unknown section errors, lists the real sections, and updates nothing"""
        mock_get_pref.return_value = "workspace1"
        self._wire(mock_get_client, mock_users_api, mock_utl_api, mock_sections_api)
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api

        result = update_asana_task(self.test_user_id, 'task1', name='Renamed', section='In Review')

        self.assertIn('In Review', result['error'])
        self.assertIn('Recently assigned', result['error'])
        mock_task_api.update_task.assert_not_called()

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.TasksApi')
    @patch('asana.UsersApi')
    @patch('asana.UserTaskListsApi')
    @patch('asana.SectionsApi')
    def test_move_on_someone_elses_task_explains_why(
            self, mock_sections_api, mock_utl_api, mock_users_api, mock_tasks_api,
            mock_get_client, mock_get_pref):
        """A rejected section move explains that My Tasks sections are per-assignee"""
        mock_get_pref.return_value = "workspace1"
        self._wire(mock_get_client, mock_users_api, mock_utl_api, mock_sections_api)
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        error = AsanaError(status=400, reason='Bad Request')
        mock_task_api.update_task.side_effect = error

        result = update_asana_task(self.test_user_id, 'task1', section='Today')

        self.assertIn('assigned to you', result['error'])

    @patch('asana_tools.get_preference')
    @patch('asana_tools.get_asana_client')
    @patch('asana.TasksApi')
    def test_update_without_section_sends_no_assignee_section(
            self, mock_tasks_api, mock_get_client, mock_get_pref):
        """Omitting section leaves the task's section untouched"""
        mock_get_pref.return_value = "workspace1"
        mock_get_client.return_value = MagicMock()
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.update_task.return_value = {'gid': 'task1'}

        update_asana_task(self.test_user_id, 'task1', name='Renamed')

        body = mock_task_api.update_task.call_args.kwargs['body']
        self.assertNotIn('assignee_section', body['data'])


if __name__ == '__main__':
    unittest.main()