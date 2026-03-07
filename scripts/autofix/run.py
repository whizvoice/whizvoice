#!/usr/bin/env python3
"""
Auto-fix pipeline for screen agent UI navigation failures.

Queries Supabase for new UI dump errors on the latest app version,
invokes Claude Code CLI to generate fixes in the whizvoiceapp repo,
and creates PRs for human review.

Usage:
    # Clone whizvoiceapp automatically into a temp dir:
    python scripts/autofix/run.py

    # Use an existing local checkout:
    python scripts/autofix/run.py --whizvoiceapp-path /path/to/whizvoiceapp

    # Process a specific dump_reason only:
    python scripts/autofix/run.py --dump-reason "whatsapp_chat_not_found"

    # Skip emulator boot (if already running):
    python scripts/autofix/run.py --skip-emulator-boot

    # Test (and fix) a previous autofix PR:
    python scripts/autofix/run.py --test-pr 42 --whizvoiceapp-path /path/to/repo
    python scripts/autofix/run.py --test-pr https://github.com/whizvoice/whizvoiceapp/pull/42

Environment variables:
    SUPABASE_URL          - Supabase project URL
    SUPABASE_SERVICE_ROLE - Supabase service role key
    ANTHROPIC_API_KEY     - Anthropic API key for Claude Code
    WHIZVOICEAPP_REPO     - (optional) Git repo URL, default: git@github.com:whizvoice/whizvoiceapp.git
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

try:
    from supabase import create_client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("autofix")

# ---------------------------------------------------------------------------
# Config / credentials
# ---------------------------------------------------------------------------

WHIZVOICEAPP_REPO = os.getenv(
    "WHIZVOICEAPP_REPO", "git@github.com:whizvoice/whizvoiceapp.git"
)


EMULATOR_SERIAL = "emulator-5556"
AVD_NAME = "whiz-test-device"
SNAPSHOT_NAME = "baseline_clean"
ANDROID_HOME = os.getenv("ANDROID_HOME", "/opt/homebrew/share/android-commandlinetools")
EMULATOR_BIN = os.path.join(ANDROID_HOME, "emulator", "emulator")
ADB_BIN = os.path.join(ANDROID_HOME, "platform-tools", "adb")

# Track whether we booted the emulator so we know whether to shut it down
_we_booted_emulator = False


def is_emulator_running() -> bool:
    """Check if an emulator is running on EMULATOR_SERIAL."""
    try:
        result = subprocess.run(
            [ADB_BIN, "-s", EMULATOR_SERIAL, "get-state"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and "device" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_snapshot_path() -> str:
    """Return the expected path to the emulator snapshot."""
    android_avd_home = os.path.join(os.path.expanduser("~"), ".android", "avd")
    return os.path.join(android_avd_home, f"{AVD_NAME}.avd", "snapshots", SNAPSHOT_NAME)


def ensure_avd_snapshot(whizvoiceapp_path: str | None) -> bool:
    """Download the AVD snapshot if it doesn't exist locally. Returns True if available."""
    snapshot_path = get_snapshot_path()
    if os.path.isdir(snapshot_path):
        log.info(f"AVD snapshot already exists at {snapshot_path}")
        return True

    # Find the download script
    download_script = None
    if whizvoiceapp_path:
        candidate = os.path.join(whizvoiceapp_path, "scripts", "avd-snapshot-download.sh")
        if os.path.isfile(candidate):
            download_script = candidate

    if not download_script:
        # Try relative to this script's location (whizvoice/scripts/autofix/run.py -> whizvoiceapp/scripts/)
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        whiz_root = os.path.join(scripts_dir, "..", "..", "..")
        candidate = os.path.join(whiz_root, "whizvoiceapp", "scripts", "avd-snapshot-download.sh")
        if os.path.isfile(candidate):
            download_script = candidate

    if not download_script:
        log.error(
            f"AVD snapshot not found at {snapshot_path} and download script not found.\n"
            f"Either create the snapshot manually or ensure whizvoiceapp/scripts/avd-snapshot-download.sh exists."
        )
        return False

    log.info(f"AVD snapshot not found. Downloading via {download_script}...")
    try:
        result = subprocess.run(
            ["bash", download_script],
            timeout=600,  # 10 minute timeout
        )
        if result.returncode != 0:
            log.error("AVD snapshot download failed")
            return False
    except subprocess.TimeoutExpired:
        log.error("AVD snapshot download timed out after 10 minutes")
        return False

    # Verify it actually arrived
    if not os.path.isdir(snapshot_path):
        log.error(f"Download script completed but snapshot not found at {snapshot_path}")
        return False

    log.info("AVD snapshot downloaded successfully")
    return True


