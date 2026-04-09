#!/usr/bin/env python3
"""
Augment training data for the on-device tool router using Claude API.

Generates synthetic examples for underrepresented tools and speech transcription
error variants. Also normalizes legacy tool names from the raw extraction.

Usage:
    source ../whizvoiceapp/export_anthropic_key.sh
    cd whizvoice && python augment_training_data.py
    cd whizvoice && python augment_training_data.py --input training_data/raw_training_data.jsonl
    cd whizvoice && python augment_training_data.py --skip-generation  # only normalize, no API calls
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from typing import List, Dict, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_INPUT = "training_data/raw_training_data.jsonl"
DEFAULT_OUTPUT = "training_data/augmented_training_data.jsonl"

# Map legacy tool names to current canonical names
LEGACY_TOOL_MAP = {
    "launch_app": "agent_launch_app",
    "get_music_app_preference": "manage_music_app_preference",
    "get_parent_task_preference": "manage_parent_task_preference",
    "create_asana_task": "get_new_asana_task_id",
    "get_user_data": "get_info",
    "get_workspace_preference": "manage_workspace_preference",
    "play_youtube_music": "agent_play_youtube_music",
    "get_google_maps_directions": "agent_get_google_maps_directions",
    "get_app_info": "get_info",
    "whatsapp_draft_message": "agent_whatsapp_draft_message",
    "whatsapp_send_message": "agent_whatsapp_send_message",
    "fullscreen_google_maps": "agent_fullscreen_google_maps",
    "search_google_maps_location": "agent_search_google_maps_location",
    "search_google_maps_phrase": "agent_search_google_maps_phrase",
}

# Complete tool catalog with their schemas for synthetic generation
TOOL_SCHEMAS = {
    # Zero-param tools
    "agent_stop_ringing": {"params": {}, "examples": [
        "stop the alarm", "turn off the alarm", "dismiss the alarm",
        "stop ringing", "shut up", "silence the alarm",
    ]},
    "agent_close_app": {"params": {}, "examples": [
        "close yourself", "bye", "go away", "close the app",
        "exit", "quit", "shut down", "you can close now",
    ]},
    "agent_disable_continuous_listening": {"params": {}, "examples": [
        "stop listening", "turn off the mic", "mute the microphone",
        "disable listening", "stop the mic",
    ]},
    "agent_pause_youtube_music": {"params": {}, "examples": [
        "pause the music", "stop the music", "pause", "stop playing",
    ]},
    "agent_get_next_alarm": {"params": {}, "examples": [
        "when is my next alarm", "what alarm do I have set",
        "do I have any alarms", "check my alarms",
    ]},
    "agent_snooze_rage_shake": {"params": {}, "examples": [
        "snooze rage shake", "turn off shake detection",
        "disable shake to report", "stop the shake thing",
    ]},
    "cancel_pending_screen_tools": {"params": {}, "examples": [
        "cancel that", "stop what you're doing", "never mind cancel",
        "cancel the pending actions",
    ]},
    "agent_dismiss_draft": {"params": {}, "examples": [
        "cancel the message", "never mind don't send it",
        "dismiss the draft", "forget the message",
    ]},
    "agent_recenter_google_maps": {"params": {}, "examples": [
        "recenter the map", "recenter Google Maps",
        "center the map on me", "where am I on the map",
    ]},
    "agent_fullscreen_google_maps": {"params": {}, "examples": [
        "make Google Maps bigger", "fullscreen the map",
        "make the map full screen", "maximize Google Maps",
    ]},
    "get_current_datetime": {"params": {}, "examples": [
        "what time is it", "what's the current time",
        "what day is it", "what's today's date",
    ]},
    "get_asana_workspaces": {"params": {}, "examples": [
        "show my Asana workspaces", "what workspaces do I have",
        "list my Asana workspaces",
    ]},
    "get_parent_tasks": {"params": {}, "examples": [
        "what are my parent tasks", "show me the task categories",
        "list my parent tasks in Asana",
    ]},
    "list_contact_preferences": {"params": {}, "examples": [
        "show my contacts", "who's in my contact preferences",
        "list my saved contacts", "what contacts do you know about",
    ]},
    "get_user_timezone": {"params": {}, "examples": [
        "what timezone am I in", "what's my timezone setting",
    ]},
    "pick_random_color": {"params": {}, "examples": [
        "pick a random color", "give me a random color",
        "help me choose a color", "random color for my outfit",
    ]},
    "agent_dismiss_alarm": {"params": {}, "examples": [
        "dismiss the alarm", "turn off the current alarm",
        "stop the alarm that's ringing",
    ]},
    "agent_dismiss_timer": {"params": {}, "examples": [
        "dismiss the timer", "stop the timer", "turn off the timer",
    ]},
    "agent_dismiss_amdroid_alarm": {"params": {}, "examples": [
        "dismiss the AMdroid alarm", "turn off the AMdroid alarm",
        "stop the AMdroid alarm",
    ]},

    # Boolean-param tools
    "agent_toggle_flashlight": {"params": {"turn_on": "bool"}, "examples": [
        ("turn on the flashlight", {"turn_on": True}),
        ("turn off the flashlight", {"turn_on": False}),
        ("flashlight on", {"turn_on": True}),
        ("flashlight off", {"turn_on": False}),
        ("turn the torch on", {"turn_on": True}),
        ("kill the flashlight", {"turn_on": False}),
    ]},
    "agent_set_tts_enabled": {"params": {"enabled": "bool"}, "examples": [
        ("enable text to speech", {"enabled": True}),
        ("disable text to speech", {"enabled": False}),
        ("turn on voice", {"enabled": True}),
        ("stop talking", {"enabled": False}),
        ("speak out loud", {"enabled": True}),
        ("be quiet", {"enabled": False}),
        ("don't talk", {"enabled": False}),
        ("read responses aloud", {"enabled": True}),
    ]},

    # Numeric-param tools
    "agent_set_timer": {"params": {"seconds": "int"}, "examples": [
        ("set a timer for 5 minutes", {"seconds": 300}),
        ("set a timer for 10 minutes", {"seconds": 600}),
        ("timer for 1 minute", {"seconds": 60}),
        ("timer for 30 seconds", {"seconds": 30}),
        ("set a 15 minute timer", {"seconds": 900}),
        ("timer 2 minutes", {"seconds": 120}),
        ("set a timer for an hour", {"seconds": 3600}),
        ("timer for 45 minutes", {"seconds": 2700}),
        ("set a timer for 3 minutes", {"seconds": 180}),
        ("20 minute timer", {"seconds": 1200}),
        ("set a timer for 90 seconds", {"seconds": 90}),
        ("timer for half an hour", {"seconds": 1800}),
        ("set a 7 minute timer", {"seconds": 420}),
    ]},
    "agent_set_alarm": {"params": {"hour": "int", "minute": "int"}, "examples": [
        ("set an alarm for 7 AM", {"hour": 7, "minute": 0}),
        ("set an alarm for 6:30 AM", {"hour": 6, "minute": 30}),
        ("alarm at 8", {"hour": 8, "minute": 0}),
        ("wake me up at 5:45", {"hour": 5, "minute": 45}),
        ("set an alarm for 10 PM", {"hour": 22, "minute": 0}),
        ("alarm at noon", {"hour": 12, "minute": 0}),
        ("set an alarm for 9:15 AM", {"hour": 9, "minute": 15}),
    ]},
    "agent_delete_alarm": {"params": {"hour": "int", "minute": "int"}, "examples": [
        ("delete my 7 AM alarm", {"hour": 7, "minute": 0}),
        ("cancel the 6:30 alarm", {"hour": 6, "minute": 30}),
        ("remove my 8 o'clock alarm", {"hour": 8, "minute": 0}),
        ("delete the alarm at 10 PM", {"hour": 22, "minute": 0}),
    ]},
    "agent_set_volume": {"params": {"volume_level": "int"}, "examples": [
        ("set volume to 50%", {"volume_level": 50}),
        ("set volume to 100%", {"volume_level": 100}),
        ("turn it up to 75", {"volume_level": 75}),
        ("volume to 25", {"volume_level": 25}),
        ("set volume to 0", {"volume_level": 0}),
        ("max volume", {"volume_level": 100}),
        ("mute", {"volume_level": 0}),
    ]},
    "agent_fitbit_add_quick_calories": {"params": {"calories": "int"}, "examples": [
        ("log 400 calories on Fitbit", {"calories": 400}),
        ("add 500 calories to Fitbit", {"calories": 500}),
        ("track 1200 calories on my Fitbit", {"calories": 1200}),
        ("Fitbit log 300 calories", {"calories": 300}),
        ("add 250 calories", {"calories": 250}),
        ("log 800 calories", {"calories": 800}),
    ]},
    "get_weather": {"params": {"days_ahead": "int"}, "examples": [
        ("what's the weather today", {"days_ahead": 0}),
        ("weather tomorrow", {"days_ahead": 1}),
        ("what's the weather going to be like the day after tomorrow", {"days_ahead": 2}),
        ("weather forecast for today", {"days_ahead": 0}),
    ]},

    # String-param tools (span extraction from input)
    "agent_app_control": {"params": {"action": "str", "app_name": "str"}, "examples": [
        ("open Google Maps", {"action": "launch", "app_name": "Google Maps"}),
        ("launch YouTube", {"action": "launch", "app_name": "YouTube"}),
        ("open Chrome", {"action": "launch", "app_name": "Chrome"}),
        ("close WhatsApp", {"action": "close", "app_name": "WhatsApp"}),
        ("open Spotify", {"action": "launch", "app_name": "Spotify"}),
        ("launch Settings", {"action": "launch", "app_name": "Settings"}),
        ("open Gmail", {"action": "launch", "app_name": "Gmail"}),
        ("pull up Maps", {"action": "launch", "app_name": "Maps"}),
    ]},
    "agent_launch_app": {"params": {"app_name": "str"}, "examples": [
        ("open WhatsApp", {"app_name": "WhatsApp"}),
        ("launch Google Maps", {"app_name": "Google Maps"}),
        ("open YouTube Music", {"app_name": "YouTube Music"}),
    ]},
}

# Additional NEEDS_LLM examples for conversational / ambiguous inputs
NEEDS_LLM_EXAMPLES = [
    "hello", "hi there", "how are you", "what can you do",
    "thanks", "thank you", "good job", "never mind",
    "yes", "no", "okay", "sure", "yeah", "nope",
    "what did you say", "can you repeat that",
    "tell me a joke", "what's the meaning of life",
    "help me with my homework", "write me a poem",
    "how do I get to the store", "what's a good restaurant nearby",
    "remind me to call mom", "what's on my schedule",
    "can you send an email", "read my messages",
    "actually make that 10 minutes", "change it to 5",
    "yes send it", "no don't send that",
    "the second one", "pick the first option",
    "what did I just ask you to do",
    "go back", "undo that", "try again",
    "I don't know", "maybe", "let me think about it",
    "what's 2 plus 2", "translate hello to Spanish",
    "who is the president", "what year is it",
]


def load_raw_data(path: str) -> List[Dict]:
    """Load raw training data from JSONL."""
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def normalize_legacy_tools(examples: List[Dict]) -> Tuple[List[Dict], int]:
    """Normalize legacy tool names to current canonical names."""
    normalized = []
    count = 0
    for ex in examples:
        output = ex["output"]
        if output.startswith("TOOL "):
            parts = output.split(" ", 2)
            if len(parts) >= 2:
                tool_name = parts[1]
                if tool_name in LEGACY_TOOL_MAP:
                    new_name = LEGACY_TOOL_MAP[tool_name]
                    params = parts[2] if len(parts) > 2 else "{}"
                    output = f"TOOL {new_name} {params}"
                    count += 1
        normalized.append({"input": ex["input"], "output": output})
    return normalized, count


def generate_synthetic_examples_from_templates() -> List[Dict]:
    """Generate synthetic examples from the TOOL_SCHEMAS templates."""
    synthetic = []

    for tool_name, schema in TOOL_SCHEMAS.items():
        for example in schema["examples"]:
            if isinstance(example, tuple):
                text, params = example
                params_json = json.dumps(params, sort_keys=True)
                synthetic.append({
                    "input": text,
                    "output": f"TOOL {tool_name} {params_json}",
                })
            else:
                # Zero-param tool, example is just a string
                synthetic.append({
                    "input": example,
                    "output": f"TOOL {tool_name} {{}}",
                })

    # Add NEEDS_LLM examples
    for text in NEEDS_LLM_EXAMPLES:
        synthetic.append({
            "input": text,
            "output": "NEEDS_LLM",
        })

    return synthetic


def generate_with_claude(tool_name: str, schema: dict, existing_examples: List[str],
                         num_to_generate: int = 30) -> List[Dict]:
    """Use Claude API to generate more diverse examples for a tool."""
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed, skipping Claude generation")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping Claude generation")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    # Build the prompt
    params_desc = schema.get("params", {})
    examples_str = "\n".join(f"  - {ex}" for ex in existing_examples[:10])

    if params_desc:
        param_info = f"Parameters: {json.dumps(params_desc)}"
        param_instruction = """For each utterance, also provide the correct parameter values as JSON.
