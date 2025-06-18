import unittest
from unittest.mock import patch, mock_open, MagicMock
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from about_me_tool import get_app_info, about_me_tools

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
        self.assertEqual(len(about_me_tools), 1)
        
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

if __name__ == '__main__':
    unittest.main() 