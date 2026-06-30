from __future__ import annotations

from pathlib import Path

from .analyzer import analyze_target
from .codrax import render_codrax_evidence
from .coverage_goal import write_coverage_goal
from .evidence_paths import codrax_test_source_dirs
from .fs import ensure_run_dir
from .memory import refresh_memory
from .models import AnalysisReport, relpath
from .obligations import render_test_obligations
from .permissions import write_permission_warmup
from .profile import ProjectProfile, load_profile
from .run_status import update_run_status
from .understanding import render_project_understanding


FORBIDDEN_PATTERNS = [
    "Do not edit production source files unless a source_change_request.md is written first.",
    "Do not skip Init when production code requires Init.",
    "Do not create empty allocator, deallocator, or external API implementations.",
    "Do not use #define private public.",
    "Do not use EXPECT_DEATH to hide ordinary crashes.",
    "Do not copy dependency structs, enums, or macros into tests.",
    "Do not use real sleep for async/timer tests.",
]


def build_task(
    project_root: Path,
    target: str,
    run_id: str | None = None,
    line_coverage: float | None = None,
) -> tuple[AnalysisReport, Path]:
    root = project_root.resolve()
    profile = load_profile(root)
    active_run_id, run_dir = ensure_run_dir(root, run_id)
    update_run_status(
        run_dir,
        phase="task.start",
        command="task",
        target=target,
        current_operation="analyze_target",
    )
    try:
        analysis = analyze_target(root, target, active_run_id)
        _, run_dir = ensure_run_dir(root, analysis.run_id)
        target_line_coverage = line_coverage if line_coverage is not None else profile.targets.default_line_coverage
        update_run_status(
            run_dir,
            phase="task.coverage_goal",
            command="task",
            target=target,
            current_operation="write_coverage_goal",
            extra={"line_coverage": target_line_coverage},
        )
        write_coverage_goal(run_dir, target, target_line_coverage)
        update_run_status(
            run_dir,
            phase="task.permission_warmup",
            command="task",
            target=target,
            current_operation="write_permission_warmup",
        )
        _, _, warmup_md = write_permission_warmup(root, run_dir, analysis, profile)
        task_text = render_task(analysis, profile, target_line_coverage, relpath(warmup_md, root))
        task_path = run_dir / "task.md"
        update_run_status(
            run_dir,
            phase="task.write_task",
            command="task",
            target=target,
            current_operation="write_task_package",
            last_artifact=str(task_path),
        )
        task_path.write_text(task_text, encoding="utf-8")
        update_run_status(
            run_dir,
            phase="task.memory_refresh",
            command="task",
            target=target,
            current_operation="memory_refresh",
            last_artifact=str(task_path),
        )
        refresh_memory(root, analysis.run_id)
        update_run_status(
            run_dir,
            phase="task.done",
            command="task",
            target=target,
            current_operation="done",
            last_artifact=str(task_path),
        )
        return analysis, task_path
    except Exception as exc:
        update_run_status(
            run_dir,
            phase="task.failed",
            command="task",
            target=target,
            current_operation="failed",
            notes=[f"{type(exc).__name__}: {exc}"],
        )
        raise


