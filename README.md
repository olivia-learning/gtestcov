# gtestcov

`gtestcov` is a Python CLI and MCP server for guiding weak AI agents, such as
OpenCode using MiniMax, through embedded C++ GoogleTest generation and coverage
improvement.

The tool follows `codex_embedded_cpp_gtest_final_guide.md`:

1. accept one target file, a target-file line coverage goal, and a user build-file anchor when supplied,
2. route project-specific build/test/coverage questions through the configured evidence backend,
3. collect target understanding through `gtestcov`'s CODRAX-backed evidence layer,
4. build a constrained task package for OpenCode,
5. preflight-check generated edits before compiling,
6. verify focused build, filtered test, target coverage, and coverage-loop progress.

## Which Document Should I Read?

- Install, upgrade, rollback, or let an AI install the tool: `docs/install.md`
- Repository and generated project directory structure: `docs/project_structure.md`
- Plain-language workflow diagram: `docs/gtestcov_workflow.md`
- Editable diagrams.net / draw.io workflow: `docs/gtestcov_workflow.drawio`

## Quick Start

```bash
python -m pip install -e .[dev]
gtestcov init --project-root /path/to/cpp/project
gtestcov cover --project-root /path/to/cpp/project --target <target-file> --line-coverage 80 --build-file <build-entry-file>
gtestcov check --project-root /path/to/cpp/project --run-id latest --target <target-file>
gtestcov verify --project-root /path/to/cpp/project --run-id latest
gtestcov next-round --project-root /path/to/cpp/project --run-id latest
gtestcov memory-show --project-root /path/to/cpp/project --run-id latest
```

## Version, Install, Upgrade, And Rollback

Use `docs/install.md` as the single source of truth for installation,
AI-assisted installation, upgrade, restore-custom, and rollback.

### Versioning Policy

`gtestcov` uses SemVer-style tool versions: `MAJOR.MINOR.PATCH`.

- `PATCH` is for compatible bug fixes.
- `MINOR` is for compatible feature additions.
- `MAJOR` is for breaking CLI, profile, memory, task, or upgrade behavior.

While the tool is still in the `0.x` stage, minor releases may move quickly, so
every upgrade must still start with `gtestcov upgrade inspect`. Git tags use
`vX.Y.Z`; release zip files use `gtestcov-vX.Y.Z.zip`.

The tool version and generated-state schema versions are separate. For example,
`gtestcov version` reports both `version` and `memory_schema_version`. The
memory schema changes only when old `.gtestcov` state requires migration; adding
compatible fields does not require a schema bump.

`pyproject.toml` is the main tool-version source. `version.py` keeps a matching
fallback only for damaged or unusual runtime environments.

Core commands:

```bash
gtestcov version
gtestcov install doctor --project-root /path/to/cpp/project
gtestcov upgrade inspect --tool-root /path/to/gtestcov --project-root /path/to/cpp/project --target-ref <tag-or-branch> --install-mode zip|git
gtestcov upgrade apply --upgrade-id <id> --project-root /path/to/cpp/project --approve-overwrite-tool-modifications
gtestcov rollback list --project-root /path/to/cpp/project
gtestcov rollback apply --upgrade-id <id> --project-root /path/to/cpp/project --approve
```

Upgrades must start with `upgrade inspect`, which writes an old-version
detection report for user review. `upgrade apply` refuses to run without the
explicit approval flag. The tool uses A/B slots for old and new tool copies and
keeps project `.gtestcov` snapshots so rollback is possible. The Python virtual
environment should be reused across installs and upgrades; do not recreate it
unless the user approves a repair. After an A/B slot switch, `upgrade apply`
and `rollback apply` automatically attempt to refresh the reused venv entry
point with `--no-deps`; if the tool cannot detect the venv, the AI installer
should pass `--venv` and continue without asking the user to run pip manually.

## Workflow Diagram

For a plain-language workflow diagram:

