from __future__ import annotations

import re
from pathlib import Path

from .codrax import FILE_LINE_RE
from .fs import CPP_SUFFIXES, iter_files, read_text
from .models import relpath
from .profile import load_profile


FORBIDDEN_CHECKS = {
    "define_private_public": re.compile(r"#\s*define\s+private\s+public"),
    "ordinary_death_test": re.compile(r"\bEXPECT_DEATH\s*\("),
    "real_sleep": re.compile(r"\b(std::this_thread::)?sleep_for\s*\("),
}
PROTECTED_GTESTCOV_STATE_FILES = {
    "handoff.json",
    "handoff.md",
    "resume_prompt.md",
    "verify.json",
    "coverage_history.json",
    "gtestcov_status.json",
    "gtestcov_events.ndjson",
    "codrax_status.json",
    "codrax_final_log.md",
    "project_memory.json",
    "project_memory.md",
}
GTEST_CASE_RE = re.compile(r"^\s*(TEST|TEST_F|TEST_P)\s*\(", re.MULTILINE)
TEST_CASE_DESCRIPTION_FIELDS = {
    "test_case_name": re.compile(r"\b(Test Case|Case Name)\s*:", re.I),
    "test_value": re.compile(r"\b(Value|Test Value|Purpose)\s*:", re.I),
    "test_steps": re.compile(r"\b(Steps|Test Steps)\s*:", re.I),
    "test_inputs": re.compile(r"\b(Inputs?|Test Inputs?)\s*:", re.I),
    "expected_outputs": re.compile(r"\b(Expected Outputs?|Outputs?|Expected)\s*:", re.I),
}


def audit_generated_tests(project_root: Path, run_dir: Path | None = None) -> dict:
    profile = load_profile(project_root)
    dirs = [
        *profile.test_support.test_dirs,
        profile.test_support.fake_dir,
        profile.test_support.harness_dir,
        profile.test_support.guard_dir,
        profile.test_support.builder_dir,
        profile.test_support.dependency_shim_dir,
    ]
    checked_dirs = [dirname for dirname in dict.fromkeys(dirs) if dirname]
    violations: list[dict[str, str]] = []
    for path in _audit_files(project_root, profile, checked_dirs):
        text = read_text(path)
        for name, pattern in FORBIDDEN_CHECKS.items():
            if pattern.search(text):
                violations.append({"check": name, "path": relpath(path, project_root)})
        for macro in profile.style.forbidden_macros:
            if re.search(rf"\b{re.escape(macro)}\s*\(", text):
                violations.append({"check": f"forbidden_macro_{macro}", "path": relpath(path, project_root)})
    if run_dir is not None:
        violations.extend(audit_test_case_descriptions(project_root, run_dir, profile, checked_dirs))
    return {"violations": violations, "checked_dirs": checked_dirs}


def audit_test_case_descriptions(project_root: Path, run_dir: Path, profile, checked_dirs: list[str]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for path in _modified_gtest_files(project_root, run_dir, profile, checked_dirs):
        text = read_text(path)
        line_starts = _line_starts(text)
        lines = text.splitlines()
        for match in GTEST_CASE_RE.finditer(text):
            line_number = _line_number(line_starts, match.start())
            context = "\n".join(lines[max(0, line_number - 13):line_number - 1])
            missing = [
                field
                for field, pattern in TEST_CASE_DESCRIPTION_FIELDS.items()
                if not pattern.search(context)
            ]
            if missing:
                violations.append(
                    {
                        "check": "missing_test_case_description",
                        "path": f"{relpath(path, project_root)}:{line_number}",
                        "detail": "missing " + ", ".join(missing),
                    }
                )
    return violations


def audit_codrax_direct_log(run_dir: Path, profile) -> list[dict[str, str]]:
    direct = profile.evidence.codrax.direct_mode
    if not direct.enabled or not direct.require_audit_log:
        return []
    path = run_dir / "codrax_direct_log.md"
    if not path.exists():
        return [{"check": "missing_codrax_direct_log", "path": relpath(path, run_dir.parent.parent)}]
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not FILE_LINE_RE.search(text):
        return [{"check": "codrax_direct_log_missing_file_line", "path": relpath(path, run_dir.parent.parent)}]
    return []


def audit_write_scope(project_root: Path, run_dir: Path, profile, target: str = "") -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    source_request = run_dir / "source_change_request.md"
    if source_request.exists():
        violations.append({"check": "source_change_request_present", "path": relpath(source_request, project_root)})

    manual_review = run_dir / "manual_review_needed.md"
    if manual_review.exists():
        violations.append({"check": "manual_review_needed_present", "path": relpath(manual_review, project_root)})

    modified = run_dir / "modified_files.txt"
    if not modified.exists():
        if (run_dir / "task.md").exists() and not source_request.exists() and not manual_review.exists():
            violations.append({"check": "missing_modified_files_log", "path": relpath(modified, project_root)})
        return violations

    allowed = allowed_write_paths(profile)
    target_norm = norm_rel(target)
    for raw_line in modified.read_text(encoding="utf-8", errors="ignore").splitlines():
        path = norm_rel(raw_line.strip())
        if not path or path.startswith("#"):
            continue
        if is_protected_gtestcov_state(path):
            violations.append({"check": "protected_gtestcov_state_modified", "path": path})
        elif target_norm and path == target_norm:
            violations.append({"check": "target_file_modified", "path": path})
        elif not is_allowed_path(path, allowed):
            violations.append({"check": "write_scope_violation", "path": path})
    return violations


def allowed_write_paths(profile) -> list[str]:
    values = [
        *profile.test_support.test_dirs,
        profile.test_support.fake_dir,
        profile.test_support.harness_dir,
        profile.test_support.guard_dir,
        profile.test_support.builder_dir,
        profile.test_support.dependency_shim_dir,
        *profile.test_support.test_build_config_paths,
        ".gtestcov",
    ]
    return [norm_rel(value) for value in values if value]


def is_allowed_path(path: str, allowed: list[str]) -> bool:
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in allowed)


