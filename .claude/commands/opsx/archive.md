---
name: "OPSX: Archive"
description: Archive a completed change in the experimental workflow
category: Workflow
tags: [workflow, archive, experimental]
---

Archive a completed change in the experimental workflow.

**Input**: Optionally specify a change name after `/opsx:archive` (e.g., `/opsx:archive add-auth`). If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

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
   - Prompt user for confirmation to continue
   - Proceed if user confirms

3. **Check task completion status**

   Read the tasks file (typically `tasks.md`) to check for incomplete tasks.

   Count tasks marked with `- [ ]` (incomplete) vs `- [x]` (complete).

   **If incomplete tasks found:**
   - Display warning showing count of incomplete tasks
   - Prompt user for confirmation to continue
   - Proceed if user confirms

   **If no tasks file exists:** Proceed without task-related warning.

4. **Assess and sync delta spec state**

   Check for delta specs at `openspec/changes/<name>/specs/`. If none exist, proceed without syncing.

   **If delta specs exist:**
   - Compare each delta spec with its corresponding main spec at `openspec/specs/<capability>/spec.md`
   - Determine what changes would be applied (adds, modifications, removals, renames)
   - Show a combined summary

   **Always sync without prompting** — do NOT ask the user for confirmation. Syncing delta specs before archiving is the established project default.
   - If changes are needed: use the Task tool (subagent_type: "general-purpose", prompt: "Use Skill tool to invoke openspec-sync-specs for change '<name>'. Delta spec analysis: <include the analyzed delta spec summary>"). Then proceed to archive.
   - If already synced: note it and proceed to archive.

5. **Perform the archive**

   Create the archive directory if it doesn't exist:
   ```bash
   mkdir -p openspec/changes/archive
   ```

   Generate target name using current date: `YYYY-MM-DD-<change-name>`

   **Check if target already exists:**
   - If yes: Fail with error, suggest renaming existing archive or using different date
   - If no: Move the change directory to archive

   ```bash
   mv openspec/changes/<name> openspec/changes/archive/YYYY-MM-DD-<name>
   ```

6. **Auto-commit the archive**

   - Run `git status` and `git diff --staged` + `git diff` to see what will be committed.
   - If there is nothing to commit, skip this step and report "Nothing to commit".
   - Otherwise stage the relevant changed/untracked files explicitly by path (never `git add -A`/`.`; skip `.env`, secrets, large binaries). Include the archive move, any synced spec changes, and pending implementation changes for this change.
   - Create a single commit using the standard project format with a HEREDOC body, ending with the `Co-Authored-By` trailer required by the global commit protocol. Subject should reference the change name, e.g. `<change-name>: archive`.
   - Do NOT use `--no-verify` or `--amend`. If the pre-commit hook fails, report the failure and stop — do not retry blindly.
   - Do NOT ask the user for confirmation.

7. **Push to the remote**

   - Only if a commit was created in step 6. If step 6 reported "Nothing to commit", skip this step.
   - Determine the current branch (`git rev-parse --abbrev-ref HEAD`) and its upstream.
   - If an upstream is configured, run `git push`. If not, run `git push -u origin <current-branch>`.
   - Do NOT use `--force` or `--no-verify`. If the push fails (e.g. non-fast-forward, no remote, auth error), report the failure verbatim and stop — do not retry blindly or force-push.
   - Do NOT ask the user for confirmation.

8. **Display summary**

   Show archive completion summary including:
   - Change name
   - Schema that was used
   - Archive location
   - Spec sync status (synced / sync skipped / no delta specs)
   - Commit and push status (committed + pushed / nothing to commit / push failed)
   - Note about any warnings (incomplete artifacts/tasks)

**Output On Success**

```
## Archive Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Archived to:** openspec/changes/archive/YYYY-MM-DD-<name>/
**Specs:** ✓ Synced to main specs

All artifacts complete. All tasks complete.
```

**Output On Success (No Delta Specs)**

```
## Archive Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Archived to:** openspec/changes/archive/YYYY-MM-DD-<name>/
**Specs:** No delta specs

All artifacts complete. All tasks complete.
```

**Output On Success With Warnings**

```
## Archive Complete (with warnings)

**Change:** <change-name>
**Schema:** <schema-name>
**Archived to:** openspec/changes/archive/YYYY-MM-DD-<name>/
**Specs:** Sync skipped (user chose to skip)

**Warnings:**
- Archived with 2 incomplete artifacts
- Archived with 3 incomplete tasks
- Delta spec sync was skipped (user chose to skip)

Review the archive if this was not intentional.
```

**Output On Error (Archive Exists)**

```
## Archive Failed

**Change:** <change-name>
**Target:** openspec/changes/archive/YYYY-MM-DD-<name>/

Target archive directory already exists.

**Options:**
1. Rename the existing archive
2. Delete the existing archive if it's a duplicate
3. Wait until a different date to archive
```

**Guardrails**
- Always prompt for change selection if not provided
- Use artifact graph (openspec status --json) for completion checking
- Don't block archive on warnings - just inform and confirm
- Preserve .openspec.yaml when moving to archive (it moves with the directory)
- Show clear summary of what happened
- If delta specs exist, always sync them before archiving — invoke `openspec-sync-specs` (agent-driven) automatically, without prompting the user. Show the combined summary, then sync.
- After the archive commit, push to the remote automatically (no confirmation). Never force-push or use `--no-verify`; if the push fails, report it verbatim and stop.