Output format: one example per line as: <utterance> ||| <params_json>
Example: set a timer for 5 minutes ||| {"seconds": 300}"""
    else:
        param_info = "This tool takes no parameters."
        param_instruction = "Output format: one utterance per line, no parameters needed."

    prompt = f"""Generate {num_to_generate} diverse, natural voice utterances that should trigger the tool "{tool_name}".
{param_info}

These are from a voice assistant, so utterances should sound like natural speech (sometimes with transcription errors).
Include variations like:
- Different phrasings for the same intent
- Casual vs formal speech
- Speech transcription errors (e.g., "set a time" instead of "set a timer")
- Incomplete sentences
- Different accents/dialects

Existing examples for reference:
{examples_str}

{param_instruction}

Generate exactly {num_to_generate} NEW examples (don't repeat the existing ones). Output ONLY the examples, nothing else."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        results = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Remove leading numbers/bullets
            line = re.sub(r'^[\d]+[\.\)]\s*', '', line)
            line = re.sub(r'^[-*]\s*', '', line)
            line = line.strip('"').strip("'").strip()

            if not line:
                continue

            if params_desc and "|||" in line:
                parts = line.split("|||", 1)
                utterance = parts[0].strip().strip('"').strip("'")
                try:
                    params = json.loads(parts[1].strip())
                    params_json = json.dumps(params, sort_keys=True)
                    results.append({
                        "input": utterance,
                        "output": f"TOOL {tool_name} {params_json}",
                    })
                except json.JSONDecodeError:
                    logger.debug(f"Skipping malformed params: {line}")
            elif not params_desc:
                results.append({
                    "input": line,
                    "output": f"TOOL {tool_name} {{}}",
                })

        logger.info(f"  Generated {len(results)} examples for {tool_name}")
        return results

    except Exception as e:
        logger.error(f"  Claude API error for {tool_name}: {e}")
        return []


def augment_data(raw_examples: List[Dict], skip_generation: bool = False,
                 target_per_tool: int = 50) -> List[Dict]:
    """Augment training data with synthetic examples."""

    # Step 1: Normalize legacy tool names
    normalized, norm_count = normalize_legacy_tools(raw_examples)
    logger.info(f"Normalized {norm_count} legacy tool names")

    # Step 2: Count examples per tool
    tool_counter = Counter()
    tool_inputs = {}  # tool_name -> list of input texts
    needs_llm_count = 0
    for ex in normalized:
        if ex["output"].startswith("TOOL "):
            parts = ex["output"].split(" ", 2)
            tool_name = parts[1]
            tool_counter[tool_name] += 1
            if tool_name not in tool_inputs:
                tool_inputs[tool_name] = []
            tool_inputs[tool_name].append(ex["input"])
        else:
            needs_llm_count += 1

    logger.info(f"Distribution before augmentation: {needs_llm_count} NEEDS_LLM, "
                f"{sum(tool_counter.values())} tool calls across {len(tool_counter)} tools")

    # Step 3: Generate template-based synthetic examples
    template_examples = generate_synthetic_examples_from_templates()
    logger.info(f"Generated {len(template_examples)} template-based examples")

    # Step 4: Generate Claude-based examples for underrepresented tools
    claude_examples = []
    if not skip_generation:
        for tool_name, schema in TOOL_SCHEMAS.items():
            current_count = tool_counter.get(tool_name, 0) + sum(
                1 for e in template_examples if e["output"].startswith(f"TOOL {tool_name} ")
            )
            if current_count < target_per_tool:
                needed = target_per_tool - current_count
                existing = tool_inputs.get(tool_name, [])
                # Add template examples to existing for context
                for e in template_examples:
                    if e["output"].startswith(f"TOOL {tool_name} "):
                        existing.append(e["input"])
                generated = generate_with_claude(tool_name, schema, existing, min(needed, 50))
                claude_examples.extend(generated)
                # Rate limit
                time.sleep(0.5)

        logger.info(f"Generated {len(claude_examples)} Claude-augmented examples")

    # Step 5: Combine all examples
    all_examples = normalized + template_examples + claude_examples

    # Step 6: Deduplicate by (input, output) pair
    seen = set()
    deduped = []
    for ex in all_examples:
        key = (ex["input"].lower().strip(), ex["output"])
        if key not in seen:
            seen.add(key)
            deduped.append(ex)

    logger.info(f"After dedup: {len(deduped)} examples (removed {len(all_examples) - len(deduped)} dupes)")
    return deduped


def print_stats(examples: List[Dict]):
    """Print statistics about the augmented training data."""
    total = len(examples)
    needs_llm = sum(1 for e in examples if e["output"] == "NEEDS_LLM")
    tool_calls = total - needs_llm

    print(f"\n{'=' * 60}")
    print(f"Augmented Training Data Statistics")
    print(f"{'=' * 60}")
    print(f"Total examples:     {total}")
    print(f"  NEEDS_LLM:        {needs_llm} ({100*needs_llm/total:.1f}%)" if total else "")
    print(f"  Tool calls:       {tool_calls} ({100*tool_calls/total:.1f}%)" if total else "")

    tool_counter = Counter()
    for e in examples:
        if e["output"].startswith("TOOL "):
            parts = e["output"].split(" ", 2)
            tool_name = parts[1]
            tool_counter[tool_name] += 1

    print(f"\nTool distribution ({len(tool_counter)} unique tools):")
    for tool_name, count in tool_counter.most_common():
        print(f"  {tool_name}: {count}")
    print(f"{'=' * 60}")


def write_jsonl(examples: List[Dict], output_path: str):
    """Write examples to JSONL."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        for ex in examples:
            f.write(json.dumps({"input": ex["input"], "output": ex["output"]},
                               ensure_ascii=False) + "\n")
    logger.info(f"Wrote {len(examples)} examples to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Augment tool router training data")
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        help=f"Input JSONL from extract step (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"Output JSONL (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--skip-generation", action="store_true",
                        help="Skip Claude API generation, only normalize and add templates")
    parser.add_argument("--target-per-tool", type=int, default=50,
                        help="Target minimum examples per tool (default: 50)")
    args = parser.parse_args()

    raw_data = load_raw_data(args.input)
    logger.info(f"Loaded {len(raw_data)} raw examples from {args.input}")

    augmented = augment_data(raw_data, skip_generation=args.skip_generation,
                             target_per_tool=args.target_per_tool)

    print_stats(augmented)
    write_jsonl(augmented, args.output)
    print(f"\nOutput written to: {args.output}")


if __name__ == "__main__":
    main()