- Editable diagrams.net / draw.io file: `docs/gtestcov_workflow.drawio`
- Mermaid Markdown version: `docs/gtestcov_workflow.md`

Open the `.drawio` file with <https://app.diagrams.net/> when you want to view
or adjust the diagram visually.

## Project Boundary And Evidence Policy

`--project-root` is the gtestcov workspace boundary. It does not need to be a
`.git` root: it can be a `.repo` multi-repository workspace, a monorepo subtree,
a source archive, or another user-selected source workspace. `project_profile.yaml`,
`.gtestcov/`, run handoff files, and project memory are stored under this root.

The generic layer is deliberately narrow. It may rely on stable C/C++ behavior,
GTest/GMock rules, coverage parsing, CLI/MCP flow, and gtestcov's own guardrails.
Project-specific facts such as build entry points, test directories,
fake/harness/support paths, module boundaries, platform APIs, and workspace-root
meaning must come from user input, `project_profile.yaml`, CODRAX `file:line`
evidence, or traceable gtestcov artifacts. If evidence is missing or conflicting,
the tool writes `manual_review_needed.md` instead of asking the weak AI to guess.

## CODRAX Evidence Backend

For a real embedded project, `gtestcov` uses a local CODRAX CLI as the targeted,
read-only evidence backend before it builds the OpenCode task package. CODRAX
substantially improves a weak AI agent's ability to understand real project
code, while `gtestcov` remains the control layer: it chooses the questions,
checks `file:line` citations, trims the evidence, updates the profile, and
writes the decision report and task package.

Project-specific understanding should flow through this `gtestcov` evidence
layer. The weak AI should not call CODRAX directly by default; it receives only
the structured evidence that `gtestcov` curated for the current target.

Configure the CODRAX backend in `project_profile.yaml`:

```yaml
evidence:
  codrax:
    enabled: true
    command: codrax
    invocation: auto
    model_policy: self_hosted_ok
    max_context: targeted
    require_file_line: true
    idle_timeout_seconds: 300
    max_runtime_seconds: 7200
    live_log_max_bytes: 10485760
    live_log_keep_tail_bytes: 1048576
```

Then check the integration and collect evidence without generating tests:

```bash
gtestcov codrax-check --project-root .
gtestcov profile-sync --project-root . --target <target-file> --line-coverage 80 --build-file <build-entry-file>
gtestcov cover --project-root . --target <target-file> --line-coverage 80 --build-file <build-entry-file> --run-id coverage-run
gtestcov evidence --project-root . --target <target-file> --run-id evidence-smoke
gtestcov obligations --project-root . --target <target-file> --run-id obligations-smoke
gtestcov analyze --project-root . --target <target-file> --run-id analyze-smoke
```

When CODRAX is unavailable, times out, returns an error, or omits required
`file:line` citations, `analyze` does not create a project-specific weak-AI task.
It records the CODRAX status, keeps generic C++/GTest static signals only as
diagnostics, and writes manual review material instead of asking the weak AI to
guess project details. Project-specific adapters do not activate without
CODRAX-cited evidence.

CODRAX execution is streamed into `.gtestcov/runs/<run_id>/codrax_live.log`.
The idle timeout stops only when CODRAX produces no output for the configured
activity window, while the max runtime is a hard ceiling for a long analysis.
The live log is bounded: after it reaches `live_log_max_bytes`, `gtestcov` keeps
the newest `live_log_keep_tail_bytes` and records that older output was dropped.
Curated CODRAX evidence remains the source of project facts; the live log is
diagnostic material.

`profile-sync` uses the CODRAX evidence backend to find project-specific build,
test, coverage, and test-support paths. It
automatically updates `project_profile.yaml`, backs up the previous profile
under `.gtestcov/profile_backups/`, and records cited field-level evidence in
`.gtestcov/runs/<run_id>/profile_evidence.md`.
If `--build-file` is provided, it is treated as the user's build-file anchor.
CODRAX still reports all plausible build/test configuration files through
`build.candidate_build_files`; `profile-sync` compares them with the user anchor.
If CODRAX-cited candidates do not include the user build file, the profile is
not updated and `manual_review_needed.md` is written.

