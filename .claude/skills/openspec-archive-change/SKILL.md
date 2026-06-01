---
name: openspec-archive-change
description: Archive a completed change in the experimental workflow. Use when the user wants to finalize and archive a change after implementation is complete.
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.3.1"
---

Archive a completed change in the experimental workflow.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **If no change name provided, prompt for selection**

   Run `openspec list --json` to get available changes. Use the **AskUserQuestion tool** to let the user select.

   Show only active changes (not already archived).
   Include the schema used for each change if available.

   **IMPORTANT**: Do NOT guess or auto-select a change. Always let the user choose.

2. **Check artifact completion status**

   Run `openspec status --change "<name>" --json` to check artifact completion.

   Parse the JSON to understand:
   - `schemaName`: The workflow being used
   - `artifacts`: List of artifacts with their status (`done` or other)

   **If any artifacts are not `done`:**
   - Display warning listing incomplete artifacts
   - Use **AskUserQuestion tool** to confirm user wants to proceed
   - Proceed if user confirms

3. **Check task completion status**

   Read the tasks file (typically `tasks.md`) to check for incomplete tasks.

   Count tasks marked with `- [ ]` (incomplete) vs `- [x]` (complete).

   **If incomplete tasks found:**
   - Display warning showing count of incomplete tasks
   - Use **AskUserQuestion tool** to confirm user wants to proceed
   - Proceed if user confirms

   **If no tasks file exists:** Proceed without task-related warning.

4. **Assess delta spec state**

   Check for delta specs at `openspec/changes/<name>/specs/`. If none exist, note "No delta specs" for the summary.

   **If delta specs exist:**
   - Compare each delta spec with its corresponding main spec at `openspec/specs/<capability>/spec.md`
   - Determine what changes will be applied (adds, modifications, removals, renames)
   - Show a combined summary

   This is **informational only** — the merge into main specs is performed programmatically by `openspec archive` in the next step. Do NOT invoke the agent-driven `openspec-sync-specs` skill here; the CLI handles the delta format natively and is far faster. (Use `openspec-sync-specs` only for standalone mid-flight syncing outside of archiving.)

5. **Perform the archive**

   Run the openspec CLI. It merges delta specs into main specs programmatically, validates, and moves the change to `openspec/changes/archive/YYYY-MM-DD-<name>`:

   ```bash
   openspec archive <name> -y
   ```

   For infrastructure/tooling/doc-only changes with no specs to merge, add `--skip-specs`.

   If the command fails because the target archive directory already exists, suggest renaming the existing archive or using a different date.

6. **Display summary**

   Show archive completion summary including:
   - Change name
   - Schema that was used
   - Archive location
   - Whether specs were synced (if applicable)
   - Note about any warnings (incomplete artifacts/tasks)

**Output On Success**

```
## Archive Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Archived to:** openspec/changes/archive/YYYY-MM-DD-<name>/
**Specs:** ✓ Synced to main specs (or "No delta specs" or "Sync skipped")

All artifacts complete. All tasks complete.
```

**Guardrails**
- Always prompt for change selection if not provided
- Use artifact graph (openspec status --json) for completion checking
- Don't block archive on warnings - just inform and confirm
- Show clear summary of what happened
- Let `openspec archive` perform both the spec merge and the directory move — do not mkdir/mv by hand, and do not pre-sync with the agent-driven `openspec-sync-specs` skill.
