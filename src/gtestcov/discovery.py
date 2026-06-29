from __future__ import annotations

import json
import re
from pathlib import Path

from .file_index import index_status, load_file_index
from .fs import CPP_SUFFIXES, read_text, scan_files, scan_roots_from_profile
from .models import DiscoveryReport, relpath
from .profile import load_profile


BUILD_NAMES = {
    "CMakeLists.txt": "cmake",
    "BUILD.gn": "gn",
    "Makefile": "make",
    "meson.build": "meson",
}


def discover_project(project_root: Path) -> DiscoveryReport:
    root = project_root.resolve()
    profile = load_profile(root)
    macro_re = _macro_re(profile)
    macros = {name: 0 for name in _configured_macros(profile)}
    test_files: list[str] = []
    gtest_includes: list[str] = []
    gmock_includes: list[str] = []
    build_files: list[str] = []
    manifests: list[str] = []
    support_dirs: dict[str, list[str]] = {kind: [] for kind in ["fake", "harness", "guard", "builder", "dependency_shim"]}

    inferred_build_system = "unknown"
    scan_roots = scan_roots_from_profile(profile)
    exclude_dirs = profile.paths.exclude_dirs
    max_files = profile.paths.max_files
    index_state = index_status(root)
    if index_state.get("hit"):
        all_scan = _scan_from_index(root, load_file_index(root))
        cpp_scan = _filter_index_scan(all_scan, CPP_SUFFIXES)
    else:
        all_scan = scan_files(root, scan_roots=scan_roots, exclude_dirs=exclude_dirs, max_files=max_files)
        cpp_scan = scan_files(root, CPP_SUFFIXES, scan_roots=scan_roots, exclude_dirs=exclude_dirs, max_files=max_files)
    _write_scan_progress(root, "discover", all_scan, cpp_scan, index_state)
    for path in all_scan["files"]:
        rel = relpath(path, root)
        name = path.name
        if name in BUILD_NAMES:
            build_files.append(rel)
            if inferred_build_system == "unknown":
                inferred_build_system = BUILD_NAMES[name]
        if rel in _configured_manifests(profile):
            manifests.append(rel)

    for path in cpp_scan["files"]:
        rel = relpath(path, root)
        text = read_text(path)
        if "gtest/gtest.h" in text:
            gtest_includes.append(rel)
        if "gmock/gmock.h" in text:
            gmock_includes.append(rel)
        for match in macro_re.findall(text):
            macros[match] = macros.get(match, 0) + 1
        if "gtest/gtest.h" in text or macro_re.search(text):
            test_files.append(rel)

    for kind, configured in _configured_support_dirs(profile).items():
        if configured and (root / configured).is_dir():
            support_dirs.setdefault(kind, []).append(configured)

    conflicts: list[str] = []
    for forbidden in profile.style.forbidden_macros:
        if macros.get(forbidden, 0) > 0:
            conflicts.append(f"Forbidden macro {forbidden} appears {macros[forbidden]} time(s).")
    configured_build = profile.build.system
    if configured_build not in {"", "unknown"} and inferred_build_system != "unknown" and configured_build != inferred_build_system:
        conflicts.append(
            f"Profile build.system={configured_build} conflicts with discovered {inferred_build_system}."
        )

    return DiscoveryReport(
        project_root=str(root),
        test_macros=macros,
        test_files=sorted(set(test_files)),
        gtest_includes=sorted(set(gtest_includes)),
        gmock_includes=sorted(set(gmock_includes)),
        build_files=sorted(set(build_files)),
        manifests=sorted(set(manifests)),
        support_dirs={key: sorted(set(value)) for key, value in support_dirs.items()},
        inferred_build_system=inferred_build_system,
        conflicts=conflicts,
    )


def _write_scan_progress(root: Path, command: str, all_scan: dict, cpp_scan: dict, index_state: dict) -> None:
    gtestcov_dir = root / ".gtestcov"
    gtestcov_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "command": command,
        "file_index": index_state,
        "all_files": _scan_summary(all_scan),
        "cpp_files": _scan_summary(cpp_scan),
    }
    progress_path = gtestcov_dir / "discovery_scan_progress.json"
    progress_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if all_scan.get("truncated") or cpp_scan.get("truncated"):
        truncated_path = gtestcov_dir / "scan_truncated.md"
        truncated_path.write_text(
            "\n".join(
                [
                    "# gtestcov Scan Truncated",
                    "",
                    f"- Command: `{command}`",
                    f"- Max files: `{all_scan.get('max_files') or cpp_scan.get('max_files')}`",
                    f"- All matched files: `{all_scan.get('matched')}`",
                    f"- C/C++ matched files: `{cpp_scan.get('matched')}`",
                    "",
                    "The configured scan limit was reached before the scan completed.",
                    "Narrow `paths.source_roots`, `paths.test_roots`, or `paths.build_roots`, or increase `paths.max_files`.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )


def _scan_summary(scan: dict) -> dict:
    return {
        "scanned": scan.get("scanned", 0),
        "matched": scan.get("matched", 0),
        "truncated": scan.get("truncated", False),
        "max_files": scan.get("max_files"),
        "scan_roots": scan.get("scan_roots", []),
        "exclude_dirs": scan.get("exclude_dirs", []),
        "skipped_excluded": scan.get("skipped_excluded", 0),
        "progress": scan.get("progress", []),
    }


def _scan_from_index(root: Path, index: dict) -> dict:
    files = []
    for rel in sorted((index.get("files") or {}).keys()):
        path = root / rel
        if path.exists() and path.is_file():
            files.append(path)
    return {
        "files": files,
        "scanned": len(files),
        "matched": len(files),
        "truncated": index.get("truncated", False),
        "max_files": index.get("max_files"),
        "scan_roots": index.get("scan_roots", []),
        "exclude_dirs": index.get("exclude_dirs", []),
        "skipped_excluded": 0,
        "progress": [],
    }


def _filter_index_scan(scan: dict, suffixes: set[str]) -> dict:
    files = [path for path in scan.get("files", []) if path.suffix.lower() in suffixes]
    filtered = dict(scan)
    filtered["files"] = files
    filtered["matched"] = len(files)
    return filtered


def save_discovery(project_root: Path, run_dir: Path) -> DiscoveryReport:
    report = discover_project(project_root)
    (run_dir / "discovery.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def discovery_to_json(project_root: Path) -> str:
    return json.dumps(discover_project(project_root).model_dump(mode="json"), indent=2)


def _configured_manifests(profile) -> set[str]:
    names = [profile.dependency.manifest, *profile.dependency.manifest_candidates]
    return {name.replace("\\", "/") for name in names if name}


def _configured_macros(profile) -> list[str]:
    return list(dict.fromkeys([*profile.style.preferred_macros, *profile.style.forbidden_macros]))


def _macro_re(profile) -> re.Pattern[str]:
    macros = _configured_macros(profile)
    if not macros:
        return re.compile(r"$^")
    pattern = "|".join(re.escape(macro) for macro in sorted(macros, key=len, reverse=True))
    return re.compile(rf"\b({pattern})\s*\(")


def _configured_support_dirs(profile) -> dict[str, str]:
    return {
        "fake": profile.test_support.fake_dir,
        "harness": profile.test_support.harness_dir,
        "guard": profile.test_support.guard_dir,
        "builder": profile.test_support.builder_dir,
        "dependency_shim": profile.test_support.dependency_shim_dir,
    }