def render_task(
    analysis: AnalysisReport,
    profile: ProjectProfile,
    line_coverage: float | None = None,
    permission_warmup_path: str = "",
) -> str:
    allowed_paths = [
        *profile.test_support.test_dirs,
        *_codrax_test_dirs_from_analysis(analysis),
        profile.test_support.fake_dir,
        profile.test_support.harness_dir,
        profile.test_support.guard_dir,
        profile.test_support.builder_dir,
        profile.test_support.dependency_shim_dir,
        *profile.test_support.test_build_config_paths,
        ".gtestcov/",
    ]
    allowed_paths = [path for path in dict.fromkeys(allowed_paths) if path]
    check_command = f"gtestcov check --run-id {analysis.run_id} --target {analysis.target} --no-codrax"
    verify_command = f"gtestcov verify --run-id {analysis.run_id}"
    return f"""# OpenCode Task Package: gtest coverage

Run ID: `{analysis.run_id}`
Target: `{analysis.target}`
Target line coverage goal: `{line_coverage if line_coverage is not None else profile.targets.default_line_coverage}%`
Selected test type: `{analysis.selected_test_type}`
Decision report: `{analysis.decision_report_path}`

## Context Reload Required
Before any implementation work, and again after any context refresh or compression, read:

- `.gtestcov/runs/{analysis.run_id}/resume_prompt.md`
- `.gtestcov/runs/{analysis.run_id}/handoff.md`
- `.gtestcov/memory/project_memory.md`

The handoff files are generated by `gtestcov` and are read-only for the weak AI.
Project-specific facts must come from user input, `project_profile.yaml`, CODRAX file:line evidence, or traceable `gtestcov` artifacts. Do not infer project details from naming habits or open-source project impressions.
For runtime progress, check `.gtestcov/runs/{analysis.run_id}/gtestcov_status.json` and `.gtestcov/runs/{analysis.run_id}/codrax_status.json`.
During CODRAX execution, gtestcov reads CODRAX native logs under `.gtestcov/runs/{analysis.run_id}/codrax_native_logs/`.
After every CODRAX invocation exits, gtestcov immediately records that invocation's bounded final output under `.gtestcov/runs/{analysis.run_id}/codrax_final_outputs/`; `.gtestcov/runs/{analysis.run_id}/codrax_final_log.md` is only the latest-invocation shortcut.

## OpenCode Permission Warmup
Before implementation edits, read `{permission_warmup_path or 'opencode_permission_warmup.md'}` and concentrate permission prompts near the start:

- Open/read the listed evidence, target, and build-entry files.
- Request edit permission only for planned test, test support, and test build configuration paths that are allowed below.
- Do not request edit permission for the target file or production/business logic source.

## Allowed Write Paths
Only write tests, test support, and test build configuration under:
{_bullets(allowed_paths)}

Do not edit the target file `{analysis.target}` or production/business logic source directly.
Do not edit tool-generated state files such as `handoff.md`, `handoff.json`, `resume_prompt.md`, `verify.json`, `coverage_history.json`, or `.gtestcov/memory/project_memory.*`.

If production source changes are required, do not edit source files. Write the requested seam or refactor to:

`.gtestcov/runs/{analysis.run_id}/source_change_request.md`

Then stop.

## Required Assertions
{_assertion_guidance(analysis.selected_test_type)}

## Test Obligation Matrix
{render_test_obligations(analysis.test_obligations).rstrip()}

Implement every obligation with status `ready`.
Do not implement `manual_review_needed` or `hardware_only` obligations by guessing; write `.gtestcov/runs/{analysis.run_id}/manual_review_needed.md` and stop if they block safe test generation.
Every generated test should map back to at least one obligation ID in comments or `review_checklist.md`.
Maintain `.gtestcov/runs/{analysis.run_id}/modified_files.txt` with one modified repository-relative path per line.

## Required Test Case Description
Before every `TEST`, `TEST_F`, or `TEST_P` case that you add or modify, write a short comment block with these fields:

```cpp
/*
Test Case: <suite.case name>
Value: <what this test really exercises and why that behavior is valuable>
Steps: <1. arrange dependencies/input; 2. call target; 3. observe result>
Inputs: <important input values, dependency states, fake returns, messages, or config>
Expected Outputs: <asserted return value, state change, fake interaction, message, or error>
*/
```

Coverage-only or low-value cases are allowed when needed to reach the target, but only use that wording when the test mainly exists to raise coverage. If the test exercises real behavior, the `Value` field must say what behavior it tests and why that matters.

## Required Support
{_bullets(analysis.required_support or ['Minimal fixture if needed.'])}

## CODRAX Evidence
{render_codrax_evidence(analysis.codrax_evidence).rstrip()}

## CODRAX Project Understanding
{render_project_understanding(analysis.project_understanding).rstrip()}

Use CODRAX-cited file:line references before making dependency, harness, or lifecycle assumptions.
If evidence is unavailable, contradictory, or insufficient for a safe test, write one of these files and stop instead of guessing:

- `.gtestcov/runs/{analysis.run_id}/manual_review_needed.md`
- `.gtestcov/runs/{analysis.run_id}/source_change_request.md`

## Safety Rules
{_bullets(FORBIDDEN_PATTERNS)}

## CODRAX Direct Mode Audit
If the active profile enables direct CODRAX mode, every direct CODRAX query must be recorded in:

`.gtestcov/runs/{analysis.run_id}/codrax_direct_log.md`

Each entry must include the question or command, conclusion, cited file:line references, and how it changed the test or build decision.

## Build/Test/Coverage Commands
- User build file anchor: `{profile.build.build_file or 'not configured'}`
- CODRAX candidate build files: `{profile.build.candidate_build_files or []}`
- Build: `{profile.build.incremental_build_command or profile.build.build_command or 'not configured'}`
- Test: `{profile.build.filtered_test_command or profile.build.test_command or 'not configured'}`
- Coverage: `{profile.build.target_coverage_command or profile.build.coverage_command or 'not configured'}`
- Build timeout: `{profile.build.build_timeout_seconds}` seconds
- Test timeout: `{profile.build.test_timeout_seconds}` seconds
- Coverage timeout: `{profile.build.coverage_timeout_seconds}` seconds
- Preflight before build/test/coverage: `{check_command}`
- Verify this iteration with: `{verify_command}`

Before running verify on a new project, review the build/test/coverage commands in `project_profile.yaml`.
Configured commands execute through `shell=True` from the project root. `gtestcov verify` records command
source, SHA256, CWD, timeout provenance, and review status, but it does not approve command safety for you.

CODRAX request timeout behavior is activity based:

- Idle timeout: `{profile.evidence.codrax.idle_timeout_seconds}` seconds without stdout/stderr or CODRAX native log growth
- Max runtime: `{profile.evidence.codrax.max_runtime_seconds}` seconds total
- Native log tail read limit: `{profile.evidence.codrax.native_log_tail_bytes}` bytes
- Final diagnostic log limit: `{profile.evidence.codrax.final_log_max_bytes}` bytes
- Status update interval: `{profile.evidence.codrax.status_update_interval_seconds}` seconds

## Completion Criteria
- `gtestcov check` passes before running build/test/coverage.
- The generated tests compile and run.
- All `ready` test obligations are implemented or explicitly explained in `review_checklist.md`.
- The selected test type matches the decision report.
- Target file line coverage meets {line_coverage if line_coverage is not None else profile.targets.default_line_coverage}% when coverage data is available.
- Any unmet coverage target is explained in `review_checklist.md`.
"""


def _assertion_guidance(selected: str) -> str:
    if "Message Conformance" in selected:
        return _bullets(["Assert message ID, length, payload bytes, endian/bitfield, and CRC/checksum."])
    if "Message Interface" in selected:
        return _bullets(["Assert sent/received messages and observable side effects via fake bus or fake peer."])
    if "Lifecycle" in selected or "Component" in selected:
        return _bullets(["Call Init with ASSERT.", "Call Shutdown/Stop/DeInit in teardown.", "Assert fake boundary effects."])
    if "Unit" in selected:
        return _bullets(["Use table-driven inputs and expected outputs.", "Assert boundary and invalid inputs."])
    if "Fault" in selected:
        return _bullets(["Inject downstream failure.", "Assert safe error handling and no crash."])
    return _bullets(["Use observable behavior and meaningful EXPECT/ASSERT checks."])


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _codrax_test_dirs_from_analysis(analysis: AnalysisReport) -> list[str]:
    values: list[str] = []
    for evidence in (analysis.codrax_evidence, analysis.project_understanding.codrax_evidence):
        values.extend(codrax_test_source_dirs(evidence).keys())
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.replace("\\", "/").strip().lstrip("./")
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result
