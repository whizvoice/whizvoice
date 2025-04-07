from constants import ASANA_ACCESS_TOKEN
import asana
import json
from datetime import datetime

def get_asana_tasks():
    """Get tasks assigned to the current user that are due today"""
    client = asana.Client.access_token(ASANA_ACCESS_TOKEN)
    me = client.users.me()
    today = datetime.now().strftime('%Y-%m-%d')
    
    tasks = client.tasks.find_all({
        'assignee': me['gid'],
        'completed_since': 'now',
        'due_on': today,  # Only get tasks due today
        'opt_fields': ['name', 'due_on', 'completed', 'projects.name']
    })
    return list(tasks)

# Define available tools
tools = [
    {
        "type": "custom",
        "name": "get_tasks",
        "description": "Tool for fetching tasks in Asana.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
] 