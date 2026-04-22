#!/usr/bin/env python3
"""
Extract training data for the on-device tool router from Supabase conversation history.

For each user message, pairs it with the next assistant response to create training
examples. Output format is JSONL where each line is:
  {"input": "<user text>", "output": "TOOL <name> <params_json>"} or
  {"input": "<user text>", "output": "NEEDS_LLM"}

Usage:
    cd whizvoice && python extract_training_data.py
    cd whizvoice && python extract_training_data.py --output training_data/raw_training_data.jsonl
    cd whizvoice && python extract_training_data.py --stats  # print stats only, no file output
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from typing import List, Dict, Optional, Tuple

from supabase_client import supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = "training_data/raw_training_data.jsonl"

# All known tools in the system. Every tool is locally routable for tool selection.
ALL_TOOLS = {
    # Device-side tools
    "agent_set_alarm", "agent_set_timer", "agent_stop_ringing", "agent_snooze_rage_shake",
    "agent_get_next_alarm", "agent_delete_alarm", "agent_toggle_flashlight",
    "agent_calendar_event", "agent_dial_phone_number", "agent_press_call_button",
    "agent_set_volume", "agent_lookup_phone_contacts",
    "agent_app_control", "agent_launch_app", "agent_close_app", "agent_open_app", "agent_close_other_app",
    "agent_disable_continuous_listening", "agent_set_tts_enabled",
    "cancel_pending_screen_tools", "agent_fitbit_add_quick_calories",
    "agent_select_chat", "agent_draft_message", "agent_send_message", "agent_dismiss_draft",
    "agent_whatsapp_select_chat", "agent_whatsapp_send_message", "agent_whatsapp_draft_message",
    "agent_sms_select_chat", "agent_sms_send_message", "agent_sms_draft_message",
    "agent_youtube_music", "agent_play_youtube_music", "agent_queue_youtube_music",
    "agent_pause_youtube_music",
    "agent_search_google_maps_location", "agent_search_google_maps_phrase",
    "agent_get_google_maps_directions", "agent_recenter_google_maps",
    "agent_fullscreen_google_maps", "agent_select_location_from_list",
    "agent_draft_calendar_event", "agent_save_calendar_event",
    "agent_dismiss_alarm", "agent_dismiss_timer", "agent_dismiss_amdroid_alarm",
    # Server-side tools
    "manage_workspace_preference", "get_current_datetime", "get_current_date",
    "get_asana_tasks", "get_asana_workspaces", "get_parent_tasks",
    "get_new_asana_task_id", "update_asana_task", "delete_asana_task",
    "manage_parent_task_preference",
    "get_weather", "save_location",
    "get_info",
    "add_contact_preference", "get_contact_preference",
    "list_contact_preferences", "remove_contact_preference",
    "set_user_timezone", "get_user_timezone",
    "pick_random_color",
    "manage_music_app_preference",
    "set_temperature_units",
}


def fetch_all_messages() -> List[Dict]:
    """Fetch all messages from Supabase, ordered by conversation and timestamp."""
    logger.info("Fetching messages from Supabase...")

    all_messages = []
    page_size = 1000
    offset = 0

    while True:
        result = supabase.table("messages") \
            .select("id, conversation_id, content, message_sender, content_type, tool_content, timestamp, request_id, cancelled") \
            .order("conversation_id", desc=False) \
            .order("timestamp", desc=False) \
            .range(offset, offset + page_size - 1) \
            .execute()

        if not result.data:
            break

        all_messages.extend(result.data)
        logger.info(f"  Fetched {len(all_messages)} messages so far...")

        if len(result.data) < page_size:
            break
        offset += page_size

    logger.info(f"Total messages fetched: {len(all_messages)}")
    return all_messages


def group_messages_by_conversation(messages: List[Dict]) -> Dict[int, List[Dict]]:
    """Group messages by conversation_id."""
    convos = {}
    for msg in messages:
        cid = msg["conversation_id"]
        if cid not in convos:
            convos[cid] = []
        convos[cid].append(msg)
    return convos


def extract_tool_use_from_message(msg: Dict) -> Optional[Dict]:
    """Extract the first tool_use block from a message's tool_content.

    Returns dict with 'name' and 'input' keys, or None.
    """
    tool_content = msg.get("tool_content")
    if not tool_content:
        return None

    for block in tool_content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return {
                "name": block.get("name", ""),
                "input": block.get("input", {})
            }
    return None


def format_tool_output(tool_name: str, tool_input: dict) -> str:
    """Format a tool call as the training output string."""
    # Sort keys for consistency
    params_json = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    return f"TOOL {tool_name} {params_json}"


def extract_training_pairs(conversations: Dict[int, List[Dict]]) -> List[Dict]:
    """Extract (user_input, output) training pairs from conversation histories.

    For each user text message, finds the immediately following assistant response.
    If the assistant used a tool, the output is TOOL <name> <params>.
    If the assistant responded with text only, the output is NEEDS_LLM.
    """
    training_examples = []
    skipped_cancelled = 0
    skipped_empty = 0
    skipped_tool_result_only = 0

    for conv_id, messages in conversations.items():
        # Filter out cancelled messages
        active_messages = [m for m in messages if not m.get("cancelled")]

        for i, msg in enumerate(active_messages):
            # We only care about USER messages that have text content
            if msg["message_sender"] != "USER":
                continue

            # Skip messages that are only tool_results (no user text)
            content = (msg.get("content") or "").strip()
            content_type = msg.get("content_type", "text")

            if content_type == "tool_result" and not content:
                skipped_tool_result_only += 1
                continue

            if not content:
                skipped_empty += 1
                continue

            # Find the next ASSISTANT message
            assistant_msg = None
            for j in range(i + 1, len(active_messages)):
                if active_messages[j]["message_sender"] == "ASSISTANT":
                    assistant_msg = active_messages[j]
                    break

            if assistant_msg is None:
                continue

            # Determine the output label
            tool_use = extract_tool_use_from_message(assistant_msg)

            if tool_use and tool_use["name"]:
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                output = format_tool_output(tool_name, tool_input)
            else:
                # No tool call - assistant responded with text
                output = "NEEDS_LLM"

            training_examples.append({
                "input": content,
                "output": output,
                "conversation_id": conv_id,  # metadata, not used for training
            })

    logger.info(f"Skipped {skipped_cancelled} cancelled, {skipped_empty} empty, "
                f"{skipped_tool_result_only} tool-result-only messages")
    return training_examples


def print_stats(examples: List[Dict]):
    """Print statistics about the extracted training data."""
    total = len(examples)
    needs_llm = sum(1 for e in examples if e["output"] == "NEEDS_LLM")
    tool_calls = total - needs_llm

    print(f"\n{'=' * 60}")
    print(f"Training Data Statistics")
    print(f"{'=' * 60}")
    print(f"Total examples:     {total}")
    print(f"  NEEDS_LLM:        {needs_llm} ({100*needs_llm/total:.1f}%)" if total else "")
    print(f"  Tool calls:       {tool_calls} ({100*tool_calls/total:.1f}%)" if total else "")

    # Count by tool name
    tool_counter = Counter()
    unknown_tools = Counter()
    for e in examples:
        if e["output"].startswith("TOOL "):
            parts = e["output"].split(" ", 2)
            tool_name = parts[1] if len(parts) > 1 else "UNKNOWN"
            tool_counter[tool_name] += 1
            if tool_name not in ALL_TOOLS:
                unknown_tools[tool_name] += 1

    print(f"\nTool distribution ({len(tool_counter)} unique tools):")
    for tool_name, count in tool_counter.most_common():
        marker = " [UNKNOWN]" if tool_name in unknown_tools else ""
        print(f"  {tool_name}: {count}{marker}")

    if unknown_tools:
        print(f"\nWarning: {len(unknown_tools)} unknown tools found (not in ALL_TOOLS set)")

    # Show some example inputs per tool
    print(f"\nSample examples per tool:")
    tool_examples = {}
    for e in examples:
        if e["output"].startswith("TOOL "):
            parts = e["output"].split(" ", 2)
            tool_name = parts[1]
            if tool_name not in tool_examples:
                tool_examples[tool_name] = []
            if len(tool_examples[tool_name]) < 3:
                tool_examples[tool_name].append(e["input"][:80])

    for tool_name in sorted(tool_examples.keys()):
        print(f"\n  {tool_name}:")
        for ex in tool_examples[tool_name]:
            print(f"    - \"{ex}\"")

    # Show some NEEDS_LLM examples
    print(f"\n  NEEDS_LLM (sample):")
    llm_examples = [e["input"][:80] for e in examples if e["output"] == "NEEDS_LLM"][:5]
    for ex in llm_examples:
        print(f"    - \"{ex}\"")

    print(f"\n{'=' * 60}")


def write_jsonl(examples: List[Dict], output_path: str):
    """Write training examples to JSONL file (without metadata fields)."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w") as f:
        for example in examples:
            # Only write input/output, not metadata like conversation_id
            line = json.dumps({
                "input": example["input"],
                "output": example["output"]
            }, ensure_ascii=False)
            f.write(line + "\n")

    logger.info(f"Wrote {len(examples)} examples to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Extract tool router training data from Supabase")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"Output JSONL file (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--stats", action="store_true",
                        help="Print statistics only, don't write output file")
    args = parser.parse_args()

    # Fetch and process
    messages = fetch_all_messages()
    conversations = group_messages_by_conversation(messages)
    logger.info(f"Found {len(conversations)} conversations")

    examples = extract_training_pairs(conversations)
    logger.info(f"Extracted {len(examples)} training examples")

    # Always print stats
    print_stats(examples)

    # Write output unless --stats only
    if not args.stats:
        write_jsonl(examples, args.output)
        print(f"\nOutput written to: {args.output}")


if __name__ == "__main__":
    main()
