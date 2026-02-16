import asana
from asana.rest import ApiException as AsanaError
from datetime import datetime, timedelta
import json
from preferences import get_preference, set_preference, get_decrypted_preference_key, get_user_timezone
import asyncio
import pytz
import time

# Module-level caches for performance
_asana_client_cache = {}  # user_id -> (client, timestamp)
_user_gid_cache = {}  # user_id -> asana_gid
_workspace_pref_cache = {}  # user_id -> (workspace_gid, timestamp)
CACHE_TTL = 300  # 5 minutes

# Redis client for shared caching across workers
_redis_client = None

def init_redis_client(client):
    """Set the Redis client for shared caching."""
    global _redis_client
    _redis_client = client

_CREATE_TASK_DESC_PARENT_REQUIRED = "Create a new task in Asana. This task MUST be a subtask of a parent task — never create a standalone task unless the user explicitly asks you to create a new parent task (use is_parent_task=true for that). Before using this tool, determine the appropriate parent task based on the task name and existing parent tasks. If there's one likely candidate, use it. Otherwise, ask the user which parent task to use. DO NOT use this tool when you can update a task with update_asana_task instead. If the user specifies a specific due date (e.g. two weeks from now), you MUST ALWAYS use the get_current_date tool before calculating the due_date. Otherwise, don't include the due_date parameter as it defaults to today. Never create a new parent task without being explicitly asked. No need to tell the user the ID of the task unless they ask. If the user wants to assign a task to another person, first use get_contact_preference to look up their email, then pass it as assignee_email."

_CREATE_TASK_DESC_DEFAULT = "Create a new task in Asana. DO NOT use this tool when you can update a task with update_asana_task instead. If the user specifies a specific due date (e.g. two weeks from now), you MUST ALWAYS use the get_current_date tool before calculating the due_date. Otherwise, don't include the due_date parameter as it defaults to today. Never create a new parent task without being explicitly asked. No need to tell the user the ID of the task unless they ask. If the user wants to assign a task to another person, first use get_contact_preference to look up their email, then pass it as assignee_email."

def get_asana_client(user_id):
    """Get an Asana client configured with the user's access token. Cached per user."""
    # Check cache first
    if user_id in _asana_client_cache:
        client, timestamp = _asana_client_cache[user_id]
        if time.time() - timestamp < CACHE_TTL:
            return client

    # Create new client
    configuration = asana.Configuration()
    token = get_decrypted_preference_key(user_id, 'asana_access_token')
    if not token:
        raise ValueError("Asana access token not found. Please go to Settings and add your Asana access token to use Asana features.")
    configuration.access_token = token
    client = asana.ApiClient(configuration)

    _asana_client_cache[user_id] = (client, time.time())
    return client

def get_asana_user_gid(user_id, api_client):
    """Get the Asana user GID for "me", cached per user."""
    if user_id not in _user_gid_cache:
        users_api = asana.UsersApi(api_client)
        me = users_api.get_user("me", {})
        _user_gid_cache[user_id] = me['gid']
    return _user_gid_cache[user_id]

def get_workspace_preference(user_id):
    """Get workspace preference with in-memory caching."""
    if user_id in _workspace_pref_cache:
        value, timestamp = _workspace_pref_cache[user_id]
        if time.time() - timestamp < CACHE_TTL:
            return value
    value = get_preference(user_id, 'asana_workspace_preference')
    if value:
        _workspace_pref_cache[user_id] = (value, time.time())
    return value

def clear_workspace_preference_cache(user_id):
    """Clear cached workspace preference for a user."""
    _workspace_pref_cache.pop(user_id, None)

async def get_parent_task_preference(user_id):
    """Get parent task preference using Redis for cross-worker shared caching."""
    redis_key = f"pref:parent_task:{user_id}"
    if _redis_client:
        try:
            cached = await _redis_client.get(redis_key)
            if cached is not None:
                return cached
        except Exception:
            pass  # Fall through to DB on Redis error
    value = get_preference(user_id, 'asana_parent_task_preference')
    if value is not None and _redis_client:
        try:
            await _redis_client.set(redis_key, value, ex=CACHE_TTL)
        except Exception:
            pass
    return value

