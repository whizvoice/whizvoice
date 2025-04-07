from constants import ASANA_ACCESS_TOKEN
import asana
from asana.rest import ApiException
from datetime import datetime
import json

def get_asana_tasks():
    """Get tasks assigned to the current user that are due today"""
    # Configure Asana client
    configuration = asana.Configuration()
    configuration.access_token = ASANA_ACCESS_TOKEN
    api_client = asana.ApiClient(configuration)

    try:
        # Get current user
        users_api = asana.UsersApi(api_client)
        me = users_api.get_user("me", {})

        # Get user's workspaces
        workspaces_api = asana.WorkspacesApi(api_client)
        workspaces = list(workspaces_api.get_workspaces({}))
        
        if not workspaces:
            return "No workspaces found"
        if len(workspaces) < 2:
            return "Only one workspace found"
            
        # Get tasks from second workspace
        workspace_gid = workspaces[1]['gid']
        tasks_api = asana.TasksApi(api_client)
        today = datetime.now().strftime('%Y-%m-%d')
        
        tasks = list(tasks_api.get_tasks({
            'workspace': workspace_gid,
            'assignee': me['gid'],
            'completed_since': 'now',
            'due_on': today,
            'opt_fields': 'name,due_on,completed,projects.name'
        }))
        return tasks
    except ApiException as e:
        return f"Error accessing Asana API: {str(e)}"

# Define available tools
tools = [
    {
        "type": "custom",
        "name": "get_asana_tasks",
        "description": "Tool for fetching tasks in Asana.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
] 