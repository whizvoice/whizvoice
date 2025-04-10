import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from asana_tools import get_asana_workspaces, get_asana_tasks, get_date_range, get_current_date, get_parent_tasks, create_asana_task

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
        expected_tasks = [task for task in self.mock_tasks if task.get('due_on') == self.today]
        self.assertEqual(result, expected_tasks)
        
        # Verify correct API calls
        mock_task_api.get_tasks.assert_called_once_with({
            'workspace': 'workspace1',
            'assignee': 'user1',
            'completed_since': 'now',
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
        
        # Mock workspaces
        mock_workspace_api = MagicMock()
        mock_workspaces_api.return_value = mock_workspace_api
        mock_workspaces_api.get_workspaces.return_value = self.mock_workspaces
        
        # Mock task API
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.get_tasks.return_value = self.mock_tasks
        
        # Call function without workspace
        result = get_asana_tasks()
        
        # Assert - should return error message when no preference is set
        self.assertEqual(result, "Error identifying user's preferred workspace. Please set a preferred workspace using the set_workspace_preference tool.")
        
        # Verify get_preference was called
        mock_get_pref.assert_called_once_with('asana_workspace_preference')
        
        # Now test with a preference set
        mock_get_pref.reset_mock()
        mock_get_pref.return_value = 'workspace2'
        
        # Call function without workspace
        result = get_asana_tasks()
        
        # Assert
        expected_tasks = [task for task in self.mock_tasks if task.get('due_on') == self.today]
        self.assertEqual(result, expected_tasks)
        
        # Verify it used the preferred workspace
        mock_task_api.get_tasks.assert_called_with({
            'workspace': 'workspace2',
            'assignee': 'user1',
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name'
        })
        
        # Verify get_preference was called
        mock_get_pref.assert_called_once_with('asana_workspace_preference')

    @patch('asana.WorkspacesApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.get_preference')
    def test_get_tasks_no_workspaces(self, mock_get_pref, mock_client, mock_workspaces_api):
        """Test handling no workspaces case"""
        # Setup mock to return empty workspace list
        mock_api = MagicMock()
        mock_workspaces_api.return_value = mock_api
        mock_api.get_workspaces.return_value = []
        
        # Mock preference to return None
        mock_get_pref.return_value = None
        
        # Call function
        result = get_asana_tasks()
        
        # Assert
        self.assertEqual(result, "Error identifying user's preferred workspace. Please set a preferred workspace using the set_workspace_preference tool.")
        
        # Verify get_preference was called
        mock_get_pref.assert_called_once_with('asana_workspace_preference')

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

    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.datetime')
    @patch('asana_tools.get_preference')
    def test_create_asana_task(self, mock_get_pref, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test creating a task in Asana"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock preference to return None (no preference set)
        mock_get_pref.return_value = None
        
        # Mock workspaces
        mock_workspace_api = MagicMock()
        mock_workspaces_api.return_value = mock_workspace_api
        mock_workspace_api.get_workspaces.return_value = self.mock_workspaces
        
        # Mock task creation
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_task.return_value = {'gid': 'new_task1', 'name': 'New Task'}
        
        # Call function with specific workspace
        result = create_asana_task('New Task', 'workspace1', '2024-03-20', 'Task notes')
        
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
            opts={'opt_fields': 'name,due_on,completed,projects.name'}
        )
        
        # Verify get_preference was not called since we provided a workspace
        mock_get_pref.assert_not_called()
        
    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.datetime')
    @patch('asana_tools.get_preference')
    def test_create_asana_subtask(self, mock_get_pref, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test creating a subtask in Asana"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock preference to return None (no preference set)
        mock_get_pref.return_value = None
        
        # Mock workspaces
        mock_workspace_api = MagicMock()
        mock_workspaces_api.return_value = mock_workspace_api
        mock_workspace_api.get_workspaces.return_value = self.mock_workspaces
        
        # Mock task creation
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_subtask_for_task.return_value = {'gid': 'new_subtask1', 'name': 'New Subtask'}
        
        # Call function with specific workspace and parent task
        result = create_asana_task('New Subtask', 'workspace1', '2024-03-20', 'Subtask notes', 'parent_task1')
        
        # Assert
        self.assertEqual(result['gid'], 'new_subtask1')
        self.assertEqual(result['name'], 'New Subtask')
        
        # Verify correct API calls
        mock_task_api.create_subtask_for_task.assert_called_once_with(
            task_gid='parent_task1',
            body={'data': {
                'name': 'New Subtask',
                'workspace': 'workspace1',
                'assignee': 'user1',
                'due_on': '2024-03-20',
                'notes': 'Subtask notes'
            }},
            opts={'opt_fields': 'name,due_on,completed,projects.name'}
        )
        
        # Verify get_preference was not called since we provided a workspace
        mock_get_pref.assert_not_called()
        
    @patch('asana.TasksApi')
    @patch('asana.WorkspacesApi')
    @patch('asana.UsersApi')
    @patch('asana.ApiClient')
    @patch('asana_tools.datetime')
    @patch('asana_tools.get_preference')
    def test_create_asana_task_with_preference(self, mock_get_pref, mock_datetime, mock_client, mock_users_api, mock_workspaces_api, mock_tasks_api):
        """Test creating a task in Asana with a workspace preference set"""
        # Setup mocks
        mock_datetime.now.return_value = self.fixed_date
        mock_user_api = MagicMock()
        mock_users_api.return_value = mock_user_api
        mock_user_api.get_user.return_value = {'gid': 'user1'}
        
        # Mock preference to return a workspace
        mock_get_pref.return_value = 'preferred_workspace'
        
        # Mock task creation
        mock_task_api = MagicMock()
        mock_tasks_api.return_value = mock_task_api
        mock_task_api.create_task.return_value = {'gid': 'new_task1', 'name': 'New Task'}
        
        # Call function without specifying a workspace
        result = create_asana_task('New Task', due_date='2024-03-20', notes='Task notes')
        
        # Assert
        self.assertEqual(result['gid'], 'new_task1')
        self.assertEqual(result['name'], 'New Task')
        
        # Verify correct API calls
        mock_task_api.create_task.assert_called_once_with(
            body={'data': {
                'name': 'New Task',
                'workspace': 'preferred_workspace',
                'assignee': 'user1',
                'due_on': '2024-03-20',
                'notes': 'Task notes'
            }},
            opts={'opt_fields': 'name,due_on,completed,projects.name'}
        )
        
        # Verify get_preference was called
        mock_get_pref.assert_called_once_with('asana_workspace_preference')

if __name__ == '__main__':
    unittest.main() 