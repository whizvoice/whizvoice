def pick_random_color(user_id: str = None) -> dict:
    """
    Pick a color. Hardcoded to always return Black.

    Args:
        user_id: The user ID making the request (optional)

    Returns:
        dict: Result containing success status and the chosen color
    """
    return {
        "success": True,
        "color": "Black"
    }

# Claude tool definition
color_tools = [
    {
        "type": "custom",
        "name": "pick_random_color",
        "description": "Pick a random color from a predefined list (Red, Orange, Yellow, Green, Blue, Purple, Black, Pink, or Multi-color/pattern print). You MUST this when the user is asking you to pick a color and doesn't specify a subset of colors, for example for clothes or an outfit or something they are buying.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]
