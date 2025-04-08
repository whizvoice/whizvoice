from constants import ASANA_ACCESS_TOKEN
import asana
from asana.rest import ApiException
from datetime import datetime
import json

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

def get_asana_tasks(workspace_gid=None):
    """Get tasks assigned to the current user that are due today"""
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
            workspace_gid = workspaces[1]['gid']

        tasks_api = asana.TasksApi(api_client)
        today = datetime.now().strftime('%Y-%m-%d')
        
        tasks = list(tasks_api.get_tasks({
            'workspace': workspace_gid,
            'assignee': me['gid'],
            'completed_since': 'now',
            'due_on': today,
            'due_on.exists': 'true',
            'opt_fields': 'name,due_on,completed,projects.name'
        }))
        # Filter for tasks that have today's date
        tasks = [task for task in tasks if task.get('due_on') == today]
        return tasks
    except ApiException as e:
        return f"Error accessing Asana API: {str(e)}"

# Define available tools
tools = [
    {
        "type": "custom",
        "name": "get_asana_tasks",
        "description": "Get tasks due today from a specific workspace in Asana. If no workspace is specified, uses the second workspace. If you don't know the workspace GID, use the get_workspaces tool to get the workspace GID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace_gid": {
                    "type": "string",
                    "description": "The GID of the workspace to get tasks from"
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