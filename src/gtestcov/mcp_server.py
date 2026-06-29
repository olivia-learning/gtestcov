from __future__ import annotations

from pathlib import Path
from typing import Any

from .analyzer import analyze_target
from .codrax import codrax_check
from .cover import cover_target
from .detached_evidence import evidence_collect, evidence_start, evidence_status
from .diagnose import diagnose_failure
from .discovery import discover_project
from .memory import refresh_memory, show_memory
from .next_round import plan_next_round
from .preflight import preflight_check
from .profile_sync import profile_sync
from .run_status import show_status
from .task import build_task
from .understanding import generate_project_understanding
from .verify import audit_generated_tests, verify_iteration


def run_mcp_server() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit("The 'mcp' package is required. Install with: python -m pip install -e .") from exc

    mcp = FastMCP("gtestcov")

    @mcp.tool()
    def gtestcov_discover_project(project_root: str = ".") -> dict[str, Any]:
        """Discover gtest style, build files, manifests, and support directories."""
        return discover_project(Path(project_root)).model_dump(mode="json")

    @mcp.tool()
    def gtestcov_analyze_target(project_root: str = ".", target: str = "", run_id: str = "") -> dict[str, Any]:
        """Analyze a target and write a decision report before any test generation."""
        return analyze_target(Path(project_root), target, run_id or None).model_dump(mode="json")

    @mcp.tool()
    def gtestcov_build_obligations(project_root: str = ".", target: str = "", run_id: str = "") -> dict[str, Any]:
        """Build a test obligation matrix for a target without generating tests."""
        report = analyze_target(Path(project_root), target, run_id or None)
        return {
            "run_id": report.run_id,
            "target": report.target,
            "test_obligations": [item.model_dump(mode="json") for item in report.test_obligations],
        }

    @mcp.tool()
    def gtestcov_collect_evidence(project_root: str = ".", target: str = "", run_id: str = "") -> dict[str, Any]:
        """Start detached CODRAX-assisted project understanding for a target without generating tests."""
        return evidence_start(Path(project_root), target, run_id or None)

    @mcp.tool()
    def gtestcov_evidence_status(project_root: str = ".", run_id: str = "") -> dict[str, Any]:
        """Poll detached CODRAX-assisted project understanding status."""
        return evidence_status(Path(project_root), run_id)

    @mcp.tool()
    def gtestcov_evidence_collect(project_root: str = ".", run_id: str = "") -> dict[str, Any]:
        """Collect detached CODRAX-assisted project understanding after it completes."""
        return evidence_collect(Path(project_root), run_id)

    @mcp.tool()
    def gtestcov_codrax_check(project_root: str = ".", run_id: str = "") -> dict[str, Any]:
        """Check whether the configured CODRAX CLI can return file:line evidence."""
        return codrax_check(Path(project_root), run_id=run_id or None)

    @mcp.tool()
    def gtestcov_profile_sync(
        project_root: str = ".",
        target: str = "",
        run_id: str = "",
        line_coverage: float | None = None,
        build_file: str = "",
    ) -> dict[str, Any]:
        """Use CODRAX evidence to update project_profile.yaml for a target."""
        return profile_sync(Path(project_root), target, run_id or None, line_coverage, build_file or None)

    @mcp.tool()
    def gtestcov_cover_target(
        project_root: str = ".",
        target: str = "",
        line_coverage: float = 70.0,
        run_id: str = "",
        build_file: str = "",
    ) -> dict[str, Any]:
        """Prepare a single-file coverage task package."""
        return cover_target(Path(project_root), target, line_coverage, run_id or None, build_file or None)

    @mcp.tool()
    def gtestcov_build_task(
        project_root: str = ".",
        target: str = "",
        run_id: str = "",
        line_coverage: float | None = None,
    ) -> dict[str, Any]:
        """Build a constrained task package for a weak AI model."""
        analysis, task_path = build_task(Path(project_root), target, run_id or None, line_coverage)
        data = analysis.model_dump(mode="json")
        data["task_path"] = str(task_path)
        return data

    @mcp.tool()
    def gtestcov_preflight_check(project_root: str = ".", run_id: str = "latest", target: str = "") -> dict[str, Any]:
        """Run fast preflight checks before build/test/coverage."""
        return preflight_check(Path(project_root), run_id, target)

    @mcp.tool()
    def gtestcov_verify_iteration(
        project_root: str = ".",
        run_id: str = "latest",
        target: str = "",
        line_coverage: float | None = None,
        max_stagnant_rounds: int | None = None,
        min_improvement: float | None = None,
        build_timeout: int | None = None,
        test_timeout: int | None = None,
        coverage_timeout: int | None = None,
    ) -> dict[str, Any]:
        """Run configured build/test/coverage commands and audit generated tests."""
        return verify_iteration(
            Path(project_root),
            run_id,
            target,
            line_coverage,
            max_stagnant_rounds,
            min_improvement,
            build_timeout,
            test_timeout,
            coverage_timeout,
        )

    @mcp.tool()
    def gtestcov_diagnose_failure(project_root: str = ".", run_id: str = "latest", target: str = "") -> dict[str, Any]:
        """Use CODRAX to diagnose a failed verify iteration."""
        return diagnose_failure(Path(project_root), run_id, target)

    @mcp.tool()
    def gtestcov_plan_next_round(
        project_root: str = ".",
        run_id: str = "latest",
        max_stagnant_rounds: int | None = None,
        min_improvement: float | None = None,
    ) -> dict[str, Any]:
        """Plan the next coverage iteration after an unmet target."""
        return plan_next_round(
            Path(project_root),
            run_id,
            max_stagnant_rounds=max_stagnant_rounds,
            min_improvement=min_improvement,
        )

    @mcp.tool()
    def gtestcov_memory_refresh(project_root: str = ".", run_id: str = "latest") -> dict[str, Any]:
        """Refresh run handoff files and project memory for context recovery."""
        return refresh_memory(Path(project_root), run_id)

    @mcp.tool()
    def gtestcov_memory_show(project_root: str = ".", run_id: str = "latest", output_format: str = "md") -> dict[str, Any]:
        """Show run handoff memory as Markdown or JSON."""
        return show_memory(Path(project_root), run_id, output_format)

    @mcp.tool()
    def gtestcov_status(project_root: str = ".", run_id: str = "latest") -> dict[str, Any]:
        """Show current gtestcov and CODRAX run status."""
        return show_status(Path(project_root), run_id)

    @mcp.tool()
    def gtestcov_audit_generated_tests(project_root: str = ".") -> dict[str, Any]:
        """Audit generated tests for forbidden patterns."""
        return audit_generated_tests(Path(project_root))

    mcp.run()
