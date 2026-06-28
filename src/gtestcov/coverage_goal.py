from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GOAL_NAME = "coverage_goal.json"


def write_coverage_goal(run_dir: Path, target: str, line_coverage: float) -> Path:
    path = run_dir / GOAL_NAME
    path.write_text(
        json.dumps(
            {
                "target": target,
                "line_coverage": float(line_coverage),
                "metric": "target_file_line_coverage",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def read_coverage_goal(run_dir: Path) -> dict[str, Any]:
    path = run_dir / GOAL_NAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
