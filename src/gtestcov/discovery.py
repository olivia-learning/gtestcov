from __future__ import annotations

import json
import re
from pathlib import Path

from .fs import CPP_SUFFIXES, iter_files, read_text
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
    for path in iter_files(root):
        rel = relpath(path, root)
        name = path.name
        if name in BUILD_NAMES:
            build_files.append(rel)
            if inferred_build_system == "unknown":
                inferred_build_system = BUILD_NAMES[name]
        if rel in _configured_manifests(profile):
            manifests.append(rel)

    for path in iter_files(root, CPP_SUFFIXES):
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
