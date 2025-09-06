#!/usr/bin/env python3
"""Test script to verify tool schemas and Claude's ability to use them"""

import json
from screen_agent_tools import screen_agent_tools
from asana_tools import asana_tools

def validate_tool_schema(tool):
    """Validate that a tool has the required fields for Claude"""
    required_fields = ['type', 'name', 'description', 'input_schema']
    schema_fields = ['type', 'properties', 'required']
    
    # Check required top-level fields
    for field in required_fields:
        if field not in tool:
            return False, f"Missing required field: {field}"
    
    # Check input_schema structure
    schema = tool.get('input_schema', {})
    for field in schema_fields:
        if field not in schema:
            return False, f"Missing input_schema field: {field}"
    
    # Check that properties is a dict
    if not isinstance(schema.get('properties', {}), dict):
        return False, "input_schema.properties must be a dictionary"
    
    # Check that required is a list
    if not isinstance(schema.get('required', []), list):
        return False, "input_schema.required must be a list"
    
    return True, "Valid"

def main():
    print("=" * 60)
    print("TOOL SCHEMA VALIDATION")
    print("=" * 60)
    
    # Find and validate launch_app tool
    launch_app_tool = next((t for t in screen_agent_tools if t.get('name') == 'launch_app'), None)
    
    if launch_app_tool:
        print("\n📱 launch_app tool found!")
        print(json.dumps(launch_app_tool, indent=2))
        
        valid, message = validate_tool_schema(launch_app_tool)
        if valid:
            print("✅ Schema is valid!")
        else:
            print(f"❌ Schema validation failed: {message}")
    else:
        print("❌ launch_app tool not found in screen_agent_tools!")
    
    # Compare with a working Asana tool
    print("\n" + "=" * 60)
    print("COMPARING WITH WORKING ASANA TOOL")
    print("=" * 60)
    
    asana_tool = next((t for t in asana_tools if t.get('name') == 'get_parent_tasks'), None)
    if asana_tool:
        print("\n📋 get_parent_tasks tool (known working):")
        print(json.dumps(asana_tool, indent=2))
        
        valid, message = validate_tool_schema(asana_tool)
        if valid:
            print("✅ Schema is valid!")
        else:
            print(f"❌ Schema validation failed: {message}")
    
    # Check all tools
    print("\n" + "=" * 60)
    print("ALL TOOLS VALIDATION SUMMARY")
    print("=" * 60)
    
    all_tools = screen_agent_tools + asana_tools
    for tool in all_tools:
        name = tool.get('name', 'UNNAMED')
        valid, message = validate_tool_schema(tool)
        status = "✅" if valid else "❌"
        print(f"{status} {name}: {message}")

if __name__ == "__main__":
    main()