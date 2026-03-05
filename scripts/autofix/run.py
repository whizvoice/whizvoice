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

Environment variables:
    SUPABASE_URL          - Supabase project URL
    SUPABASE_SERVICE_ROLE - Supabase service role key
    ANTHROPIC_API_KEY     - Anthropic API key for Claude Code
    WHIZVOICEAPP_REPO     - (optional) Git repo URL, default: git@github.com:whizvoice/whizvoiceapp.git
"""

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
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


def create_pr(repo_path: str, branch: str, dump_reason: str, error_message: str | None) -> str | None:
    """Create a PR via gh CLI. Returns the PR URL or None."""
    title = f"autofix: {dump_reason}"[:70]
    body = (
        f"## Auto-Fix: Screen Agent Navigation Failure\n\n"
        f"**Error tag**: `{dump_reason}`\n"
        f"**Error message**: {error_message or 'N/A'}\n\n"
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
        timeout=600,  # 10 minute timeout
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

    # Commit and push
    has_changes = commit_and_push(repo_path, branch, dump_reason)
    if not has_changes:
        log.info(f"No changes for '{dump_reason}'")
        mark_processed(supabase, dump_reason, dump["app_version"], None)
        result["status"] = "no_changes"
        return result

    # Create PR
    pr_url = create_pr(
        repo_path, branch, dump_reason, dump.get("error_message")
    )
    mark_processed(supabase, dump_reason, dump["app_version"], pr_url)
    result["status"] = "pr_created"
    result["pr_url"] = pr_url
    return result


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
    args = parser.parse_args()

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
    log.info(f"Using whizvoiceapp at: {repo_path}")

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


if __name__ == "__main__":
    main()
