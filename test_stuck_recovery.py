"""When a screen agent tool fails on an unexpected screen, the phone attaches a `stuck` block
(goal, failed_step, foreground_app, screen with [n] element ids, recovery). These tests pin the
server side of that contract: the system prompt teaches the model how to recover, and the generic
UI tools accept the ids the block already carries.

app.py is read as text on purpose: importing it starts Stripe and Redis clients.
"""
import pathlib

from screen_agent_tools import STUCK_RECOVERY_PROMPT, screen_agent_tools


def _tool(name):
    return next(t for t in screen_agent_tools if t["name"] == name)


def test_prompt_names_the_stuck_block_and_the_recovery_tools():
    p = STUCK_RECOVERY_PROMPT
    assert "stuck" in p
    for tool in ("agent_click", "agent_press_back"):
        assert tool in p


def test_prompt_forbids_granting_and_committing_actions():
    p = STUCK_RECOVERY_PROMPT.lower()
    assert "never" in p and "allow" in p and "permission" in p
    for word in ("send", "pay", "delete"):
        assert word in p


def test_prompt_limits_retry_to_once_and_handles_wrong_foreground_app():
    p = STUCK_RECOVERY_PROMPT.lower()
    assert "once" in p
    assert "foreground_app" in STUCK_RECOVERY_PROMPT and "launch" in p


def test_click_description_accepts_ids_from_a_stuck_screen():
    click = _tool("agent_click")
    assert "stuck" in click["description"]
    assert "stuck" in click["input_schema"]["properties"]["element_id"]["description"]


def test_get_ui_description_says_it_is_optional_after_a_stuck_result():
    assert "stuck" in _tool("agent_get_ui")["description"]


def test_system_prompt_includes_recovery_text():
    src = pathlib.Path(__file__).with_name("app.py").read_text()
    assert "CLAUDE_SYSTEM_PROMPT = " in src
    assert "+ STUCK_RECOVERY_PROMPT" in src
