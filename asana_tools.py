import asana
from asana.rest import ApiException as AsanaError
from datetime import datetime, timedelta
import json
from preferences import get_preference, set_preference, get_decrypted_preference_key, get_user_timezone
import asyncio
import pytz

def get_asana_client(user_id):
    """Get an Asana client configured with the user's access token."""
    configuration = asana.Configuration()
    token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not token:
        raise ValueError("Asana access token not found. Please go to Settings and add your Asana access token to use Asana features.")
    configuration.access_token = token
    return asana.ApiClient(configuration)

def get_date_range(range_str=None):
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

def get_asana_workspaces(user_id):
    configuration = asana.Configuration()
    asana_access_token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not asana_access_token:
        return "Error: Asana access token not found in user preferences."
    configuration.access_token = asana_access_token
    api_client = asana.ApiClient(configuration)

    try:
        workspaces_api = asana.WorkspacesApi(api_client)
        workspaces = list(workspaces_api.get_workspaces(opts={}))
        return workspaces
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def get_asana_tasks(user_id: str, start_date=None, end_date=None):
    configuration = asana.Configuration()
    asana_access_token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not asana_access_token:
        return "Error: Asana access token not found in user preferences."
    configuration.access_token = asana_access_token
    api_client = asana.ApiClient(configuration)
    
    workspace_gid = get_preference(user_id, 'asana_workspace_preference')
    if not workspace_gid:
        return "Error identifying user's preferred workspace to get tasks from. Please set a preferred workspace using the set_workspace_preference tool."
    try:
        api_client = get_asana_client(user_id)
        # Get current user
        users_api = asana.UsersApi(api_client)
        me = users_api.get_user("me", {})

        # Handle date defaults
        today = get_current_date()
        if not start_date:
            start_date = today
        if not end_date:
            end_date = start_date
            
        tasks_api = asana.TasksApi(api_client)
        
        # Get tasks using the regular Tasks API
        tasks = list(tasks_api.get_tasks({
            'workspace': workspace_gid,
            'assignee': me['gid'],
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name'
        }))

        # Filter out any tasks that don't have a due date
        tasks = [task for task in tasks if task.get('due_on') is not None]
        
        # Filter tasks by date range
        tasks = [task for task in tasks if start_date <= task['due_on'] <= end_date]
        
        return tasks
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def get_current_date(user_id: str = None) -> str:
    """Get today's date in YYYY-MM-DD format, using the user's timezone if available."""
    if user_id:
        try:
            user_tz = get_user_timezone(user_id)
            return datetime.now(user_tz).strftime('%Y-%m-%d')
        except Exception as e:
            return f"Error using timezone for user {user_id}, falling back to PST: {str(e)}"
    
    # Fallback to PST if no user_id or timezone error
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d')

