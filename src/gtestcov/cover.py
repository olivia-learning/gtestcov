from __future__ import annotations

from pathlib import Path
from typing import Any

from .profile_sync import profile_sync
from .task import build_task


def cover_target(
    project_root: Path,
    target: str,
    line_coverage: float,
    run_id: str | None = None,
    build_file: str | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    sync = profile_sync(root, target=target, run_id=run_id, line_coverage=line_coverage, build_file=build_file)
    if sync["status"] != "ok":
        return {
            "run_id": sync["run_id"],
            "status": "manual_review_needed",
            "target": target,
            "line_coverage": line_coverage,
            "profile_sync": sync,
            "task_path": "",
            "analysis": None,
        }

    analysis, task_path = build_task(root, target, run_id=sync["run_id"], line_coverage=line_coverage)
    return {
        "run_id": analysis.run_id,
        "status": "task_ready",
        "target": target,
        "line_coverage": line_coverage,
        "profile_sync": sync,
        "task_path": str(task_path),
        "analysis": analysis.model_dump(mode="json"),
    }