async def set_parent_task_preference(user_id, require_parent):
    """Set the user's parent task preference. require_parent is a boolean."""
    value = "true" if require_parent else "false"
    result = set_preference(user_id, 'asana_parent_task_preference', value)
    if result and _redis_client:
        try:
            await _redis_client.set(f"pref:parent_task:{user_id}", value, ex=CACHE_TTL)
        except Exception:
            pass
    return result

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
    try:
        api_client = get_asana_client(user_id)
        workspaces_api = asana.WorkspacesApi(api_client)
        workspaces = list(workspaces_api.get_workspaces(opts={}))
        return workspaces
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def get_asana_tasks(user_id: str, start_date=None, end_date=None):
    workspace_gid = get_workspace_preference(user_id)
    if not workspace_gid:
        return "Error identifying user's preferred workspace to get tasks from. Please set a preferred workspace using the set_workspace_preference tool."
    try:
        api_client = get_asana_client(user_id)
        user_gid = get_asana_user_gid(user_id, api_client)

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
            'assignee': user_gid,
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
            success, user_tz = get_user_timezone(user_id)
            if success:
                return datetime.now(user_tz).strftime('%Y-%m-%d')
            else:
                # user_tz contains error message in this case
                return f"Error using timezone for user {user_id}: {user_tz}"
        except Exception as e:
            return f"Error using timezone for user {user_id}, falling back to PST: {str(e)}"
    
    # Fallback to PST if no user_id or timezone error
    return datetime.now(pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d')

def get_parent_tasks(user_id: str):
    workspace_gid = get_workspace_preference(user_id)
    if not workspace_gid:
        return "Error identifying user's preferred workspace to get parent tasks from. Please set a preferred workspace using the set_workspace_preference tool."
    try:
        api_client = get_asana_client(user_id)
        user_gid = get_asana_user_gid(user_id, api_client)

        tasks_api = asana.TasksApi(api_client)

        # Get all tasks in the workspace
        tasks = list(tasks_api.get_tasks({
            'workspace': workspace_gid,
            'assignee': user_gid,
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

async def get_new_asana_task_id(user_id: str, name, due_date=None, notes=None, parent_task_gid=None, assignee_email=None, is_parent_task=False):
    workspace_gid = get_workspace_preference(user_id)
    if not workspace_gid:
        return "Error identifying user's preferred workspace that the new Asana task should be created in. Please set a preferred workspace using the set_workspace_preference tool."

    # Enforce parent task preference
    parent_pref = await get_parent_task_preference(user_id)
    if parent_pref == "true" and parent_task_gid is None and not is_parent_task:
        return "Error: Your parent task preference requires all new tasks to be subtasks. Please provide a parent_task_gid, or set is_parent_task=True if this is intended as a new parent task."

    try:
        api_client = get_asana_client(user_id)
        user_gid = get_asana_user_gid(user_id, api_client)

        tasks_api = asana.TasksApi(api_client)

        # Set due_date to today if not provided
        if due_date is None:
            due_date = get_current_date()

        # Prepare task data
        task_data = {
            'name': name,
            'workspace': workspace_gid,
            'assignee': assignee_email if assignee_email else user_gid,
            'due_on': due_date
        }

        # Add optional fields if provided
        if notes:
            task_data['notes'] = notes
            
        # Create the task
        if parent_task_gid is None:
            new_task = tasks_api.create_task(body={'data': task_data}, opts={'opt_fields': 'gid,name,due_on,completed,projects.name'})
        else:
            new_task = tasks_api.create_subtask_for_task(body={'data': task_data}, task_gid=parent_task_gid, opts={'opt_fields': 'gid,name,due_on,completed,projects.name'})

        # Convert to dict and add reminder message
        result = dict(new_task)
        result['_reminder'] = "Task created successfully. CRITICAL: Before responding to the user, check the conversation history for old versions of tasks you created. If you find any, you MUST call delete_asana_task for each one NOW (in your next response) to avoid duplicates."
        return result
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def update_asana_task(user_id: str, task_gid, name=None, due_date=None, notes=None, completed=None, parent_gid=None):
    """
    Update one or more fields of an Asana task. Only provided fields will be updated.

    Args:
        task_gid: The GID of the task to update
        name: New task name (optional)
        due_date: New due date in YYYY-MM-DD format (optional)
        notes: New task notes/description (optional)
        completed: Boolean to mark task as complete/incomplete (optional)
        parent_gid: New parent task GID, or None to remove parent (optional)
    """
    try:
        api_client = get_asana_client(user_id)
        tasks_api = asana.TasksApi(api_client)

        # Build update data for regular fields
        update_data = {}
        if name is not None:
            update_data['name'] = name
        if due_date is not None:
            update_data['due_on'] = due_date
        if notes is not None:
            update_data['notes'] = notes
        if completed is not None:
            update_data['completed'] = completed

        # Update regular fields if any were provided
        updated_task = None
        if update_data:
            updated_task = tasks_api.update_task(
                body={'data': update_data},
                task_gid=task_gid,
                opts={'opt_fields': 'gid,name,due_on,completed,projects.name'}
            )

        # Handle parent separately (uses different API endpoint)
        if parent_gid is not None:
            updated_task = tasks_api.set_parent_for_task(
                body={'data': {'parent': parent_gid}},
                task_gid=task_gid,
                opts={'opt_fields': 'gid,name,due_on,completed,projects.name'}
            )

        # If no updates were provided, fetch the current task
        if updated_task is None:
            updated_task = tasks_api.get_task(task_gid, opts={'opt_fields': 'gid,name,due_on,completed,projects.name'})

        return dict(updated_task)
    except ValueError as e:
        # Re-raise the token error to be handled by the WebSocket endpoint
        raise
    except AsanaError as e:
        status_code = e.status if hasattr(e, 'status') else 500
        if status_code == 401:
            return {"error": "Asana authentication failed. Please check your Asana Access Token in settings.", "detail": str(e), "status_code": 401}
        else:
            return {"error": "Asana API error.", "detail": str(e), "status_code": status_code}

def delete_asana_task(user_id: str, task_gid):
    """Delete an Asana task by its GID."""
    try:
        api_client = get_asana_client(user_id)
        tasks_api = asana.TasksApi(api_client)

        # Delete the task
        tasks_api.delete_task(task_gid=task_gid)
        return {"success": True, "message": f"Task {task_gid} has been deleted successfully."}
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
        "description": "Set your preferred Asana workspace. This is only necessary if the user hasn't set one yet, or if they want to change the preferred workspace that's set.",
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
        "name": "get_new_asana_task_id",
        "description": _CREATE_TASK_DESC_DEFAULT,
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
                },
                "assignee_email": {
                    "type": "string",
                    "description": "Email address of the person to assign the task to. If not provided, the task is assigned to the current user. Use get_contact_preference to look up a contact's email first."
                },
                "is_parent_task": {
                    "type": "boolean",
                    "description": "Set to true only when the user explicitly asks to create a new parent/top-level task. Skips the parent task requirement when the user's preference requires parent tasks."
                }
            },
            "required": ["name"]
        }
    },
    {
        "type": "custom",
        "name": "update_asana_task",
        "description": "Update an existing Asana task. You can update the task name, due date, notes, completion status, and/or parent in a single call. Only provide the fields you want to change - all parameters are optional. If the user specifies a specific due date (e.g. two weeks from now), you MUST use the get_current_date tool to calculate the due date in YYYY-MM-DD format. To mark a task as complete, set completed to true. To remove a parent (make it a standalone task), set parent_gid to null.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_gid": {
                    "type": "string",
                    "description": "The GID of the task to update"
                },
                "name": {
                    "type": "string",
                    "description": "New name for the task (optional)"
                },
                "due_date": {
                    "type": "string",
                    "description": "New due date in YYYY-MM-DD format (optional)"
                },
                "notes": {
                    "type": "string",
                    "description": "New notes/description for the task (optional)"
                },
                "completed": {
                    "type": "boolean",
                    "description": "Set to true to mark task as complete, false to mark as incomplete (optional)"
                },
                "parent_gid": {
                    "type": "string",
                    "description": "New parent task GID. Set to null to remove parent and make it a standalone task (optional)"
                }
            },
            "required": ["task_gid"]
        }
    },
    {
        "type": "custom",
        "name": "delete_asana_task",
        "description": "Delete an Asana task. This action is permanent and cannot be undone. Use this tool when the user explicitly asks to delete a task. You can also use this tool if you recreate a task, to clean up the old one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_gid": {
                    "type": "string",
                    "description": "The GID of the task to delete"
                }
            },
            "required": ["task_gid"]
        }
    },
    {
        "type": "custom",
        "name": "get_parent_task_preference",
        "description": "Get the user's parent task preference. Returns 'true' if tasks must always have a parent, 'false' if not required, or null if not set.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "custom",
        "name": "set_parent_task_preference",
        "description": "Set the user's parent task preference. When enabled (true), all new Asana tasks must have a parent task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "require_parent": {
                    "type": "boolean",
                    "description": "If true, all new tasks must have a parent task. If false, tasks can be standalone."
                }
            },
            "required": ["require_parent"]
        }
    }
] 