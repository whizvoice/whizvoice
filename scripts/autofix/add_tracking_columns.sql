-- Add tracking columns for the auto-fix pipeline.
-- processed_at: when the auto-fix pipeline processed this dump
-- autofix_pr_url: URL of the PR created by the auto-fix pipeline (if any)

ALTER TABLE screen_agent_ui_dumps ADD COLUMN IF NOT EXISTS processed_at timestamptz DEFAULT NULL;
ALTER TABLE screen_agent_ui_dumps ADD COLUMN IF NOT EXISTS autofix_pr_url text DEFAULT NULL;