`cover` is the preferred single-file workflow. It runs `profile-sync`, writes a
single-target coverage goal, and builds an OpenCode task package. Weak AI may
edit tests, test support, and cited test build configuration paths; it must not
edit the target file or production/business logic source directly.

`cover` also writes an OpenCode permission warmup manifest:

```text
.gtestcov/runs/<run_id>/opencode_permission_warmup.json
.gtestcov/runs/<run_id>/opencode_permission_warmup.md
```

OpenCode should read this immediately after task creation to concentrate file
permission prompts near the start of the run. The manifest lists files to
open/read first, planned test-side files that may need edit permission, allowed
write paths, and forbidden write targets such as the target file and production
source. This does not make the flow fully automatic, but it reduces later
piecemeal user involvement.

## Context Memory Layer

`gtestcov` writes context memory so a weak AI agent can resume safely after
context refresh or compression. This is not free-form AI memory; it is generated
from profile data, CODRAX evidence, decision reports, preflight results, verify
results, coverage history, next-round tasks, and diagnosis reports.

Run-scoped memory lives under the active run:

```text
.gtestcov/runs/<run_id>/handoff.json
.gtestcov/runs/<run_id>/handoff.md
.gtestcov/runs/<run_id>/resume_prompt.md
```

Project-scoped memory lives under the selected `--project-root`:

```text
.gtestcov/memory/project_memory.json
.gtestcov/memory/project_memory.md
```

The weak AI should read `resume_prompt.md`, `handoff.md`, and
`project_memory.md` at the start of work and after any context refresh. These
files are read-only for the weak AI. It should only influence future memory by
writing controlled artifacts such as `modified_files.txt`, `review_checklist.md`,
`manual_review_needed.md`, `source_change_request.md`, or `codrax_direct_log.md`.

Memory can be refreshed or inspected explicitly:

```bash
gtestcov memory-refresh --project-root . --run-id latest
gtestcov memory-show --project-root . --run-id latest --format md
gtestcov memory-show --project-root . --run-id latest --format json
```

`check` is the preflight gate after weak AI edits and before expensive
build/test/coverage commands. It audits write scope, forbidden generated-test
patterns, required per-test-case descriptions, direct CODRAX audit logs, and
CODRAX-cited compile blockers when evidence is available.
If it fails, it writes `preflight_check.json`, `preflight_check.md`, and
`preflight_fix_task.md`; `verify` will then skip build/test/coverage until the
preflight issues are fixed.

Each added or modified gtest case must include a short comment block immediately
before `TEST`, `TEST_F`, or `TEST_P`:

```cpp
/*
Test Case: <TestSuite>.<TestName>
Value: Exercises <real target behavior> and verifies <observable outcome>, which protects <why this matters>.
Steps: Arrange required fakes or inputs, call the target entry point, then inspect observable state or fake interactions.
Inputs: <important input values, fake return values, dependency states, messages, or configuration>.
Expected Outputs: <asserted return value, state change, fake interaction, message, or error>.
*/
```

Coverage-only and low business value cases are allowed when needed to reach the
coverage target, but use that wording only when the case mainly exists for
coverage. When the test exercises real behavior, the `Value` field should say
what behavior was tested and why it matters.

When target-file coverage is below the requested goal, `verify` writes
`coverage_history.json`, calls the next-round analysis layer, and produces
`next_round_analysis.md` plus `next_task.md`. By default, if three consecutive
iterations improve by less than five percentage points each, it writes
`stagnation_report.md` and the weak AI must stop for user review.

Low target-file coverage is handled in phases instead of blindly chasing the
final goal:

