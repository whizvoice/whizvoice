from constants import ASANA_ACCESS_TOKEN
import asana
from asana.rest import ApiException
from datetime import datetime, timedelta
import json

def get_date_range(range_str=None):
    """Convert date range string to start and end dates"""
    today = datetime.now().date()
    
    if not range_str:  # Default to today
        return today, today
        
    range_str = range_str.lower()
    if 'week' in range_str:
        end_date = today + timedelta(days=7)
        return today, end_date
    elif 'month' in range_str:
        end_date = today + timedelta(days=30)
        return today, end_date
    
    return today, today  # Default to today if range not recognized

def get_asana_workspaces():
    """Get all available workspaces"""
    configuration = asana.Configuration()
    configuration.access_token = ASANA_ACCESS_TOKEN
    api_client = asana.ApiClient(configuration)

    try:
        workspaces_api = asana.WorkspacesApi(api_client)
        workspaces = list(workspaces_api.get_workspaces({}))
        return workspaces
    except ApiException as e:
        return f"Error accessing Asana API: {str(e)}"

def get_asana_tasks(workspace_gid=None, start_date=None, end_date=None):
    """Get tasks assigned to the current user within a date range
    
    Args:
        workspace_gid (str, optional): Workspace to get tasks from. Defaults to second workspace.
        start_date (str, optional): Start date in YYYY-MM-DD format. Defaults to today.
        end_date (str, optional): End date in YYYY-MM-DD format. Defaults to start_date.
    """
    configuration = asana.Configuration()
    configuration.access_token = ASANA_ACCESS_TOKEN
    api_client = asana.ApiClient(configuration)

    try:
        # Get current user
        users_api = asana.UsersApi(api_client)
        me = users_api.get_user("me", {})

        # If no workspace specified, use second workspace
        if not workspace_gid:
            workspaces = list(asana.WorkspacesApi(api_client).get_workspaces({}))
            if not workspaces:
                return "No workspaces found"
            if len(workspaces) < 2:
                return "Only one workspace found"
            workspace_gid = workspaces[1]['gid']  # Use second workspace

        # Handle date defaults
        today = datetime.now().strftime('%Y-%m-%d')
        if not start_date:
            start_date = today
        if not end_date:
            end_date = start_date
            
        tasks_api = asana.TasksApi(api_client)
        tasks = list(tasks_api.get_tasks({
            'workspace': workspace_gid,
            'assignee': me['gid'],
            'completed_since': 'now',
            'due_on.after': start_date,
            'due_on.before': end_date,
            'opt_fields': 'name,due_on,completed,projects.name'
        }))
        
        # Filter tasks to match date range
        tasks = [task for task in tasks 
                if task.get('due_on') and 
                start_date <= task['due_on'] <= end_date]
        return tasks
    except ApiException as e:
        return f"Error accessing Asana API: {str(e)}"

def get_current_date():
    """Get today's date in YYYY-MM-DD format"""
    return datetime.now().strftime('%Y-%m-%d')

# Define available tools
tools = [
    {
        "type": "custom",
        "name": "get_current_date",
        "description": "Get today's date in YYYY-MM-DD format. Use this to format dates correctly when querying tasks.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "get_asana_tasks",
        "description": "Get tasks from Asana within a specific date range. Dates must be in YYYY-MM-DD format (e.g., '2024-03-15'). Use get_current_date to get today's date in the correct format.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace_gid": {
                    "type": "string",
                    "description": "The GID of the workspace to get tasks from"
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format. Use get_current_date to get today's date."
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Use get_current_date to get today's date."
                }
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "get_asana_workspaces",
        "description": "Get information about Asana workspaces.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
] 