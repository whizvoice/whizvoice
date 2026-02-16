import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import asyncio

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import TOOL_REGISTRY, execute_tool

class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.test_user_id = "test_user_123"

    def test_tool_registry_structure(self):
        """Test that the tool registry has the expected structure"""
        self.assertIsInstance(TOOL_REGISTRY, dict)
        self.assertGreater(len(TOOL_REGISTRY), 0)
        
        # Check that all expected tools are present
        expected_tools = [
            "get_asana_workspaces", "get_asana_tasks", "get_current_date",
            "get_parent_tasks", "get_new_asana_task_id", "set_workspace_preference",
            "get_workspace_preference", "update_asana_task", "delete_asana_task",
            "get_app_info", "set_temperature_units"
        ]
        
        for tool_name in expected_tools:
            with self.subTest(tool=tool_name):
                self.assertIn(tool_name, TOOL_REGISTRY)

    def test_tool_registry_entry_structure(self):
        """Test that each tool registry entry has the required fields"""
        required_fields = ["function_name", "requires_auth", "args_mapping", "validation"]
        
        for tool_name, tool_config in TOOL_REGISTRY.items():
            with self.subTest(tool=tool_name):
                for field in required_fields:
                    self.assertIn(field, tool_config, f"Tool {tool_name} missing field {field}")
                
                # Check field types
                self.assertIsInstance(tool_config["function_name"], str)
                self.assertIsInstance(tool_config["requires_auth"], bool)
                self.assertTrue(callable(tool_config["args_mapping"]))
                # validation can be None or callable
                self.assertTrue(tool_config["validation"] is None or callable(tool_config["validation"]))

    def test_auth_requirements_classification(self):
        """Test that tools are correctly classified as requiring auth or not"""
        # Public tools (should not require auth)
        public_tools = ["get_current_date", "get_app_info"]
        
        # Protected tools (should require auth)
        protected_tools = [
            "get_asana_workspaces", "get_asana_tasks", "get_parent_tasks",
            "get_new_asana_task_id", "set_workspace_preference", "get_workspace_preference",
            "update_asana_task", "delete_asana_task", "set_temperature_units"
        ]
        
        for tool_name in public_tools:
            with self.subTest(tool=tool_name):
                self.assertFalse(TOOL_REGISTRY[tool_name]["requires_auth"],
                               f"Tool {tool_name} should not require auth")
        
        for tool_name in protected_tools:
            with self.subTest(tool=tool_name):
                self.assertTrue(TOOL_REGISTRY[tool_name]["requires_auth"],
                              f"Tool {tool_name} should require auth")

    def test_args_mapping_functionality(self):
        """Test that args_mapping functions work correctly"""
        # Test get_asana_tasks args mapping
        tool_config = TOOL_REGISTRY["get_asana_tasks"]
        test_args = {"start_date": "2024-01-01", "end_date": "2024-01-31"}
        mapped_args = tool_config["args_mapping"](test_args, self.test_user_id)
        
        expected = (self.test_user_id, "2024-01-01", "2024-01-31")
        self.assertEqual(mapped_args, expected)
        
        # Test get_new_asana_task_id args mapping
        tool_config = TOOL_REGISTRY["get_new_asana_task_id"]
        test_args = {
            "name": "Test Task",
            "due_date": "2024-03-15",
            "notes": "Test notes",
            "parent_task_gid": "parent123"
        }
        mapped_args = tool_config["args_mapping"](test_args, self.test_user_id)
        
        expected = (self.test_user_id, "Test Task", "2024-03-15", "Test notes", "parent123", None, False)
        self.assertEqual(mapped_args, expected)

    def test_validation_functionality(self):
        """Test that validation functions work correctly"""
        # Test get_new_asana_task_id validation (name required)
        tool_config = TOOL_REGISTRY["get_new_asana_task_id"]
        validation_func = tool_config["validation"]
        
        # Test with missing name
        result = validation_func({"due_date": "2024-03-15"})
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Task name is required.")
        
        # Test with name present
        result = validation_func({"name": "Test Task"})
        self.assertIsNone(result)
        
        # Test set_workspace_preference validation (workspace_gid required)
        tool_config = TOOL_REGISTRY["set_workspace_preference"]
        validation_func = tool_config["validation"]
        
        # Test with missing workspace_gid - returns ValueError object
        result = validation_func({})
        self.assertIsInstance(result, ValueError)
        self.assertIn("Workspace GID is required", str(result))
        
        # Test with workspace_gid present
        result = validation_func({"workspace_gid": "workspace123"})
        self.assertIsNone(result)

    def test_function_name_resolution(self):
        """Test that all function names in the registry can be resolved"""
        import app
        
        for tool_name, tool_config in TOOL_REGISTRY.items():
            with self.subTest(tool=tool_name):
                function_name = tool_config["function_name"]
                # Check that the function exists in the app module's globals
                self.assertTrue(hasattr(app, function_name) or function_name in globals(),
                              f"Function {function_name} not found for tool {tool_name}")

    def test_registry_maintains_original_behavior(self):
        """Test that the registry-based execute_tool maintains the same behavior as the original"""
        # Test a few key scenarios to ensure backward compatibility
        
        # Test public tool without user_id
        with patch('app.get_current_date') as mock_func:
            mock_func.return_value = '2024-03-15'
            result = asyncio.run(execute_tool("get_current_date", {}, user_id=None))
            self.assertEqual(result, '2024-03-15')
            mock_func.assert_called_once_with(None)
        
        # Test protected tool without user_id (should return error)
        result = asyncio.run(execute_tool("get_asana_workspaces", {}, user_id=None))
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertIn("User authentication required", result["error"])
        
        # Test validation error
        result = asyncio.run(execute_tool("get_new_asana_task_id", {"due_date": "2024-03-15"}, self.test_user_id))
        self.assertIsInstance(result, dict)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Task name is required.")

    def test_adding_new_tool_pattern(self):
        """Test that the registry pattern supports easy addition of new tools"""
        # This test demonstrates how easy it would be to add a new tool
        new_tool_config = {
            "function_name": "hypothetical_new_function",
            "requires_auth": True,
            "args_mapping": lambda args, user_id: (user_id, args.get('param1')),
            "validation": lambda args: {"error": "param1 required"} if not args.get('param1') else None
        }
        
        # Verify the config has the right structure
        self.assertIn("function_name", new_tool_config)
        self.assertIn("requires_auth", new_tool_config)
        self.assertIn("args_mapping", new_tool_config)
        self.assertIn("validation", new_tool_config)
        
        # Test the config functions
        test_args = {"param1": "value1"}
        mapped_args = new_tool_config["args_mapping"](test_args, self.test_user_id)
        self.assertEqual(mapped_args, (self.test_user_id, "value1"))
        
        validation_result = new_tool_config["validation"](test_args)
        self.assertIsNone(validation_result)
        
        validation_result = new_tool_config["validation"]({})
        self.assertIsInstance(validation_result, dict)
        self.assertIn("error", validation_result)

if __name__ == '__main__':
    unittest.main() 