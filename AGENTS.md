# gtestcov Agent Instructions

## Project-Specific Evidence Rules

1. The generic layer must stay generic. It may rely on stable C/C++ behavior, GTest/GMock rules, coverage parsing, CLI/MCP flow, and gtestcov's own guardrails.
2. Do not infer project-specific facts from file-name habits, open-source project impressions, or previous guesses. This includes project root meaning, build entry points, test directories, fake/harness/support paths, business module boundaries, dependency names, platform APIs, and framework structure.
3. Project-specific facts must come from one of these sources:
   - user input;
   - `project_profile.yaml`;
   - CODRAX `file:line` evidence;
   - traceable gtestcov artifacts.
4. `--project-root` is the user-selected gtestcov workspace boundary. It does not need to contain `.git`; it may be a `.repo` workspace, monorepo subtree, source archive, or another selected source workspace.
5. If evidence is missing, conflicting, or outside the selected project boundary, stop and write `manual_review_needed.md` instead of guessing.
6. If a production/business source change is required, write `source_change_request.md` and do not edit production logic directly.
7. Weak AI must treat `.gtestcov/runs/<run_id>/handoff.*`, `.gtestcov/runs/<run_id>/resume_prompt.md`, `.gtestcov/runs/<run_id>/verify.json`, `.gtestcov/runs/<run_id>/coverage_history.json`, and `.gtestcov/memory/project_memory.*` as tool-generated read-only state.