def get_parent_tasks(user_id: str):
    configuration = asana.Configuration()
    asana_access_token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not asana_access_token:
        return "Error: Asana access token not found in user preferences."
    configuration.access_token = asana_access_token
    api_client = asana.ApiClient(configuration)

    workspace_gid = get_preference(user_id, 'asana_workspace_preference')
    if not workspace_gid:
        return "Error identifying user's preferred workspace to get parent tasks from. Please set a preferred workspace using the set_workspace_preference tool."
    try:
        api_client = get_asana_client(user_id)
        # Get current user
        users_api = asana.UsersApi(api_client)
        me = users_api.get_user("me", {})
            
        tasks_api = asana.TasksApi(api_client)
        
        # Get all tasks in the workspace
        tasks = list(tasks_api.get_tasks({
            'workspace': workspace_gid,
            'assignee': me['gid'],
            'completed_since': 'now',
            'opt_fields': 'name,due_on,completed,projects.name,num_subtasks'
        }))
        
        # Filter tasks to only those with subtasks and not completed
        parent_tasks = [task for task in tasks 
                       if task.get('num_subtasks', 0) > 0 and 
                       not task.get('completed', False)]
        
        return parent_tasks
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def create_asana_task(user_id: str, name, due_date=None, notes=None, parent_task_gid=None):
    configuration = asana.Configuration()
    asana_access_token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not asana_access_token:
        return "Error: Asana access token not found in user preferences."
    configuration.access_token = asana_access_token
    api_client = asana.ApiClient(configuration)

    workspace_gid = get_preference(user_id, 'asana_workspace_preference')
    if not workspace_gid:
        return "Error identifying user's preferred workspace that the new Asana task should be created in. Please set a preferred workspace using the set_workspace_preference tool."

    try:
        api_client = get_asana_client(user_id)
        # Get current user
        users_api = asana.UsersApi(api_client)
        me = users_api.get_user("me", {})
            
        tasks_api = asana.TasksApi(api_client)
        
        # Set due_date to today if not provided
        if due_date is None:
            due_date = get_current_date()
        
        # Prepare task data
        task_data = {
            'name': name,
            'workspace': workspace_gid,
            'assignee': me['gid'],
            'due_on': due_date
        }
        
        # Add optional fields if provided
        if notes:
            task_data['notes'] = notes
            
        # Create the task
        if parent_task_gid is None:
            new_task = tasks_api.create_task(body={'data': task_data}, opts={'opt_fields': 'name,due_on,completed,projects.name'})
        else:
            new_task = tasks_api.create_subtask_for_task(body={'data': task_data}, task_gid=parent_task_gid, opts={'opt_fields': 'name,due_on,completed,projects.name'})

        return new_task
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def change_task_parent(user_id: str, task_gid, new_parent_gid=None):
    configuration = asana.Configuration()
    asana_access_token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not asana_access_token:
        return "Error: Asana access token not found in user preferences."
    configuration.access_token = asana_access_token
    api_client = asana.ApiClient(configuration)
    try:
        api_client = get_asana_client(user_id)
        tasks_api = asana.TasksApi(api_client)
        # Update the task's parent
        updated_task = tasks_api.set_parent_for_task(
            body={'data': {'parent': new_parent_gid}},
            task_gid=task_gid,
            opts={'opt_fields': 'name,due_on,completed,projects.name'}
        )
        print(f"DEBUG: Updated task: {updated_task}")
        return updated_task
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

# Define available tools
asana_tools = [
    {
        "type": "custom",
        "name": "set_workspace_preference",
        "description": "Set your preferred Asana workspace. Before setting the workspace preference, please check if a preference is already set using the get_workspace_preference tool. Pass the workspace GID to make it the default. If you do not know the GID please use the get_asana_workspaces tool to get a list of workspaces and their GIDs. If there is more than one, ask the user which is their preferred workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace_gid": {
                    "type": "string",
                    "description": "The GID of your preferred workspace"
                }
            },
            "required": ["workspace_gid"]
        }
    },
    {
        "type": "custom",
        "name": "get_workspace_preference",
        "description": "Get your currently preferred Asana workspace GID.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "get_current_date",
        "description": "Get today's date in YYYY-MM-DD format.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "get_asana_tasks",
        "description": "Get tasks assigned to the current user within a date range. If the user doesn't specify the date, no need to include start_date or end_date; it will default to today.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format. Defaults to today."
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Defaults to start_date."
                }
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "get_asana_workspaces",
        "description": "Get all available workspaces.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "get_parent_tasks",
        "description": "Get all parent tasks (tasks with subtasks).",
        "input_schema": {
            "type": "object",
            "properties": {
            },
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "create_asana_task",
        "description": "Create a new task in Asana, with a strong preference to be a subtask of a parent task. Before using this tool, please guess what the parent task should be based on the name of the task and existing parent tasks. If you are pretty confident that you know the correct parent task, go ahead and create the task. If you're not sure, please confirm the parent task with the user before creating the task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the task to create"
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date in YYYY-MM-DD format. Defaults to today."
                },
                "notes": {
                    "type": "string",
                    "description": "Notes/description for the task. Defaults to None."
                },
                "parent_task_gid": {
                    "type": "string",
                    "description": "GID of the parent task if this is a subtask. While it is not required, please try to provide a parent task GID if possible to prevent tasks from getting lost in the user's inbox."
                }
            },
            "required": ["name"]
        }
    },
    {
        "type": "custom",
        "name": "change_task_parent",
        "description": "Change the parent task of an existing task. If new_parent_gid is None, the task will become a standalone task (no parent).",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_gid": {
                    "type": "string",
                    "description": "The GID of the task to update"
                },
                "new_parent_gid": {
                    "type": "string",
                    "description": "The GID of the new parent task. If None, the task will become a standalone task."
                }
            },
            "required": ["task_gid"]
        }
    }
] 