def boot_emulator(whizvoiceapp_path: str | None = None) -> bool:
    """Boot the emulator from snapshot. Returns True if ready."""
    global _we_booted_emulator

    if is_emulator_running():
        log.info(f"Emulator already running on {EMULATOR_SERIAL}")
        return True

    # Auto-download snapshot if missing
    if not ensure_avd_snapshot(whizvoiceapp_path):
        return False

    # Validate snapshot exists
    snapshot_path = get_snapshot_path()
    if not os.path.isdir(snapshot_path):
        log.error(
            f"Emulator snapshot not found at: {snapshot_path}\n"
            f"Create it by booting the emulator manually, setting up the desired state, "
            f"then saving a snapshot named '{SNAPSHOT_NAME}' via the emulator UI "
            f"(Extended Controls > Snapshots > Take Snapshot)."
        )
        return False

    log.info(f"Booting emulator '{AVD_NAME}' from snapshot '{SNAPSHOT_NAME}'...")

    # Kill any stale emulator on this port
    subprocess.run(
        [ADB_BIN, "-s", EMULATOR_SERIAL, "emu", "kill"],
        capture_output=True, timeout=10,
    )
    time.sleep(3)

    # Boot from snapshot
    subprocess.Popen(
        [EMULATOR_BIN, "-avd", AVD_NAME, "-snapshot", SNAPSHOT_NAME,
         "-port", "5556", "-no-audio", "-gpu", "swiftshader_indirect"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _we_booted_emulator = True

    # Wait for boot (120s timeout)
    max_wait = 120
    elapsed = 0
    while elapsed < max_wait:
        try:
            result = subprocess.run(
                [ADB_BIN, "-s", EMULATOR_SERIAL, "shell", "getprop", "sys.boot_completed"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip() == "1":
                log.info("Emulator is ready")
                time.sleep(5)  # Let the system settle
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        time.sleep(2)
        elapsed += 2

    log.error(f"Emulator did not boot within {max_wait}s")
    return False


def shutdown_emulator():
    """Kill the emulator if we booted it."""
    if not _we_booted_emulator:
        return
    log.info("Shutting down emulator...")
    try:
        subprocess.run(
            [ADB_BIN, "-s", EMULATOR_SERIAL, "emu", "kill"],
            capture_output=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def load_supabase():
    """Load Supabase client, trying env vars first, then constants.py."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE", os.getenv("SUPABASE_KEY", ""))

    if not url or not key:
        try:
            # Try loading from whizvoice/constants.py
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            whizvoice_dir = os.path.join(scripts_dir, "..", "..")
            sys.path.insert(0, whizvoice_dir)
            from constants import SUPABASE_URL, SUPABASE_SERVICE_ROLE
            url = SUPABASE_URL
            key = SUPABASE_SERVICE_ROLE
        except ImportError:
            log.error(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE env vars not set, "
                "and constants.py not found"
            )
            sys.exit(1)

    return create_client(url, key)


# ---------------------------------------------------------------------------
# Supabase queries
# ---------------------------------------------------------------------------


def get_latest_app_version(supabase) -> str | None:
    """Get the most recent app_version from ui dumps."""
    resp = (
        supabase.table("screen_agent_ui_dumps")
        .select("app_version")
        .not_.is_("app_version", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["app_version"]
    return None


def get_unprocessed_errors(supabase, app_version: str, dump_reason: str | None = None):
    """
    Fetch unprocessed error dumps for the given app version.
    Groups by dump_reason and picks the representative with the longest ui_hierarchy.
    """
    query = (
        supabase.table("screen_agent_ui_dumps")
        .select("*")
        .eq("app_version", app_version)
        .is_("processed_at", "null")
        .not_.is_("ui_hierarchy", "null")
    )

    if dump_reason:
        query = query.eq("dump_reason", dump_reason)

    resp = query.order("created_at", desc=True).limit(200).execute()

    if not resp.data:
        return []

    # Group by dump_reason, pick the one with the longest ui_hierarchy
    by_reason: dict[str, dict] = {}
    for row in resp.data:
        reason = row["dump_reason"]
        if reason not in by_reason:
            by_reason[reason] = row
        else:
            existing_len = len(by_reason[reason].get("ui_hierarchy") or "")
            current_len = len(row.get("ui_hierarchy") or "")
            if current_len > existing_len:
                by_reason[reason] = row

    return list(by_reason.values())


def mark_processed(supabase, dump_reason: str, app_version: str, pr_url: str | None):
    """Mark all dumps with this reason+version as processed."""
    supabase.table("screen_agent_ui_dumps").update(
        {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "autofix_pr_url": pr_url,
        }
    ).eq("dump_reason", dump_reason).eq("app_version", app_version).is_(
        "processed_at", "null"
    ).execute()


# ---------------------------------------------------------------------------
# Git / GitHub helpers
# ---------------------------------------------------------------------------


def ensure_whizvoiceapp(path: str | None) -> str:
    """Clone whizvoiceapp if no path given, return the checkout path."""
    if path:
        if not os.path.isdir(os.path.join(path, ".git")):
            log.error(f"Not a git repo: {path}")
            sys.exit(1)
        # Ensure we're on a clean main branch
        subprocess.run(["git", "fetch", "origin"], cwd=path, check=True)
        subprocess.run(
            ["git", "checkout", "main"], cwd=path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        return path

    # Clone into a temp directory
    tmp = tempfile.mkdtemp(prefix="whizvoiceapp-autofix-")
    log.info(f"Cloning whizvoiceapp into {tmp}")
    subprocess.run(
        ["git", "clone", "--depth=1", WHIZVOICEAPP_REPO, tmp], check=True
    )
    return tmp


def pr_already_exists(dump_reason: str) -> bool:
    """Check if an open PR already exists for this dump_reason."""
    sanitized = sanitize_branch_name(dump_reason)
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            "whizvoice/whizvoiceapp",
            "--search",
            f"autofix: {dump_reason}",
            "--state",
            "open",
            "--json",
            "number",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.warning(f"gh pr list failed: {result.stderr}")
        return False
    try:
        prs = json.loads(result.stdout)
        return len(prs) > 0
    except json.JSONDecodeError:
        return False


def sanitize_branch_name(s: str) -> str:
    """Turn a dump_reason into a valid git branch name component."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)[:60]


def create_branch(repo_path: str, dump_reason: str) -> str:
    """Create and checkout a new branch for this fix."""
    date_str = datetime.now().strftime("%Y%m%d")
    branch = f"autofix/{sanitize_branch_name(dump_reason)}-{date_str}"
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo_path, check=True)
    return branch


def commit_and_push(repo_path: str, branch: str, dump_reason: str) -> bool:
    """Stage, commit, and push changes. Returns True if there were changes."""
    # Check for changes (including new files like SKIP_REASON.txt)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        log.info("No changes made by Claude Code")
        return False

    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"autofix: {dump_reason}\n\nGenerated by screen agent auto-fix pipeline.",
        ],
        cwd=repo_path,
        check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch], cwd=repo_path, check=True
    )
    return True


def create_pr(
    repo_path: str,
    branch: str,
    dump_reason: str,
    error_message: str | None,
    test_result: str | None = None,
) -> str | None:
    """Create a PR via gh CLI. Returns the PR URL or None."""
    title = f"autofix: {dump_reason}"[:70]
    test_status_line = ""
    if test_result is not None:
        test_status_line = f"\n**Verification test**: {test_result}\n"
    body = (
        f"## Auto-Fix: Screen Agent Navigation Failure\n\n"
        f"**Error tag**: `{dump_reason}`\n"
        f"**Error message**: {error_message or 'N/A'}\n"
        f"{test_status_line}\n"
        f"This PR was generated by the screen agent auto-fix pipeline. "
        f"It attempts to fix a UI navigation failure caused by a target app update.\n\n"
        f"## Review Checklist\n"
        f"- [ ] Fix is backwards-compatible with previous app versions\n"
        f"- [ ] Only minimal changes to address the specific failure\n"
        f"- [ ] No unrelated code modifications\n"
    )
    result = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            "whizvoice/whizvoiceapp",
            "--title",
            title,
            "--body",
            body,
            "--head",
            branch,
            "--base",
            "main",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error(f"gh pr create failed: {result.stderr}")
        return None

    pr_url = result.stdout.strip()
    log.info(f"PR created: {pr_url}")
    return pr_url


# ---------------------------------------------------------------------------
# Claude Code invocation
# ---------------------------------------------------------------------------

CLAUDE_PROMPT_TEMPLATE = """You are fixing a screen agent navigation failure in an Android app. The screen agent
navigates app UIs using accessibility tree resource IDs and text patterns. The most
common cause of failure is that the target app (WhatsApp, Google Maps, etc.) was
updated and changed its UI — resource IDs got renamed, elements moved, or new screen
states appeared.

IMPORTANT: Your job is ONLY to fix failures caused by UI changes in the target app.
If the error looks like it's caused by something else (network issues, accessibility
service not running, app not installed, timing issues, etc.), do NOT make code changes.
Instead, create a file called SKIP_REASON.txt explaining why this isn't a UI change
issue, and exit.

## Error Details
- **Error tag (dump_reason)**: {dump_reason}
- **Error message**: {error_message}
- **App package**: {package_name}
- **Whiz app version**: {app_version}
- **Device**: {device_manufacturer} {device_model}, Android {android_version}
- **Screen dimensions**: {screen_width}x{screen_height}
- **Timestamp**: {created_at}

## Recent Agent Actions Before Failure
These are the last actions the screen agent took before the error occurred:
{recent_actions}

## Extra Debug Context
{screen_agent_context}

## Screenshot of Screen at Time of Failure
Read the file screenshot.jpg in the current directory to see what the screen looked
like when the error occurred. This gives you visual context for what the app's UI
looks like now.

## UI Hierarchy Dump at Time of Failure
This is the FULL accessibility tree dump captured at the same time as the screenshot.
Each line shows: [ClassName] id=resourceId text="..." desc="..." bounds=... clickable=...

```
{ui_hierarchy}
```

## Your Task

1. Search the codebase for "{dump_reason}" to find the exact code location where this
   error is raised.
2. Read the surrounding function to understand what resource IDs, text patterns, or
   screen states the code expects.
3. Compare against the UI hierarchy dump and screenshot above. Identify what changed
   in the target app's UI: renamed resource ID? Different element nesting? New screen
   state?
4. Make the MINIMAL fix. CRITICAL RULE: The fix MUST be backwards compatible with
   BOTH the old and new versions of the app being navigated (e.g., both old and new
   WhatsApp). Users may not have updated yet, so the code must handle both versions.

   - NEVER remove or replace existing selectors. Only ADD new ones alongside them.
   - If a resource ID changed, try the new ID first, fall back to the old:
     ```kotlin
     var nodes = rootNode.findAccessibilityNodeInfosByViewId("com.app:id/new_id")
     if (nodes.isNullOrEmpty()) {{
         nodes = rootNode.findAccessibilityNodeInfosByViewId("com.app:id/old_id")
     }}
     ```
   - If a content description changed, check for both old and new text.
   - If a new screen state appeared, add detection for it WITHOUT removing existing
     state detection — old app versions will still show the old states.
   - If element search strategy needs updating, add the new approach as the primary
     attempt and keep the old approach as a fallback.
5. Do NOT change code unrelated to this error.
6. If you determine the error is NOT caused by a UI change (e.g., it's a timing
   issue, network error, or missing accessibility service), make NO code changes.
   Instead, create a file called SKIP_REASON.txt explaining why.
7. Generate a verification test. Create the file `autofix_tests/test_autofix_verification.py`
   that exercises the specific screen agent feature you just fixed. The test should:

   - Import helpers from the `helpers` module in the same directory:
     ```python
     import time
     import subprocess
     import os
     from helpers import (
         check_element_exists_in_ui, save_failed_screenshot,
         navigate_to_my_chats, send_voice_command, EMULATOR_SERIAL,
     )
     ```
   - Use the `tester` fixture (provided by conftest.py) which gives you an
     AndroidAccessibilityTester connected to the emulator with the Whiz app open.
   - The `tester` object provides these methods directly:
     `tester.screenshot(path)`, `tester.tap(x, y)`, `tester.swipe(...)`,
     `tester.press_back()`, `tester.open_app(package)`, `tester.shell(cmd)`,
     `tester.validate_screenshot(path, description)` (uses Claude vision to check what's on screen),
     `tester.input_text(text)`, `tester.press_key(keycode)`.
   - The `helpers` module provides:
     `check_element_exists_in_ui(tester, content_desc=None, text=None, wait_after_dump=0.5)`,
     `save_failed_screenshot(tester, test_name, step_name)`,
     `navigate_to_my_chats(tester, test_name)` -> (success, error_msg),
     `send_voice_command(text)` - sends a test voice transcription to the Whiz app,
     `EMULATOR_SERIAL` (the adb serial for the emulator).
   - IMPORTANT: The test MUST exercise the actual screen agent feature on the emulator
     by sending a voice command that triggers the screen agent, then validating the result
     on screen. A test that only reads source code or runs a Gradle build is NOT acceptable.
   - Trigger the same user action that caused the original failure (e.g., send a
     voice command via the Whiz app that exercises the screen agent feature).
   - Verify the screen agent navigates successfully by checking for expected UI
     elements after the action completes.
   - Use `save_failed_screenshot(tester, "test_name", "step_name")` on failures.
   - Keep the test focused on just this one fix. Name the test function
     `test_autofix_{{dump_reason}}` (replacing non-alphanumeric chars with underscores).

   Example test structure:
   ```python
   def test_autofix_{{dump_reason_sanitized}}(tester):
       \"\"\"Verify fix for {{dump_reason}}.\"\"\"
       import time
       from helpers import navigate_to_my_chats, send_voice_command, save_failed_screenshot

       # Navigate to My Chats page first
       success, error = navigate_to_my_chats(tester, "autofix_verification")
       assert success, f"Could not reach My Chats: {{error}}"

       # Open new chat
       tester.tap(950, 2225)
       time.sleep(2)

       # Send a voice command that exercises the screen agent feature
       send_voice_command("what are the trader joes near me?")
       time.sleep(25)  # wait for screen agent to complete

       # Validate the result
       tester.screenshot("/tmp/whiz_screen.png")
       result = tester.validate_screenshot("/tmp/whiz_screen.png",
           "Google Maps is showing search results")
       if not result:
           save_failed_screenshot(tester, "autofix_verification", "validation_failed")
       assert result, "Screen agent did not produce expected result"
   ```

8. Run the verification test you wrote. Do NOT commit before running the test.
   Run: `ANDROID_SERIAL=emulator-5556 python -m pytest autofix_tests/test_autofix_verification.py -v`
   - If the test fails, read the error output, fix your code or the test, and re-run.
   - You may retry up to 3 times total.
   - Only commit after the test passes.
   - If the test still fails after 3 attempts, commit what you have and include
     "VERIFICATION TEST FAILED" in the commit message so reviewers know.
9. Commit your changes (including the test file)."""


CLAUDE_RETEST_PROMPT_TEMPLATE = """A previous autofix PR has a failing verification test. Your job is to fix the
code or the test so that the verification passes.

## Original PR Context
- **PR Title**: {pr_title}
- **PR Body**:
{pr_body}

## Test File Contents
```python
{test_file_contents}
```

## Test Failure Output
```
{test_failure_output}
```

## Test Infrastructure

The `autofix_tests/` directory has `conftest.py` and `helpers.py` that provide:

- **`tester` fixture** (from conftest.py): Creates an `AndroidAccessibilityTester` connected
  to the emulator with the Whiz debug app open, logged in, and accessibility service enabled.
  The tester provides: `tester.screenshot(path)`, `tester.tap(x, y)`, `tester.swipe(...)`,
  `tester.press_back()`, `tester.open_app(package)`, `tester.shell(cmd)`,
  `tester.validate_screenshot(path, description)` (uses Claude vision to check what's on screen),
  `tester.input_text(text)`, `tester.press_key(keycode)`.

- **`helpers` module**: Import from `helpers` (same directory):
  ```python
  from helpers import (
      check_element_exists_in_ui,  # check_element_exists_in_ui(tester, content_desc=None, text=None)
      save_failed_screenshot,       # save_failed_screenshot(tester, test_name, step_name)
      navigate_to_my_chats,         # navigate_to_my_chats(tester, test_name) -> (success, error_msg)
      send_voice_command,            # send_voice_command("text") - sends test voice transcription
      EMULATOR_SERIAL,
  )
  ```

- **IMPORTANT**: Tests MUST exercise the actual screen agent feature on the emulator by sending
  a voice command that triggers the screen agent, then validating the result. A test that only
  reads source code or runs a Gradle build is NOT a proper verification test.

Example test structure:
```python
def test_autofix_example(tester):
    from helpers import navigate_to_my_chats, send_voice_command, save_failed_screenshot

    # Navigate to My Chats page
    success, error = navigate_to_my_chats(tester, "autofix_example")
    assert success, f"Could not reach My Chats: {{error}}"

    # Open new chat
    tester.tap(950, 2225)
    time.sleep(2)

    # Send a voice command that exercises the screen agent feature
    send_voice_command("what are the trader joes near me?")
    time.sleep(25)  # wait for screen agent to complete

    # Validate the result
    tester.screenshot("/tmp/whiz_screen.png")
    result = tester.validate_screenshot("/tmp/whiz_screen.png",
        "Google Maps is showing search results for Trader Joe's locations")
    if not result:
        save_failed_screenshot(tester, "autofix_example", "validation_failed")
    assert result, "Screen agent did not produce expected result"
```

## Your Task

1. Read the test failure output carefully. Understand what's failing and why.
2. Read the code that was changed in this PR to understand the fix that was attempted.
3. Determine whether the issue is in the fix code or in the test itself.
4. If the test only does static source analysis or build checks (no emulator interaction),
   REWRITE it as a proper end-to-end test that sends a voice command and validates the result.
5. Make the MINIMAL changes needed. The same backwards-compatibility rules apply:
   - NEVER remove or replace existing selectors, only ADD new ones alongside them.
   - If a resource ID changed, try new first, fall back to old.
   - Keep the fix working for both old and new versions of the target app.
6. Run the test: `ANDROID_SERIAL=emulator-5556 python -m pytest autofix_tests/test_autofix_verification.py -v`
   - If it fails, read the error, fix, and re-run (up to 3 attempts total).
   - Only commit after the test passes.
   - If the test still fails after 3 attempts, commit what you have and include
     "VERIFICATION TEST FAILED" in the commit message.
7. Commit your changes."""


def build_prompt(dump: dict) -> str:
    """Build the Claude Code prompt from a dump row."""
    # Format recent actions
    recent_actions = dump.get("recent_actions")
    if isinstance(recent_actions, list):
        recent_actions_str = "\n".join(f"  - {a}" for a in recent_actions)
    elif recent_actions:
        recent_actions_str = str(recent_actions)
    else:
        recent_actions_str = "(none recorded)"

    # Format screen_agent_context (excluding screenshot which is passed separately)
    ctx = dump.get("screen_agent_context") or {}
    ctx_display = {k: v for k, v in ctx.items() if k != "screenshot_base64"}
    ctx_str = json.dumps(ctx_display, indent=2) if ctx_display else "(none)"

    return CLAUDE_PROMPT_TEMPLATE.format(
        dump_reason=dump["dump_reason"],
        error_message=dump.get("error_message") or "N/A",
        package_name=dump.get("package_name") or "unknown",
        app_version=dump.get("app_version") or "unknown",
        device_manufacturer=dump.get("device_manufacturer") or "unknown",
        device_model=dump.get("device_model") or "unknown",
        android_version=dump.get("android_version") or "unknown",
        screen_width=dump.get("screen_width") or "unknown",
        screen_height=dump.get("screen_height") or "unknown",
        created_at=dump.get("created_at") or "unknown",
        recent_actions=recent_actions_str,
        screen_agent_context=ctx_str,
        ui_hierarchy=dump.get("ui_hierarchy") or "(no hierarchy captured)",
    )


def save_screenshot(dump: dict, repo_path: str) -> bool:
    """Extract screenshot_base64 from context and save as screenshot.jpg. Returns True if saved."""
    ctx = dump.get("screen_agent_context") or {}
    b64 = ctx.get("screenshot_base64")
    if not b64:
        log.info("No screenshot available for this dump")
        return False

    try:
        img_bytes = base64.b64decode(b64)
        screenshot_path = os.path.join(repo_path, "screenshot.jpg")
        with open(screenshot_path, "wb") as f:
            f.write(img_bytes)
        log.info(f"Screenshot saved to {screenshot_path}")
        return True
    except Exception as e:
        log.warning(f"Failed to save screenshot: {e}")
        return False


def invoke_claude_code(repo_path: str, prompt: str) -> bool:
    """Run Claude Code CLI with the given prompt. Returns True if it succeeded."""
    log.info("Invoking Claude Code CLI...")
    env = os.environ.copy()
    env["ANDROID_SERIAL"] = EMULATOR_SERIAL
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--max-turns",
            "100",
            "--dangerously-skip-permissions",
            prompt,
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 minute timeout (test execution adds time)
        env=env,
    )

    if result.returncode != 0:
        log.error(f"Claude Code failed (exit {result.returncode})")
        if result.stderr:
            log.error(f"stderr: {result.stderr[:2000]}")
        if result.stdout:
            log.error(f"stdout: {result.stdout[:2000]}")
        return False

    log.info("Claude Code completed successfully")
    if result.stdout:
        # Log last 500 chars of output for debugging
        log.info(f"Claude output (tail): ...{result.stdout[-500:]}")
    return True


