from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence_pack import evidence_cache_status
from .fs import ensure_run_dir
from .profile_sync import profile_sync
from .run_status import update_run_status
from .task import build_task


def cover_target(
    project_root: Path,
    target: str,
    line_coverage: float,
    run_id: str | None = None,
    build_file: str | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    active_run_id, run_dir = ensure_run_dir(root, run_id)
    update_run_status(
        run_dir,
        phase="cover.start",
        command="cover",
        target=target,
        current_operation="profile_sync",
        extra={"line_coverage": line_coverage, "build_file": build_file or ""},
    )
    try:
        sync = profile_sync(root, target=target, run_id=active_run_id, line_coverage=line_coverage, build_file=build_file)
        if sync["status"] != "ok":
            update_run_status(
                run_dir,
                phase="cover.manual_review_needed",
                command="cover",
                target=target,
                current_operation="stopped",
                last_artifact="manual_review_needed.md",
                notes=[f"profile-sync status: {sync['status']}"],
            )
            return {
                "run_id": sync["run_id"],
                "status": "manual_review_needed",
                "target": target,
                "line_coverage": line_coverage,
                "profile_sync": sync,
                "task_path": "",
                "analysis": None,
            }

        update_run_status(
            run_dir,
            phase="cover.build_task",
            command="cover",
            target=target,
            current_operation="build_task",
        )
        analysis, task_path = build_task(root, target, run_id=sync["run_id"], line_coverage=line_coverage)
        update_run_status(
            run_dir,
            phase="cover.task_ready",
            command="cover",
            target=target,
            current_operation="done",
            last_artifact=str(task_path),
            extra={
                "profile_sync_cache": sync.get("evidence_cache", {}),
                "analysis_cache": evidence_cache_status(analysis.codrax_evidence),
            },
        )
        return {
            "run_id": analysis.run_id,
            "status": "task_ready",
            "target": target,
            "line_coverage": line_coverage,
            "profile_sync": sync,
            "task_path": str(task_path),
            "analysis": analysis.model_dump(mode="json"),
        }
    except Exception as exc:
        update_run_status(
            run_dir,
            phase="cover.failed",
            command="cover",
            target=target,
            current_operation="failed",
            notes=[f"{type(exc).__name__}: {exc}"],
        )
        raise
