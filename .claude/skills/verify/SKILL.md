---
name: verify
description: Run the full quality check suite (format, lint, type-check, 100% test coverage). Use after making code changes to confirm everything passes before finishing.
---

Run `make check` from the project root and report the results.

If any step fails:
1. Show the exact error output.
2. Fix the issue.
3. Re-run `make check` until it passes.

Do not mark the task done until `make check` exits with code 0.