def check_skip_reason(repo_path: str) -> str | None:
    """Check if Claude created a SKIP_REASON.txt file."""
    skip_path = os.path.join(repo_path, "SKIP_REASON.txt")
    if os.path.exists(skip_path):
        with open(skip_path) as f:
            return f.read().strip()
    return None


def run_verification_test(repo_path: str) -> tuple[bool, str]:
    """Run the autofix verification test. Returns (passed, output)."""
    test_file = os.path.join(repo_path, "autofix_tests", "test_autofix_verification.py")
    if not os.path.exists(test_file):
        return False, "Test file not found"

    env = os.environ.copy()
    env["ANDROID_SERIAL"] = EMULATOR_SERIAL
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "autofix_tests/test_autofix_verification.py", "-v"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for test
            env=env,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Test timed out after 300s"


def cleanup_screenshot(repo_path: str):
    """Remove the temp screenshot file so it doesn't get committed."""
    screenshot_path = os.path.join(repo_path, "screenshot.jpg")
    if os.path.exists(screenshot_path):
        os.remove(screenshot_path)


def cleanup_skip_reason(repo_path: str):
    """Remove SKIP_REASON.txt so it doesn't get committed."""
    skip_path = os.path.join(repo_path, "SKIP_REASON.txt")
    if os.path.exists(skip_path):
        os.remove(skip_path)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def process_dump(supabase, dump: dict, repo_path: str) -> dict:
    """
    Process a single dump_reason error. Returns a result dict with status info.
    """
    dump_reason = dump["dump_reason"]
    result = {"dump_reason": dump_reason, "status": "unknown"}

    # Check for existing PR
    if pr_already_exists(dump_reason):
        log.info(f"PR already exists for '{dump_reason}', skipping")
        result["status"] = "skipped_pr_exists"
        return result

    # Reset to main before creating a new branch
    subprocess.run(
        ["git", "checkout", "main"], cwd=repo_path, check=True, capture_output=True
    )

    # Create branch
    branch = create_branch(repo_path, dump_reason)
    log.info(f"Created branch: {branch}")

    # Save screenshot
    has_screenshot = save_screenshot(dump, repo_path)

    # Build prompt and invoke Claude Code
    prompt = build_prompt(dump)

    try:
        success = invoke_claude_code(repo_path, prompt)
    except subprocess.TimeoutExpired:
        log.error(f"Claude Code timed out for '{dump_reason}'")
        result["status"] = "timeout"
        cleanup_screenshot(repo_path)
        return result

    # Always clean up the screenshot before committing
    cleanup_screenshot(repo_path)

    if not success:
        result["status"] = "claude_failed"
        return result

    # Check if Claude determined this isn't a UI change issue
    skip_reason = check_skip_reason(repo_path)
    if skip_reason:
        log.info(f"Claude skipped '{dump_reason}': {skip_reason}")
        cleanup_skip_reason(repo_path)
        mark_processed(supabase, dump_reason, dump["app_version"], None)
        result["status"] = "skipped_not_ui_change"
        result["skip_reason"] = skip_reason
        return result

    # Run verification test as a post-invocation sanity check
    test_passed, test_output = run_verification_test(repo_path)
    if test_passed:
        log.info(f"Verification test PASSED for '{dump_reason}'")
        test_result = "PASSED"
    else:
        log.warning(f"Verification test FAILED for '{dump_reason}'")
        log.warning(f"Test output (tail): ...{test_output[-500:]}")
        test_result = "FAILED"
    result["test_result"] = test_result

    # Commit and push
    has_changes = commit_and_push(repo_path, branch, dump_reason)
    if not has_changes:
        log.info(f"No changes for '{dump_reason}'")
        mark_processed(supabase, dump_reason, dump["app_version"], None)
        result["status"] = "no_changes"
        return result

    # Create PR
    pr_url = create_pr(
        repo_path, branch, dump_reason, dump.get("error_message"),
        test_result=test_result,
    )
    mark_processed(supabase, dump_reason, dump["app_version"], pr_url)
    result["status"] = "pr_created"
    result["pr_url"] = pr_url
    return result