def protected_gtestcov_state_paths(run_id: str | None = None) -> list[str]:
    run_prefix = f".gtestcov/runs/{run_id}" if run_id else ".gtestcov/runs/<run_id>"
    return [
        f"{run_prefix}/handoff.json",
        f"{run_prefix}/handoff.md",
        f"{run_prefix}/resume_prompt.md",
        f"{run_prefix}/verify.json",
        f"{run_prefix}/coverage_history.json",
        f"{run_prefix}/gtestcov_status.json",
        f"{run_prefix}/gtestcov_events.ndjson",
        f"{run_prefix}/codrax_status.json",
        f"{run_prefix}/codrax_final_log.md",
        f"{run_prefix}/codrax_final_outputs/",
        f"{run_prefix}/codrax_native_logs/",
        ".gtestcov/memory/project_memory.json",
        ".gtestcov/memory/project_memory.md",
    ]


def is_protected_gtestcov_state(path: str) -> bool:
    normalized = norm_rel(path)
    if normalized.startswith(".gtestcov/memory/"):
        return Path(normalized).name in PROTECTED_GTESTCOV_STATE_FILES
    if normalized.startswith(".gtestcov/runs/"):
        if "/codrax_final_outputs/" in normalized or normalized.endswith("/codrax_final_outputs"):
            return True
        if "/codrax_native_logs/" in normalized or normalized.endswith("/codrax_native_logs"):
            return True
        return Path(normalized).name in PROTECTED_GTESTCOV_STATE_FILES
    return False


def norm_rel(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _audit_files(project_root: Path, profile, checked_dirs: list[str]) -> list[Path]:
    if checked_dirs:
        files: list[Path] = []
        for dirname in checked_dirs:
            base = project_root / dirname
            if base.exists():
                files.extend(iter_files(base, CPP_SUFFIXES))
        return files
    return [path for path in iter_files(project_root, CPP_SUFFIXES) if _looks_like_gtest_file(path, profile)]


def _modified_gtest_files(project_root: Path, run_dir: Path, profile, checked_dirs: list[str]) -> list[Path]:
    modified = run_dir / "modified_files.txt"
    if not modified.exists():
        return []
    result: list[Path] = []
    seen: set[Path] = set()
    for raw_line in modified.read_text(encoding="utf-8", errors="ignore").splitlines():
        rel = norm_rel(raw_line.strip())
        if not rel or rel.startswith("#"):
            continue
        path = project_root / rel
        if path in seen or not path.exists() or path.suffix not in CPP_SUFFIXES:
            continue
        if _is_in_checked_dir(rel, checked_dirs) or _looks_like_gtest_file(path, profile):
            result.append(path)
            seen.add(path)
    return result


def _is_in_checked_dir(path: str, checked_dirs: list[str]) -> bool:
    return any(path == norm_rel(dirname) or path.startswith(norm_rel(dirname).rstrip("/") + "/") for dirname in checked_dirs)


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _line_number(line_starts: list[int], offset: int) -> int:
    line = 1
    for index, start in enumerate(line_starts, start=1):
        if start > offset:
            break
        line = index
    return line


def _looks_like_gtest_file(path: Path, profile) -> bool:
    text = read_text(path)
    if "gtest/gtest.h" in text or "gmock/gmock.h" in text:
        return True
    macros = [*profile.style.preferred_macros, *profile.style.forbidden_macros]
    return any(re.search(rf"\b{re.escape(macro)}\s*\(", text) for macro in macros)
