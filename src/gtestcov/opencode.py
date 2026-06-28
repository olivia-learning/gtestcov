from __future__ import annotations

import json
from pathlib import Path


OPENCODE_COMMAND = """---
description: Generate gtest coverage safely with gtestcov
agent: build
---

Use the `gtestcov` MCP tools for target `$ARGUMENTS`.

Required flow:

1. Treat `$ARGUMENTS` as one target file, the requested target-file line coverage, and an optional user build-file anchor.
2. Call `gtestcov_cover_target`; pass the build-file anchor when supplied. This syncs profile data through CODRAX, compares CODRAX build-file candidates with the user anchor, and builds the task package only when safe.
3. Read `.gtestcov/runs/<run_id>/resume_prompt.md`, `.gtestcov/runs/<run_id>/handoff.md`, and `.gtestcov/memory/project_memory.md`. Repeat this step after any context refresh or compression.
4. Read `.gtestcov/runs/<run_id>/opencode_permission_warmup.md` and concentrate OpenCode file permission prompts near the start: open/read listed evidence and build files, and request edit permission only for needed allowed test-side paths.
5. Read the task package, decision report, CODRAX project understanding, and Test Obligation Matrix.
6. Implement every task-package obligation with status `ready`; map generated tests back to obligation IDs.
7. Before every added or modified `TEST`, `TEST_F`, or `TEST_P`, write one block comment with `Test Case`, `Value`, `Steps`, `Inputs`, and `Expected Outputs`. In `Value`, describe what real behavior is tested and why it matters; only say `coverage-only, low business value` when that is genuinely the main purpose.
8. Edit only tests, test support, and test build configuration paths allowed in the task package.
9. Do not edit the target file or production/business logic source. If a production change is required, write `.gtestcov/runs/<run_id>/source_change_request.md` and stop.
10. Do not edit tool-generated gtestcov state such as `handoff.*`, `project_memory.*`, `verify.json`, or `coverage_history.json`.
11. If evidence is insufficient, write `.gtestcov/runs/<run_id>/manual_review_needed.md` and stop.
12. If direct CODRAX mode is enabled, every direct CODRAX query must be logged to `.gtestcov/runs/<run_id>/codrax_direct_log.md` with file:line evidence.
13. Call `gtestcov_preflight_check` before any build/test/coverage command.
14. If preflight fails, read `.gtestcov/runs/<run_id>/preflight_fix_task.md`, fix only test-side or test-build-config issues, and run preflight again.
15. Call `gtestcov_verify_iteration` only after preflight passes.
16. If verification fails because coverage is below target, read `.gtestcov/runs/<run_id>/next_task.md` and continue the next iteration.
17. If `.gtestcov/runs/<run_id>/stagnation_report.md` exists, stop editing and ask the user to review it.
18. If build/test/coverage commands fail, call `gtestcov_diagnose_failure`, then fix only test-side or test-build-config issues until verification passes or a stagnation report is produced.

Never skip the decision report. Never generate macros forbidden by the active profile. Never invent dependency structs, enums, macros, empty allocator/free shims, or project-specific facts not backed by user input, project_profile.yaml, CODRAX file:line evidence, or gtestcov artifacts.
"""


OPENCODE_INSTALL_COMMAND = """---
description: Install gtestcov for an OpenCode workstation
agent: build
---

Use `docs/install.md` from the gtestcov tool repository as the single source of truth.

Required behavior:

1. Ask the user only for install mode, tool directory, C++ project root, and branch/tag or zip path.
2. Follow the "First Install" section in `docs/install.md`.
3. Reuse an existing gtestcov virtual environment when one exists; create it only on first install.
4. Run `gtestcov version`, `gtestcov install doctor`, `gtestcov init`, and `gtestcov codrax-check`.
5. Write `<project-root>/.gtestcov/install_report.md`.

Do not duplicate install rules here. Do not change system network, drivers, registry, WSL installation, Hyper-V, services, or virtualization settings. If the requested tool directory is not empty, stop and report before changing it.
"""


OPENCODE_UPGRADE_COMMAND = """---
description: Upgrade gtestcov with old-version report, A/B slots, and rollback
agent: build
---

Use `docs/install.md` from the gtestcov tool repository as the single source of truth.

Required behavior:

1. Follow the "Upgrade" section in `docs/install.md`.
2. Always run `gtestcov upgrade inspect` first.
3. Open and summarize `old_version_detection_report.md` for the user, then stop for approval.
4. Only after approval, run `gtestcov upgrade apply --upgrade-id <id> --approve-overwrite-tool-modifications`.
5. Check the returned `venv_refresh` field. If it says `needs_ai_action`, pass `--venv <existing-gtestcov-venv>` and continue; do not ask the user to type pip commands.
6. For rollback, follow the "A/B Slots And Rollback" section in `docs/install.md` and require explicit user approval.

Do not duplicate upgrade rules here. Do not carry old tool-source edits into the new version automatically. Do not delete or recreate the Python virtual environment unless the user approves that specific repair. Do not touch OS-level settings, WSL installation, network, drivers, registry, services, or virtualization settings.
"""


def write_opencode_files(project_root: Path, overwrite: bool = False) -> dict[str, str]:
    command_dir = project_root / ".opencode" / "commands"
    command_dir.mkdir(parents=True, exist_ok=True)
    command_path = command_dir / "gtest-cover.md"
    if overwrite or not command_path.exists():
        command_path.write_text(OPENCODE_COMMAND, encoding="utf-8")
    install_path = command_dir / "gtestcov-install.md"
    if overwrite or not install_path.exists():
        install_path.write_text(OPENCODE_INSTALL_COMMAND, encoding="utf-8")
    upgrade_path = command_dir / "gtestcov-upgrade.md"
    if overwrite or not upgrade_path.exists():
        upgrade_path.write_text(OPENCODE_UPGRADE_COMMAND, encoding="utf-8")

    gtestcov_dir = project_root / ".gtestcov"
    gtestcov_dir.mkdir(parents=True, exist_ok=True)
    snippet_path = gtestcov_dir / "opencode_mcp_snippet.jsonc"
    snippet = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "gtestcov": {
                "type": "local",
                "command": ["gtestcov", "mcp"],
                "enabled": True,
            }
        },
    }
    if overwrite or not snippet_path.exists():
        snippet_path.write_text(json.dumps(snippet, indent=2), encoding="utf-8")

    return {
        "command": str(command_path),
        "install_command": str(install_path),
        "upgrade_command": str(upgrade_path),
        "mcp_snippet": str(snippet_path),
    }