def parse_pr_number(pr_ref: str) -> int:
    """Parse a PR number from a URL or plain number string."""
    # Handle URLs like https://github.com/whizvoice/whizvoiceapp/pull/42
    match = re.search(r"/pull/(\d+)", pr_ref)
    if match:
        return int(match.group(1))
    # Plain number
    try:
        return int(pr_ref)
    except ValueError:
        log.error(f"Cannot parse PR number from: {pr_ref}")
        sys.exit(1)


def test_pr_mode(pr_ref: str, repo_path: str):
    """
    Test (and fix) a previous autofix PR.
    Checks out the PR branch, runs tests, invokes Claude to fix on failure.
    """
    pr_number = parse_pr_number(pr_ref)
    log.info(f"Testing PR #{pr_number}")

    # Check out the PR branch
    subprocess.run(
        ["gh", "pr", "checkout", str(pr_number)],
        cwd=repo_path, check=True,
    )

    # Boot emulator
    if not boot_emulator(repo_path):
        log.error("Failed to boot emulator")
        sys.exit(1)

    try:
        # Run the verification test
        test_passed, test_output = run_verification_test(repo_path)

        if test_passed:
            log.info(f"PR #{pr_number} verification test PASSED")
            return

        log.warning(f"PR #{pr_number} verification test FAILED, invoking Claude to fix...")

        # Get PR context
        pr_view = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title,body"],
            cwd=repo_path, capture_output=True, text=True,
        )
        pr_info = json.loads(pr_view.stdout) if pr_view.returncode == 0 else {}
        pr_title = pr_info.get("title", f"PR #{pr_number}")
        pr_body = pr_info.get("body", "(no body)")

        # Read test file contents
        test_file_path = os.path.join(repo_path, "autofix_tests", "test_autofix_verification.py")
        test_file_contents = "(test file not found)"
        if os.path.exists(test_file_path):
            with open(test_file_path) as f:
                test_file_contents = f.read()

        # Build retest prompt
        prompt = CLAUDE_RETEST_PROMPT_TEMPLATE.format(
            pr_title=pr_title,
            pr_body=pr_body,
            test_file_contents=test_file_contents,
            test_failure_output=test_output[-3000:],  # Last 3000 chars
        )

        try:
            success = invoke_claude_code(repo_path, prompt)
        except subprocess.TimeoutExpired:
            log.error("Claude Code timed out during retest fix")
            return

        if not success:
            log.error("Claude Code failed during retest fix")
            return

        # Re-run verification test
        test_passed, test_output = run_verification_test(repo_path)
        test_result = "PASSED" if test_passed else "FAILED"
        log.info(f"Post-fix verification test: {test_result}")

        # Check if Claude committed changes; if not, commit them
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path, capture_output=True, text=True,
        )
        if status.stdout.strip():
            subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
            subprocess.run(
                ["git", "commit", "-m",
                 f"autofix: fix verification test for PR #{pr_number}\n\n"
                 f"Verification test: {test_result}"],
                cwd=repo_path, check=True,
            )
            subprocess.run(["git", "push"], cwd=repo_path, check=True)
            log.info(f"Pushed fix commits to PR #{pr_number}")

        # Update PR body with test result
        current_body = pr_body
        updated_body = current_body.rstrip() + f"\n\n---\n**Re-test result**: {test_result}\n"
        subprocess.run(
            ["gh", "pr", "edit", str(pr_number), "--body", updated_body],
            cwd=repo_path, check=True,
        )
        log.info(f"Updated PR #{pr_number} body with test result: {test_result}")

    finally:
        shutdown_emulator()