- `coverage_mapping_blocked`: coverage is missing or the target file is not in
  the report; fix test build or coverage mapping first.
- `bootstrap`: below 15%, establish the smallest credible harness and smoke path
  into the target file.
- `characterization`: 15% to 40%, cover observable current behavior using
  `gtestcov`-curated, CODRAX-cited fixtures, fakes, and dependencies.
- `branch_expansion`: 40% to 70%, add boundary, failure, lifecycle, and protocol
  branch cases.
- `precision_closure`: 70% to the requested goal, close specific remaining
  file:line gaps or write `source_change_request.md` when a seam is required.

If direct CODRAX mode is enabled, each direct weak-AI CODRAX query must be logged
to `.gtestcov/runs/<run_id>/codrax_direct_log.md` with file:line citations.
`verify` fails the audit when that log is missing or lacks file:line evidence.

The generic layer is deliberately narrow: C/C++ syntax-level signals,
GTest/GMock includes and macros, coverage parsing, CLI/MCP flow, and gtestcov's
own safety rules. Build entry points, manifest meanings, support directories,
external API names, custom test macros, framework concepts, project family
detection, and workspace-root meaning must come from user input,
`project_profile.yaml`, CODRAX `file:line` evidence, or traceable gtestcov
artifacts.

`analyze` also writes a Test Obligation Matrix:

```text
.gtestcov/runs/<run_id>/test_obligations.json
.gtestcov/runs/<run_id>/test_obligations.md
```

Each obligation records the test intent, status, evidence, support needs, risk
tags, and required assertions. OpenCode task packages require the weak AI to
implement `ready` obligations and stop with `manual_review_needed.md` instead of
inventing behavior for insufficient or hardware-only obligations.

`codrax-check` probes the local CODRAX CLI with read-only help commands such as
`--help`, `help`, and `--version`, then auto-selects a supported invocation when
it can recognize the current protocol. If a future CODRAX release changes its CLI
shape, configure an explicit template:

```yaml
evidence:
  codrax:
    enabled: true
    command: codrax
    args_template:
      - ask
      - --path
      - "{repo}"
      - --prompt
      - "{request}"
```

Coverage loop thresholds can be tuned in `project_profile.yaml`:

```yaml
coverage:
  max_stagnant_rounds: 3
  min_iteration_improvement: 5.0
  bootstrap_threshold: 15.0
  characterization_threshold: 40.0
  branch_expansion_threshold: 70.0
```

## Linux And WSL Support

`gtestcov` is a Python CLI/MCP tool and is designed to run naturally on Linux.
Native Linux is the preferred environment for large C++ builds when available.

WSL is also supported. When running under WSL, keep the tool checkout and target
C++ project on the Linux filesystem when possible, for example under `~/work`.
Building or doing editable Python installs directly under `/mnt/c` can hit
Windows filesystem permission semantics that CMake and setuptools do not handle
well.

OpenCode can load the MCP server with this config:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "gtestcov": {
      "type": "local",
      "command": ["gtestcov", "mcp"],
      "enabled": true
    }
  }
}
```

The generated `.opencode/commands/gtest-cover.md` command tells the model to use
the MCP tools before editing tests.

## Public Benchmark Projects

For public complex C++ validation, use this sequence before trying very large
projects such as OpenHarmony:

1. F Prime: validates component ports, generated Tester/GTestBase harnesses, and
   F Prime-specific test file reuse.
2. PX4 Autopilot: validates uORB/message boundaries, module lifecycle,
   parameters, driver boundaries, and PX4 functional gtest obligations.

These are evidence-driven adapter validations, not generic discovery rules.
`discover` does not infer F Prime or PX4 from marker files or directory layout.
Enable the `gtestcov` evidence backend and require file:line evidence; the
adapter layer can then consume CODRAX-cited facts and add project-specific
obligations without polluting the generic C++/GTest layer.
