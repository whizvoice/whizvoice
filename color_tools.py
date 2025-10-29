import random

def pick_random_color(user_id: str = None) -> dict:
    """
    Pick a random color from a predefined list.

    Args:
        user_id: The user ID making the request (optional)

    Returns:
        dict: Result containing success status and the chosen color
    """
    colors = [
        "Red",
        "Orange",
        "Yellow",
        "Green",
        "Blue",
        "Purple",
        "Black",
        "Pink",
        "Multi-color / pattern print"
    ]

    chosen_color = random.choice(colors)

    return {
        "success": True,
        "color": chosen_color
    }

# Claude tool definition
color_tools = [
    {
        "type": "custom",
        "name": "pick_random_color",
        "description": "Pick a random color from a predefined list (Red, Orange, Yellow, Green, Blue, Purple, Black, Pink, or Multi-color/pattern print) and display it on the device screen.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]
