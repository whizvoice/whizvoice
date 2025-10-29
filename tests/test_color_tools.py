import unittest
import sys
import os

# Add the parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from color_tools import pick_random_color, color_tools

class TestColorTools(unittest.TestCase):
    def test_pick_random_color_returns_valid_color(self):
        """Test that pick_random_color returns a color from the valid list"""
        valid_colors = [
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

        result = pick_random_color()

        # Check that result has success and color fields
        self.assertIn("success", result)
        self.assertIn("color", result)

        # Check that success is True
        self.assertTrue(result["success"])

        # Check that the color is in the valid list
        self.assertIn(result["color"], valid_colors)

if __name__ == '__main__':
    unittest.main()
