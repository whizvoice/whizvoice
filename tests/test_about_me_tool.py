import unittest
from unittest.mock import patch, mock_open, MagicMock
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from about_me_tool import get_app_info, get_user_data, about_me_tools

class TestAboutMeTool(unittest.TestCase):
    def setUp(self):
        self.test_user_id = "test_user_123"
        self.mock_content = """# WhizVoice App

WhizVoice is an AI-powered chatbot with Asana integration.

## Features
- Chat with Claude AI
- Manage Asana tasks
- Voice interactions
"""

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.path.join')
    @patch('os.path.dirname')
    @patch('os.path.abspath')
    def test_get_app_info_success(self, mock_abspath, mock_dirname, mock_join, mock_file):
        """Test successful reading of ABOUTME.md file"""
        # Mock file path construction
        mock_abspath.return_value = "/path/to/about_me_tool.py"
        mock_dirname.return_value = "/path/to"
        mock_join.return_value = "/path/to/ABOUTME.md"
        
        # Mock file content
        mock_file.return_value.read.return_value = self.mock_content
        
        result = get_app_info(self.test_user_id)
        
        self.assertEqual(result, self.mock_content)
        mock_file.assert_called_once_with("/path/to/ABOUTME.md", 'r', encoding='utf-8')

    @patch('builtins.open', side_effect=FileNotFoundError())
    @patch('os.path.join')
    @patch('os.path.dirname')
    @patch('os.path.abspath')
    def test_get_app_info_file_not_found(self, mock_abspath, mock_dirname, mock_join, mock_file):
        """Test handling of missing ABOUTME.md file"""
        mock_abspath.return_value = "/path/to/about_me_tool.py"
        mock_dirname.return_value = "/path/to"
        mock_join.return_value = "/path/to/ABOUTME.md"
        
        result = get_app_info(self.test_user_id)
        
        self.assertIn("Error: App information file not found", result)
        self.assertIn("contact support", result)

    @patch('builtins.open', side_effect=PermissionError("Permission denied"))
    @patch('os.path.join')
    @patch('os.path.dirname')
    @patch('os.path.abspath')
    def test_get_app_info_permission_error(self, mock_abspath, mock_dirname, mock_join, mock_file):
        """Test handling of permission errors when reading file"""
        mock_abspath.return_value = "/path/to/about_me_tool.py"
        mock_dirname.return_value = "/path/to"
        mock_join.return_value = "/path/to/ABOUTME.md"
        
        result = get_app_info(self.test_user_id)
        
        self.assertIn("Error reading app information", result)
        self.assertIn("Permission denied", result)

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.path.join')
    @patch('os.path.dirname')
    @patch('os.path.abspath')
    def test_get_app_info_without_user_id(self, mock_abspath, mock_dirname, mock_join, mock_file):
        """Test get_app_info works without user_id parameter"""
        mock_abspath.return_value = "/path/to/about_me_tool.py"
        mock_dirname.return_value = "/path/to"
        mock_join.return_value = "/path/to/ABOUTME.md"
        mock_file.return_value.read.return_value = self.mock_content
        
        result = get_app_info()
        
        self.assertEqual(result, self.mock_content)
        mock_file.assert_called_once_with("/path/to/ABOUTME.md", 'r', encoding='utf-8')

    @patch('builtins.open', side_effect=UnicodeDecodeError('utf-8', b'', 0, 1, 'invalid start byte'))
    @patch('os.path.join')
    @patch('os.path.dirname')
    @patch('os.path.abspath')
    def test_get_app_info_encoding_error(self, mock_abspath, mock_dirname, mock_join, mock_file):
        """Test handling of encoding errors when reading file"""
        mock_abspath.return_value = "/path/to/about_me_tool.py"
        mock_dirname.return_value = "/path/to"
        mock_join.return_value = "/path/to/ABOUTME.md"
        
        result = get_app_info(self.test_user_id)
        
        self.assertIn("Error reading app information", result)
        self.assertIn("invalid start byte", result)

    def test_about_me_tools_structure(self):
        """Test that about_me_tools has the correct structure"""
        self.assertIsInstance(about_me_tools, list)
        self.assertEqual(len(about_me_tools), 2)
        
        tool = about_me_tools[0]
        self.assertIsInstance(tool, dict)
        
        # Check required fields
        self.assertEqual(tool["type"], "custom")
        self.assertEqual(tool["name"], "get_app_info")
        self.assertIn("description", tool)
        self.assertIn("input_schema", tool)
        
        # Check input schema structure
        schema = tool["input_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("properties", schema)
        self.assertIn("required", schema)
        self.assertEqual(schema["required"], [])

    def test_about_me_tools_description(self):
        """Test that the tool description is appropriate"""
        tool = about_me_tools[0]
        description = tool["description"]

        self.assertIn("Whiz Voice", description)
        self.assertIn("features", description)
        self.assertIn("functionality", description)
        self.assertIn("how to use", description)

    @patch('about_me_tool.get_preference')
    def test_get_user_data_with_all_preferences(self, mock_get_preference):
        """Test get_user_data with all preferences set"""
        # Mock all preferences
        def get_pref_side_effect(user_id, key):
            prefs = {
                'asana_workspace_preference': 'workspace_123',
                'music_app_preference': 'YouTube Music',
                'user_timezone': 'America/Los_Angeles'
            }
            return prefs.get(key)

        mock_get_preference.side_effect = get_pref_side_effect

        result = get_user_data(self.test_user_id)

        self.assertIn("Here's what we know about you:", result)
        self.assertIn("Preferred Asana workspace: workspace_123", result)
        self.assertIn("Preferred music app: YouTube Music", result)
        self.assertIn("Timezone: America/Los_Angeles", result)

    @patch('about_me_tool.get_preference')
    def test_get_user_data_with_no_preferences(self, mock_get_preference):
        """Test get_user_data when no preferences are set"""
        mock_get_preference.return_value = None

        result = get_user_data(self.test_user_id)

        self.assertEqual(result, "We currently have no stored preferences for you.")

    @patch('about_me_tool.get_preference')
    def test_get_user_data_with_partial_preferences(self, mock_get_preference):
        """Test get_user_data with only some preferences set"""
        def get_pref_side_effect(user_id, key):
            if key == 'music_app_preference':
                return 'Spotify'
            return None

        mock_get_preference.side_effect = get_pref_side_effect

        result = get_user_data(self.test_user_id)

        self.assertIn("Here's what we know about you:", result)
        self.assertIn("Preferred music app: Spotify", result)
        self.assertNotIn("Asana workspace", result)
        self.assertNotIn("Timezone", result)

    @patch('about_me_tool.get_preference')
    def test_get_user_data_handles_exceptions(self, mock_get_preference):
        """Test that get_user_data handles exceptions gracefully"""
        mock_get_preference.side_effect = Exception("Database error")

        result = get_user_data(self.test_user_id)

        self.assertIn("Error retrieving user data", result)
        self.assertIn("Database error", result)

    def test_get_user_data_tool_in_tools_list(self):
        """Test that get_user_data tool is properly defined in about_me_tools"""
        # Find the get_user_data tool
        user_data_tool = None
        for tool in about_me_tools:
            if tool["name"] == "get_user_data":
                user_data_tool = tool
                break

        self.assertIsNotNone(user_data_tool)
        self.assertEqual(user_data_tool["type"], "custom")
        self.assertIn("description", user_data_tool)
        self.assertIn("what data we have stored", user_data_tool["description"])
        self.assertIn("preferences", user_data_tool["description"])

        # Check input schema
        schema = user_data_tool["input_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], [])

if __name__ == '__main__':
    unittest.main() 