def main():
    parser = argparse.ArgumentParser(
        description="Auto-fix screen agent UI navigation failures"
    )
    parser.add_argument(
        "--whizvoiceapp-path",
        help="Path to an existing whizvoiceapp checkout (clones if not provided)",
    )
    parser.add_argument(
        "--dump-reason",
        help="Only process this specific dump_reason",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be processed without making changes",
    )
    parser.add_argument(
        "--test-pr",
        metavar="URL_OR_NUMBER",
        help="Test (and fix) a previous autofix PR instead of running the normal pipeline",
    )
    parser.add_argument(
        "--skip-emulator-boot",
        action="store_true",
        help="Skip emulator boot (assumes emulator is already running)",
    )
    args = parser.parse_args()

    # Handle --test-pr mode (skips normal pipeline)
    if args.test_pr:
        repo_path = ensure_whizvoiceapp(args.whizvoiceapp_path)
        test_pr_mode(args.test_pr, repo_path)
        return

    supabase = load_supabase()

    # Find latest app version
    latest_version = get_latest_app_version(supabase)
    if not latest_version:
        log.info("No UI dumps found in database")
        return

    log.info(f"Latest app version: {latest_version}")

    # Get unprocessed errors
    errors = get_unprocessed_errors(supabase, latest_version, args.dump_reason)
    if not errors:
        log.info("No unprocessed errors to fix")
        return

    log.info(f"Found {len(errors)} unique dump_reason(s) to process")

    if args.dry_run:
        for err in errors:
            log.info(
                f"  Would process: {err['dump_reason']} "
                f"(error: {err.get('error_message', 'N/A')[:80]})"
            )
        return

    # Ensure whizvoiceapp checkout
    repo_path = ensure_whizvoiceapp(args.whizvoiceapp_path)

    # Boot emulator (unless skipped)
    if not args.skip_emulator_boot:
        if not boot_emulator(repo_path):
            log.error("Failed to boot emulator, aborting")
            sys.exit(1)
    log.info(f"Using whizvoiceapp at: {repo_path}")

    try:
        # Process each error
        results = []
        for dump in errors:
            log.info(f"\n{'='*60}")
            log.info(f"Processing: {dump['dump_reason']}")
            log.info(f"Error: {dump.get('error_message', 'N/A')[:100]}")
            log.info(f"{'='*60}")

            try:
                result = process_dump(supabase, dump, repo_path)
                results.append(result)
                log.info(f"Result: {result['status']}")
            except Exception as e:
                log.error(f"Failed to process '{dump['dump_reason']}': {e}", exc_info=True)
                results.append(
                    {"dump_reason": dump["dump_reason"], "status": "error", "error": str(e)}
                )

        # Summary
        log.info(f"\n{'='*60}")
        log.info("SUMMARY")
        log.info(f"{'='*60}")
        for r in results:
            status = r["status"]
            reason = r["dump_reason"]
            extra = r.get("pr_url") or r.get("skip_reason") or r.get("error") or ""
            log.info(f"  [{status}] {reason} {extra}")
    finally:
        if not args.skip_emulator_boot:
            shutdown_emulator()


if __name__ == "__main__":
    main()
