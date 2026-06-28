from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from gtestcov.cover import cover_target
from gtestcov.coverage_goal import write_coverage_goal
from gtestcov.diagnose import diagnose_failure
from gtestcov.analyzer import analyze_target
from gtestcov.codrax import codrax_check
from gtestcov.dependency import classify_symbol, parse_dependency_xml
from gtestcov.discovery import discover_project
from gtestcov.memory import refresh_memory, show_memory
from gtestcov.preflight import preflight_check
from gtestcov.profile_sync import profile_sync
from gtestcov.profile import load_profile, profile_to_yaml
from gtestcov.task import build_task
from gtestcov.verify import audit_generated_tests, parse_gcovr_summary, parse_gcovr_xml, verify_iteration


REPO_ROOT = Path(__file__).resolve().parents[1]
MINI_REPO = REPO_ROOT / "examples" / "energy_mini_repo"


def copy_mini_repo(tmp_path: Path) -> Path:
    dst = tmp_path / "energy_mini_repo"
    shutil.copytree(MINI_REPO, dst)
    return dst


def write_fake_codrax(tmp_path: Path, output: str, returncode: int = 0) -> str:
    script = tmp_path / "fake_codrax.py"
    script.write_text(
        "import argparse\n"
        "import sys\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--repo')\n"
        "parser.add_argument('--request')\n"
        "parser.parse_args()\n"
        f"sys.stdout.write({output!r})\n"
        f"sys.exit({returncode})\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def write_fake_codrax_alt_cli(tmp_path: Path) -> str:
    script = tmp_path / "fake_codrax_alt.py"
    script.write_text(
        "import argparse\n"
        "import sys\n"
        "if '--help' in sys.argv or len(sys.argv) == 1:\n"
        "    print('Usage: codrax ask --path <repo> --prompt <prompt>')\n"
        "    sys.exit(0)\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('command')\n"
        "parser.add_argument('--path')\n"
        "parser.add_argument('--prompt')\n"
        "args = parser.parse_args()\n"
        "if args.command != 'ask' or not args.path or not args.prompt:\n"
        "    print('bad invocation', file=sys.stderr)\n"
        "    sys.exit(3)\n"
        "sys.stdout.write('- alt evidence: src/energy_service.cpp:12 uses Init.\\n')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def write_fake_codrax_template_only(tmp_path: Path) -> str:
    script = tmp_path / "fake_codrax_template.py"
    script.write_text(
        "import argparse\n"
        "import sys\n"
        "if '--help' in sys.argv or len(sys.argv) == 1:\n"
        "    print('Usage: codrax custom-run <options>')\n"
        "    sys.exit(0)\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('command')\n"
        "parser.add_argument('--root')\n"
        "parser.add_argument('--question')\n"
        "args = parser.parse_args()\n"
        "if args.command != 'probe' or not args.root or not args.question:\n"
        "    print('bad template invocation', file=sys.stderr)\n"
        "    sys.exit(4)\n"
        "sys.stdout.write('- template evidence: src/energy_service.cpp:12 uses Init.\\n')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def enable_codrax(root: Path, command: str) -> None:
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")


def test_profile_and_discovery_on_mini_repo(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    profile = load_profile(root)
    discovery = discover_project(root)

    assert profile.project_name == "energy_mini_repo"
    assert "dependency.xml" in discovery.manifests
    assert "CMakeLists.txt" in discovery.build_files
    assert discovery.inferred_build_system == "cmake"
    assert discovery.test_macros["TEST"] >= 2
    assert any(path.endswith("tests/support/fakes") for path in discovery.support_dirs["fake"])


def test_discovery_does_not_embed_project_specific_build_detection(tmp_path: Path) -> None:
    root = tmp_path / "fprime"
    (root / "cmake").mkdir(parents=True)
    (root / "settings.ini").write_text("[fprime]\n", encoding="utf-8")
    (root / "cmake" / "fprime-util.cmake").write_text("# marker\n", encoding="utf-8")
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    (root / "project_profile.yaml").write_text(
        """
project_name: fprime
build:
  system: fprime-util
""",
        encoding="utf-8",
    )

    discovery = discover_project(root)

    assert discovery.inferred_build_system == "cmake"
    assert any("conflicts" in item for item in discovery.conflicts)


def test_px4_adapter_uses_only_codrax_evidence(tmp_path: Path) -> None:
    root = tmp_path / "PX4-Autopilot"
    module_dir = root / "src" / "modules" / "battery_status"
    for path in [root / "boards", root / "msg", root / "platforms", module_dir]:
        path.mkdir(parents=True)
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    (root / "project_profile.yaml").write_text(
        """
project_name: px4-autopilot
build:
  system: cmake
test_support:
  test_dirs:
    - src
  harness_dir: src
""",
        encoding="utf-8",
    )
    (module_dir / "BatteryStatus.cpp").write_text(
        """
#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <parameters/param.h>

class BatteryStatus : public ModuleParams {
public:
    void Run() {
        orb_advert_t pub = nullptr;
        param_get(param_find("BAT_LOW_THR"), &_threshold);
        _vehicle_status_sub.update();
        _battery_status_pub.publish(_battery_status);
    }

private:
    uORB::Subscription _vehicle_status_sub{ORB_ID(vehicle_status)};
    uORB::Publication<battery_status_s> _battery_status_pub{ORB_ID(battery_status)};
    battery_status_s _battery_status{};
    float _threshold{};
};
""",
        encoding="utf-8",
    )
    command = write_fake_codrax(
        tmp_path,
        """
- [framework_concepts] src/modules/battery_status/BatteryStatus.cpp:5 PX4 module uses ModuleParams and uORB concepts.
- [dependencies] src/modules/battery_status/BatteryStatus.cpp:14 ORB_ID(vehicle_status) and uORB Subscription are collaborator boundaries.
- [test_surfaces] src/modules/battery_status/battery_status_test.cpp:3 PX4 functional gtest pattern exists for this module family.
- [boundaries_and_risks] src/modules/battery_status/BatteryStatus.cpp:8 PX4 params must be reset and uORB topics must not be invented.
""",
    )
    enable_codrax(root, command)

    discovery = discover_project(root)
    analysis = analyze_target(root, "src/modules/battery_status/BatteryStatus.cpp", run_id="px4")
    obligations = {item.obligation_id for item in analysis.test_obligations}

    assert discovery.inferred_build_system == "cmake"
    assert analysis.project_understanding.status == "ok"
    assert analysis.selected_test_type == "Message Interface Test / Component Test (CODRAX-cited PX4 Functional GTest)"
    assert "px4_cited_functional_gtest_boundary" in obligations
    assert any(item.kind == "px4_functional_gtest" for item in analysis.test_obligations)


def test_fprime_adapter_uses_only_codrax_cited_harness(tmp_path: Path) -> None:
    root = tmp_path / "fprime"
    component = root / "Svc" / "ComStub"
    test_dir = component / "test" / "ut"
    (root / "cmake").mkdir(parents=True)
    test_dir.mkdir(parents=True)
    (root / "settings.ini").write_text("[fprime]\n", encoding="utf-8")
    (root / "cmake" / "fprime-util.cmake").write_text("# marker\n", encoding="utf-8")
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    (root / "project_profile.yaml").write_text(
        """
project_name: fprime
build:
  system: fprime-util
test_support:
  test_dirs:
    - Svc
  harness_dir: Svc
""",
        encoding="utf-8",
    )
    (component / "ComStub.cpp").write_text(
        """
void ComStub::drvReceiveIn_handler(Fw::Buffer& recvBuffer, Drv::ByteStreamStatus status) {
    if (status == Drv::ByteStreamStatus::SEND_RETRY) {
        this->drvAsyncSendOut_out(0, recvBuffer);
    }
    // Reset retry count.
}
""",
        encoding="utf-8",
    )
    (test_dir / "ComStubTestMain.cpp").write_text("TEST(Sync, Retry) {}\n", encoding="utf-8")
    (test_dir / "ComStubTester.hpp").write_text("class ComStubTester : public ComStubGTestBase {};\n", encoding="utf-8")
    (test_dir / "ComStubTester.cpp").write_text("#include \"ComStubTester.hpp\"\n", encoding="utf-8")
    command = write_fake_codrax(
        tmp_path,
        """
- [target_behavior] Svc/ComStub/ComStub.cpp:2 F Prime component port handler retries through drvAsyncSendOut_out.
- [test_surfaces] Svc/ComStub/test/ut/ComStubTestMain.cpp:1 existing F Prime unit test entrypoint.
- [test_surfaces] Svc/ComStub/test/ut/ComStubTester.hpp:1 existing F Prime Tester/GTestBase harness.
- [test_surfaces] Svc/ComStub/test/ut/ComStubTester.cpp:1 existing F Prime Tester implementation.
""",
    )
    enable_codrax(root, command)

    analysis = analyze_target(root, "Svc/ComStub/ComStub.cpp", run_id="comstub")

    assert analysis.selected_test_type == "Message Interface Test / Component Test (CODRAX-cited F Prime Harness)"
    assert analysis.observed_features.lifecycle_signals == []
    assert analysis.planned_files[:3] == [
        "Svc/ComStub/test/ut/ComStubTestMain.cpp",
        "Svc/ComStub/test/ut/ComStubTester.hpp",
        "Svc/ComStub/test/ut/ComStubTester.cpp",
    ]


def test_generic_layers_do_not_embed_project_specific_terms() -> None:
    generic_files = [
        REPO_ROOT / "src" / "gtestcov" / "analyzer.py",
        REPO_ROOT / "src" / "gtestcov" / "audit.py",
        REPO_ROOT / "src" / "gtestcov" / "codrax.py",
        REPO_ROOT / "src" / "gtestcov" / "dependency.py",
        REPO_ROOT / "src" / "gtestcov" / "discovery.py",
        REPO_ROOT / "src" / "gtestcov" / "models.py",
        REPO_ROOT / "src" / "gtestcov" / "next_round.py",
        REPO_ROOT / "src" / "gtestcov" / "obligations.py",
        REPO_ROOT / "src" / "gtestcov" / "opencode.py",
        REPO_ROOT / "src" / "gtestcov" / "permissions.py",
        REPO_ROOT / "src" / "gtestcov" / "preflight.py",
        REPO_ROOT / "src" / "gtestcov" / "task.py",
        REPO_ROOT / "src" / "gtestcov" / "understanding.py",
        REPO_ROOT / "src" / "gtestcov" / "verify.py",
    ]
    forbidden = [
        "F Prime",
        "fprime",
        "PX4",
        "px4",
        "uORB",
        "ORB_ID",
        "GTestBase",
        "ModuleParams",
        "EMAP",
        "dependency.xml",
        "west.yml",
        "project.yml",
        "ADC_REGISTER",
        "DAC_REGISTER",
        "PWM_REGISTER",
        "HW_",
        "HWTEST",
    ]

    for path in generic_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token!r} leaked into generic layer {path}"


def test_dependency_xml_and_symbol_classification(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    profile = load_profile(root)
    deps = parse_dependency_xml(root, profile)
    symbol = classify_symbol(root, "EMAP_MemFree", profile)

    assert deps.manifest_path == "dependency.xml"
    assert {dep.name for dep in deps.dependencies} >= {"EMAP", "OSAL", "HAL", "CRC_LIB"}
    assert symbol.kind == "extern function"
    assert any("emap_memory.h" in location for location in symbol.locations)
    assert "fake/shim at external boundary" in symbol.recommendation


def test_analysis_classifies_codec_and_service(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)

    codec = analyze_target(root, "src/can_codec.cpp", run_id="codec")
    service = analyze_target(root, "src/energy_service.cpp", run_id="service")
    power = analyze_target(root, "src/power_limit.cpp", run_id="power")

    assert codec.selected_test_type == "Message Conformance Test"
    assert "Lifecycle Test" in service.selected_test_type
    assert "Unit Test" in power.selected_test_type
    assert (root / ".gtestcov" / "runs" / "codec" / "decision_report.md").exists()


def test_analysis_writes_test_obligation_matrix(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)

    analysis = analyze_target(root, "src/energy_service.cpp", run_id="obligations")
    decision = (root / ".gtestcov" / "runs" / "obligations" / "decision_report.md").read_text(encoding="utf-8")
    obligations = {item.obligation_id: item for item in analysis.test_obligations}

    assert "lifecycle_init_shutdown_smoke" in obligations
    assert "message_boundary_observable_effects" in obligations
    assert obligations["lifecycle_init_shutdown_smoke"].status == "ready"
    assert any(ref.startswith("src/energy_service.cpp:") for ref in obligations["lifecycle_init_shutdown_smoke"].evidence)
    assert "## Test Obligation Matrix" in decision
    assert (root / ".gtestcov" / "runs" / "obligations" / "test_obligations.json").exists()
    assert (root / ".gtestcov" / "runs" / "obligations" / "test_obligations.md").exists()


def test_task_package_limits_weak_ai_write_scope(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    analysis, task_path = build_task(root, "src/energy_service.cpp", run_id="task")
    task = task_path.read_text(encoding="utf-8")
    run_dir = root / ".gtestcov" / "runs" / "task"
    warmup = json.loads((run_dir / "opencode_permission_warmup.json").read_text(encoding="utf-8"))

    assert analysis.run_id == "task"
    assert "Allowed Write Paths" in task
    assert "OpenCode Permission Warmup" in task
    assert "source_change_request.md" in task
    assert "Do not edit production source files" in task
    assert "Test Obligation Matrix" in task
    assert "lifecycle_init_shutdown_smoke" in task
    assert "Implement every obligation with status `ready`" in task
    assert "Preflight before build/test/coverage" in task
    assert "Required Test Case Description" in task
    assert "/*" in task
    assert "what this test really exercises" in task
    assert (run_dir / "opencode_permission_warmup.md").exists()
    assert "src/energy_service.cpp" in warmup["read_files"]
    assert "src/energy_service.cpp" in warmup["forbidden_write_paths"]
    assert "src/energy_service.cpp" not in warmup["planned_write_files"]
    assert all(not Path(path).is_absolute() for key in ("read_files", "planned_write_files") for path in warmup[key])
    assert all("\\" not in path for key in ("read_files", "planned_write_files") for path in warmup[key])


def test_codrax_evidence_is_written_to_reports_and_task(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- responsibility: src/energy_service.cpp:12 handles Init and service state.
- dependency: external/emap/include/emap_memory.h:5 EMAP_MemFree is used.
- existing test: tests/energy_service_component_test.cpp:20 has service fixture coverage.
- harness: tests/support/harness/energy_service_harness.hpp:1 should be reused.
- risk: Init/Shutdown ordering matters; do not invent HAL or NVM behavior.
""",
    )
    enable_codrax(root, command)

    analysis = analyze_target(root, "src/energy_service.cpp", run_id="codrax")
    _, task_path = build_task(root, "src/energy_service.cpp", run_id="codrax-task")
    decision = (root / ".gtestcov" / "runs" / "codrax" / "decision_report.md").read_text(encoding="utf-8")
    task = task_path.read_text(encoding="utf-8")

    assert analysis.codrax_evidence.status == "ok"
    assert "src/energy_service.cpp" in analysis.codrax_evidence.related_files
    assert "src/energy_service.cpp:12" in analysis.codrax_evidence.file_line_refs
    assert "reuse_existing_harness_or_fixture" in {item.obligation_id for item in analysis.test_obligations}
    assert "## CODRAX Evidence" in decision
    assert "## Test Obligation Matrix" in decision
    assert "tests/energy_service_component_test.cpp:20" in decision
    assert "CODRAX Evidence" in task
    assert "reuse_existing_harness_or_fixture" in task
    assert "manual_review_needed.md" in task
    assert "external/emap/include/emap_memory.h:5" in task


def test_codrax_unavailable_falls_back_to_static_analysis(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    enable_codrax(root, str(tmp_path / "missing_codrax"))

    analysis = analyze_target(root, "src/energy_service.cpp", run_id="codrax-missing")
    decision = (root / ".gtestcov" / "runs" / "codrax-missing" / "decision_report.md").read_text(encoding="utf-8")

    assert analysis.codrax_evidence.status == "unavailable"
    assert "codrax_evidence_needs_manual_review" in {item.obligation_id for item in analysis.test_obligations}
    assert "Lifecycle Test" in analysis.selected_test_type
    assert "weak AI must not invent missing dependencies" in decision


def test_codrax_check_and_evidence_cli_with_fake_command(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- probe: CMakeLists.txt:1 is the build entry.
- target: src/energy_service.cpp:12 calls Init and Shutdown.
""",
    )
    enable_codrax(root, command)

    check = codrax_check(root)
    evidence = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "evidence",
            "--project-root",
            str(root),
            "--target",
            "src/energy_service.cpp",
            "--run-id",
            "evidence-cli",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    cli_check = subprocess.run(
        [sys.executable, "-m", "gtestcov.cli", "codrax-check", "--project-root", str(root)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert check["status"] == "ok"
    assert "CMakeLists.txt:1" in check["file_line_refs"]
    assert evidence.returncode == 0
    assert json.loads(evidence.stdout)["status"] == "ok"
    assert (root / ".gtestcov" / "runs" / "evidence-cli" / "codrax_evidence.json").exists()
    assert cli_check.returncode == 0
    assert json.loads(cli_check.stdout)["status"] == "ok"


def test_codrax_auto_discovers_alternate_cli_protocol(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    enable_codrax(root, write_fake_codrax_alt_cli(tmp_path))

    analysis = analyze_target(root, "src/energy_service.cpp", run_id="codrax-alt")
    check = codrax_check(root)

    assert analysis.codrax_evidence.status == "ok"
    assert analysis.codrax_evidence.invocation == "ask_path_prompt_flags"
    assert "src/energy_service.cpp:12" in analysis.codrax_evidence.file_line_refs
    assert check["discovery"]["supported"] is True
    assert check["selected_invocation"] == "ask_path_prompt_flags"


def test_codrax_args_template_handles_unrecognized_cli_protocol(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = write_fake_codrax_template_only(tmp_path)
    profile.evidence.codrax.args_template = ["probe", "--root", "{repo}", "--question", "{request}"]
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    analysis = analyze_target(root, "src/energy_service.cpp", run_id="codrax-template")
    check = codrax_check(root)

    assert analysis.codrax_evidence.status == "ok"
    assert analysis.codrax_evidence.invocation == "args_template"
    assert "src/energy_service.cpp:12" in analysis.codrax_evidence.file_line_refs
    assert check["discovery"]["selected_invocation"] == "args_template"


def test_profile_sync_updates_profile_with_codrax_evidence(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.build_file: CMakeLists.txt # CMakeLists.txt:1
- build.candidate_build_files: CMakeLists.txt, tests/CMakeLists.txt # CMakeLists.txt:1
- build.build_command: cmake -S . -B build && cmake --build build # CMakeLists.txt:1
- build.incremental_build_command: cmake --build build --target energy_tests # CMakeLists.txt:1
- build.filtered_test_command: ctest --test-dir build -R EnergyService # CMakeLists.txt:1
- build.target_coverage_command: gcovr -r . --filter src/energy_service.cpp --xml -o coverage.xml # CMakeLists.txt:1
- test_support.test_dirs: tests # tests/energy_service_component_test.cpp:1
- test_support.test_build_config_paths: CMakeLists.txt # CMakeLists.txt:1
- evidence.codrax.direct_mode.enabled: true # project_profile.yaml:1
""",
    )
    enable_codrax(root, command)

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync", line_coverage=82, build_file="CMakeLists.txt")
    profile = load_profile(root)

    assert result["status"] == "ok"
    assert result["updated"] is True
    assert Path(result["backup_path"]).exists()
    assert (root / ".gtestcov" / "runs" / "profile-sync" / "profile_evidence.md").exists()
    assert profile.build.incremental_build_command == "cmake --build build --target energy_tests"
    assert profile.build.build_file == "CMakeLists.txt"
    assert profile.build.candidate_build_files == ["CMakeLists.txt", "tests/CMakeLists.txt"]
    assert profile.build.filtered_test_command == "ctest --test-dir build -R EnergyService"
    assert profile.build.target_coverage_command.startswith("gcovr -r . --filter")
    assert profile.test_support.test_build_config_paths == ["CMakeLists.txt"]
    assert profile.targets.default_line_coverage == 82
    assert profile.evidence.codrax.direct_mode.enabled is True
    assert result["build_file_comparison"]["status"] == "matched"


def test_profile_sync_stops_when_user_build_file_conflicts_with_codrax(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.build_file: other/CMakeLists.txt # other/CMakeLists.txt:1
- build.candidate_build_files: other/CMakeLists.txt # other/CMakeLists.txt:1
- build.filtered_test_command: ctest --test-dir other-build -R EnergyService # other/CMakeLists.txt:1
""",
    )
    enable_codrax(root, command)

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-mismatch", line_coverage=82, build_file="CMakeLists.txt")
    profile = load_profile(root)
    run_dir = root / ".gtestcov" / "runs" / "profile-sync-mismatch"

    assert result["status"] == "build_file_mismatch"
    assert result["updated"] is False
    assert profile.build.build_file == ""
    assert (run_dir / "manual_review_needed.md").exists()
    assert "Build File Anchor Comparison" in Path(result["profile_evidence_path"]).read_text(encoding="utf-8")


def test_build_file_anchor_comparison_is_case_sensitive(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.build_file: build/RunTests.sh # build/RunTests.sh:1
- build.candidate_build_files: build/RunTests.sh # build/RunTests.sh:1
""",
    )
    enable_codrax(root, command)

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-case", line_coverage=82, build_file="Build/RunTests.sh")
    run_dir = root / ".gtestcov" / "runs" / "profile-sync-case"
    evidence = Path(result["profile_evidence_path"]).read_text(encoding="utf-8")

    assert result["status"] == "build_file_mismatch"
    assert result["build_file_comparison"]["user_build_file"] == "Build/RunTests.sh"
    assert result["build_file_comparison"]["codrax_candidates"] == ["build/RunTests.sh"]
    assert "Build/RunTests.sh" in evidence
    assert "build/RunTests.sh" in evidence
    assert (run_dir / "manual_review_needed.md").exists()


def test_profile_sync_stops_when_user_build_file_has_no_codrax_candidates(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- test_support.test_dirs: tests # tests/energy_service_component_test.cpp:1
""",
    )
    enable_codrax(root, command)

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-no-candidates", line_coverage=82, build_file="build_entry.sh")
    profile = load_profile(root)
    run_dir = root / ".gtestcov" / "runs" / "profile-sync-no-candidates"

    assert result["status"] == "build_file_unverified"
    assert result["updated"] is False
    assert profile.build.build_file == ""
    assert (run_dir / "manual_review_needed.md").exists()


def test_cover_builds_single_file_task_with_coverage_goal(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.build_file: CMakeLists.txt # CMakeLists.txt:1
- build.candidate_build_files: CMakeLists.txt # CMakeLists.txt:1
- build.filtered_test_command: ctest --test-dir build -R EnergyService # CMakeLists.txt:1
- build.target_coverage_command: gcovr -r . --filter src/energy_service.cpp --xml -o coverage.xml # CMakeLists.txt:1
- test_support.test_dirs: tests # tests/energy_service_component_test.cpp:1
- test_support.test_build_config_paths: CMakeLists.txt # CMakeLists.txt:1
""",
    )
    enable_codrax(root, command)

    result = cover_target(root, "src/energy_service.cpp", line_coverage=88, run_id="cover", build_file="CMakeLists.txt")
    task = Path(result["task_path"]).read_text(encoding="utf-8")
    goal = json.loads((root / ".gtestcov" / "runs" / "cover" / "coverage_goal.json").read_text(encoding="utf-8"))

    assert result["status"] == "task_ready"
    assert goal == {
        "target": "src/energy_service.cpp",
        "line_coverage": 88.0,
        "metric": "target_file_line_coverage",
    }
    assert "Target line coverage goal: `88%" in task
    assert "CMakeLists.txt" in task
    assert "User build file anchor" in task
    assert "Do not edit the target file `src/energy_service.cpp`" in task


def test_memory_layer_writes_context_handoff_and_project_memory(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.build_file: CMakeLists.txt # CMakeLists.txt:1
- build.candidate_build_files: CMakeLists.txt # CMakeLists.txt:1
- build.filtered_test_command: ctest --test-dir build -R EnergyService # CMakeLists.txt:1
- build.target_coverage_command: gcovr -r . --filter src/energy_service.cpp --xml -o coverage.xml # CMakeLists.txt:1
- test_support.test_dirs: tests # tests/energy_service_component_test.cpp:1
- test_support.harness_dir: tests/support/harness # tests/support/harness/energy_service_harness.hpp:1
- test_support.test_build_config_paths: CMakeLists.txt # CMakeLists.txt:1
""",
    )
    enable_codrax(root, command)

    result = cover_target(root, "src/energy_service.cpp", line_coverage=88, run_id="memory-cover", build_file="CMakeLists.txt")
    run_dir = root / ".gtestcov" / "runs" / "memory-cover"
    handoff = json.loads((run_dir / "handoff.json").read_text(encoding="utf-8"))
    project_memory = json.loads((root / ".gtestcov" / "memory" / "project_memory.json").read_text(encoding="utf-8"))
    task = Path(result["task_path"]).read_text(encoding="utf-8")
    warmup = json.loads((run_dir / "opencode_permission_warmup.json").read_text(encoding="utf-8"))

    assert handoff["target"] == "src/energy_service.cpp"
    assert handoff["coverage_goal"]["line_coverage"] == 88.0
    assert handoff["status"] == "task_ready"
    assert ".gtestcov/runs/memory-cover/handoff.md" in handoff["context_reload"]["read_first"]
    assert ".gtestcov/memory/project_memory.md" in handoff["context_reload"]["read_first"]
    assert ".gtestcov/runs/memory-cover/handoff.md" in warmup["read_files"]
    assert ".gtestcov/runs/memory-cover/resume_prompt.md" in warmup["read_files"]
    assert ".gtestcov/memory/project_memory.md" in warmup["read_files"]
    assert "Context Reload Required" in task
    assert "Do not infer project details" in task
    assert project_memory["scope"]["requires_git"] is False
    assert all(fact["sources"] for fact in project_memory["verified_facts"])
    assert any(fact["value"] == "CMakeLists.txt" for fact in project_memory["verified_facts"])
    assert any("CMakeLists.txt:1" in fact["evidence"] for fact in project_memory["verified_facts"])


def test_memory_project_root_does_not_require_git(tmp_path: Path) -> None:
    root = tmp_path / "openharmony_like_workspace"
    root.mkdir()
    (root / ".repo").mkdir()
    (root / "project_profile.yaml").write_text(
        """
project_name: no_git_workspace
test_support:
  test_dirs:
    - tests
build:
  build_file: build.sh
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "nogit"
    run_dir.mkdir(parents=True)
    write_coverage_goal(run_dir, "foundation/Foo.cpp", 80)

    result = refresh_memory(root, "nogit")
    handoff = show_memory(root, "nogit", "json")["content"]
    project_memory = json.loads((root / ".gtestcov" / "memory" / "project_memory.json").read_text(encoding="utf-8"))

    assert result["status"] == "initialized"
    assert handoff["scope"]["project_root"] == str(root.resolve())
    assert project_memory["scope"]["requires_git"] is False
    assert not (root / ".git").exists()
    assert any(fact["value"] == "build.sh" for fact in project_memory["verified_facts"])


def test_verify_uses_filtered_commands_and_target_file_coverage(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text(
        f"""
project_name: verify_project
build:
  incremental_build_command: '"{sys.executable}" -c "print(\\"incremental build\\")"'
  filtered_test_command: '"{sys.executable}" -c "print(\\"filtered test\\")"'
  target_coverage_command: '"{sys.executable}" -c "print(\\"target coverage\\")"'
  coverage_xml: coverage.xml
targets:
  default_line_coverage: 80.0
""",
        encoding="utf-8",
    )
    (root / "coverage.xml").write_text(
        '<coverage><packages><package><classes><class filename="src/foo.cpp" line-rate="0.85" /></classes></package></packages></coverage>',
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "verify"
    run_dir.mkdir(parents=True)
    write_coverage_goal(run_dir, "src/foo.cpp", 80)

    verify = verify_iteration(root, run_id="verify")

    assert verify["commands"]["build"]["stdout"].strip() == "incremental build"
    assert verify["commands"]["test"]["stdout"].strip() == "filtered test"
    assert verify["commands"]["coverage"]["stdout"].strip() == "target coverage"
    assert verify["coverage"]["line_rate_percent"] == 85.0
    assert verify["coverage"]["meets_threshold"] is True
    assert verify["passed"] is True


def write_target_coverage(root: Path, target: str, rate: float) -> None:
    (root / "coverage.xml").write_text(
        f'<coverage><packages><package><classes><class filename="{target}" line-rate="{rate}" /></classes></package></packages></coverage>',
        encoding="utf-8",
    )


def make_coverage_project(tmp_path: Path, codrax_command: str = "") -> Path:
    root = tmp_path / "coverage_project"
    root.mkdir()
    profile = load_profile(root)
    profile.build.coverage_xml = "coverage.xml"
    if codrax_command:
        profile.evidence.codrax.enabled = True
        profile.evidence.codrax.command = codrax_command
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")
    run_dir = root / ".gtestcov" / "runs" / "coverage-loop"
    run_dir.mkdir(parents=True)
    write_coverage_goal(run_dir, "src/foo.cpp", 80)
    return root


def test_verify_generates_next_round_when_target_coverage_is_low(tmp_path: Path) -> None:
    command = write_fake_codrax(
        tmp_path,
        """
- uncovered branch: src/foo.cpp:12 error path needs a fixture case.
- reuse fixture: tests/foo_test.cpp:4 has existing gtest setup.
""",
    )
    root = make_coverage_project(tmp_path, command)
    write_target_coverage(root, "src/foo.cpp", 0.60)

    verify = verify_iteration(root, run_id="coverage-loop")
    run_dir = root / ".gtestcov" / "runs" / "coverage-loop"
    history = json.loads((run_dir / "coverage_history.json").read_text(encoding="utf-8"))

    assert verify["passed"] is False
    assert verify["next_round"]["status"] == "next_task_ready"
    assert verify["next_round"]["history"]["coverage_phase"] == "branch_expansion"
    assert history["entries"][0]["current_coverage"] == 60.0
    assert history["entries"][0]["coverage_phase"] == "branch_expansion"
    assert history["entries"][0]["improvement"] is None
    assert (run_dir / "next_round_analysis.md").exists()
    assert (run_dir / "next_task.md").exists()
    assert not (run_dir / "stagnation_report.md").exists()
    handoff = json.loads((run_dir / "handoff.json").read_text(encoding="utf-8"))
    assert handoff["status"] == "coverage_below_target"
    assert handoff["coverage_history"]["coverage_phase"] == "branch_expansion"
    assert ".gtestcov/runs/coverage-loop/next_task.md" in handoff["context_reload"]["read_first"]


def test_coverage_improvement_resets_stagnation_count(tmp_path: Path) -> None:
    root = make_coverage_project(tmp_path)
    write_target_coverage(root, "src/foo.cpp", 0.60)
    verify_iteration(root, run_id="coverage-loop")
    write_target_coverage(root, "src/foo.cpp", 0.68)

    verify = verify_iteration(root, run_id="coverage-loop")
    history = verify["next_round"]["history"]

    assert verify["next_round"]["status"] == "next_task_ready"
    assert history["entries"][-1]["improvement"] == 8.0
    assert history["entries"][-1]["coverage_phase"] == "branch_expansion"
    assert history["consecutive_stagnant_rounds"] == 0
    assert history["stagnated"] is False


def test_consecutive_low_improvement_generates_stagnation_report(tmp_path: Path) -> None:
    command = write_fake_codrax(
        tmp_path,
        """
- blocker: src/foo.cpp:20 branch may require source_change_request.md if no seam exists.
""",
    )
    root = make_coverage_project(tmp_path, command)
    for rate in [0.60, 0.62, 0.63, 0.64]:
        write_target_coverage(root, "src/foo.cpp", rate)
        verify = verify_iteration(root, run_id="coverage-loop")

    run_dir = root / ".gtestcov" / "runs" / "coverage-loop"
    history = verify["next_round"]["history"]

    assert verify["next_round"]["status"] == "stagnated"
    assert history["consecutive_stagnant_rounds"] == 3
    assert (run_dir / "stagnation_report.md").exists()
    assert not (run_dir / "next_task.md").exists()


def test_met_target_updates_history_without_next_task(tmp_path: Path) -> None:
    root = make_coverage_project(tmp_path)
    write_target_coverage(root, "src/foo.cpp", 0.85)

    verify = verify_iteration(root, run_id="coverage-loop")
    run_dir = root / ".gtestcov" / "runs" / "coverage-loop"

    assert verify["passed"] is True
    assert verify["next_round"]["status"] == "met_target"
    assert verify["next_round"]["history"]["met_target"] is True
    assert verify["next_round"]["history"]["coverage_phase"] == "met_target"
    assert not (run_dir / "next_task.md").exists()
    assert not (run_dir / "stagnation_report.md").exists()


def test_next_round_with_codrax_unavailable_is_conservative(tmp_path: Path) -> None:
    root = make_coverage_project(tmp_path)
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = str(tmp_path / "missing_codrax")
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")
    write_target_coverage(root, "src/foo.cpp", 0.60)

    verify = verify_iteration(root, run_id="coverage-loop")
    analysis = (root / ".gtestcov" / "runs" / "coverage-loop" / "next_round_analysis.md").read_text(encoding="utf-8")

    assert verify["next_round"]["status"] == "next_task_ready"
    assert verify["next_round"]["codrax_evidence"]["status"] == "unavailable"
    assert "CODRAX Evidence" in analysis


def test_low_coverage_values_select_expected_phases(tmp_path: Path) -> None:
    cases = [
        (0.05, "bootstrap"),
        (0.30, "characterization"),
        (0.60, "branch_expansion"),
        (0.75, "precision_closure"),
    ]
    for rate, phase in cases:
        case_dir = tmp_path / phase
        case_dir.mkdir()
        root = make_coverage_project(case_dir)
        write_target_coverage(root, "src/foo.cpp", rate)

        verify = verify_iteration(root, run_id="coverage-loop")
        task = (root / ".gtestcov" / "runs" / "coverage-loop" / "next_task.md").read_text(encoding="utf-8")

        assert verify["next_round"]["history"]["coverage_phase"] == phase
        assert f"Coverage phase: `{phase}`" in task


def test_coverage_target_not_found_enters_mapping_phase(tmp_path: Path) -> None:
    root = make_coverage_project(tmp_path)
    write_target_coverage(root, "src/other.cpp", 0.90)

    verify = verify_iteration(root, run_id="coverage-loop")
    task = (root / ".gtestcov" / "runs" / "coverage-loop" / "next_task.md").read_text(encoding="utf-8")

    assert verify["coverage"]["target_found"] is False
    assert verify["next_round"]["history"]["coverage_phase"] == "coverage_mapping_blocked"
    assert "coverage mapping" in task


def test_direct_codrax_mode_requires_audit_log(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text(
        """
project_name: direct_mode_project
evidence:
  codrax:
    direct_mode:
      enabled: true
      require_audit_log: true
""",
        encoding="utf-8",
    )
    verify = verify_iteration(root, run_id="direct-missing")

    assert verify["passed"] is False
    assert "missing_codrax_direct_log" in {item["check"] for item in verify["audit"]["violations"]}

    run_dir = root / ".gtestcov" / "runs" / "direct-ok"
    run_dir.mkdir(parents=True)
    (run_dir / "codrax_direct_log.md").write_text(
        "- question: find test target\n- conclusion: CMakeLists.txt:1 defines it.\n",
        encoding="utf-8",
    )
    verify = verify_iteration(root, run_id="direct-ok")

    assert "missing_codrax_direct_log" not in {item["check"] for item in verify["audit"]["violations"]}


def test_verify_audits_modified_files_write_scope(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text(
        """
project_name: write_scope_project
build:
  build_command: python3 -c "print('should not run')"
test_support:
  test_dirs:
    - tests
  test_build_config_paths:
    - CMakeLists.txt
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "write-scope"
    run_dir.mkdir(parents=True)
    (run_dir / "modified_files.txt").write_text(
        "tests/foo_test.cpp\nCMakeLists.txt\nsrc/foo.cpp\n",
        encoding="utf-8",
    )

    verify = verify_iteration(root, run_id="write-scope", target="src/foo.cpp")
    checks = {item["check"] for item in verify["audit"]["violations"]}

    assert "target_file_modified" in checks
    assert "write_scope_violation" not in checks
    assert verify["blocked_by_preflight"] is True
    assert verify["commands"]["build"]["skipped"] is True
    assert "should not run" not in verify["commands"]["build"]["stdout"]
    assert (run_dir / "preflight_check.json").exists()
    assert (run_dir / "preflight_fix_task.md").exists()
    assert verify["passed"] is False


def test_preflight_blocks_weak_ai_edits_to_memory_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text(
        """
project_name: protected_memory_project
test_support:
  test_dirs:
    - tests
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "protected"
    run_dir.mkdir(parents=True)
    (run_dir / "task.md").write_text("# task\n", encoding="utf-8")
    (run_dir / "modified_files.txt").write_text(
        ".gtestcov/runs/protected/handoff.md\n.gtestcov/memory/project_memory.json\n",
        encoding="utf-8",
    )

    result = preflight_check(root, run_id="protected", target="src/foo.cpp")
    checks = {item["check"] for item in result["audit"]["violations"]}

    assert result["passed"] is False
    assert "protected_gtestcov_state_modified" in checks
    assert (run_dir / "handoff.json").exists()
    assert (run_dir / "preflight_fix_task.md").exists()


def test_preflight_blocks_task_runs_without_modified_files_log(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text(
        """
project_name: missing_modified_files_project
test_support:
  test_dirs:
    - tests
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "missing-log"
    run_dir.mkdir(parents=True)
    (run_dir / "task.md").write_text("# generated task\n", encoding="utf-8")

    result = preflight_check(root, run_id="missing-log", target="src/foo.cpp")

    assert result["passed"] is False
    assert "missing_modified_files_log" in {item["check"] for item in result["audit"]["violations"]}
    assert (run_dir / "preflight_fix_task.md").exists()


def test_preflight_check_cli_and_codrax_blocker(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "tests").mkdir()
    command = write_fake_codrax(
        tmp_path,
        "- preflight_blocker: tests/foo_test.cpp:3 references a missing fake; src/foo.cpp:1 owns the dependency.\n",
    )
    (root / "project_profile.yaml").write_text(
        f"""
project_name: preflight_project
test_support:
  test_dirs:
    - tests
evidence:
  codrax:
    enabled: true
    command: {command!r}
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "preflight"
    run_dir.mkdir(parents=True)
    (run_dir / "modified_files.txt").write_text("tests/foo_test.cpp\n", encoding="utf-8")

    result = preflight_check(root, run_id="preflight", target="src/foo.cpp")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "check",
            "--project-root",
            str(root),
            "--run-id",
            "preflight",
            "--target",
            "src/foo.cpp",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result["passed"] is False
    assert "codrax_preflight_blocker" in {item["check"] for item in result["audit"]["violations"]}
    assert (run_dir / "preflight_fix_task.md").exists()
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["blocked"] is True


def test_preflight_requires_descriptions_for_modified_test_cases(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "project_profile.yaml").write_text(
        """
project_name: described_tests_project
test_support:
  test_dirs:
    - tests
""",
        encoding="utf-8",
    )
    (root / "tests" / "foo_test.cpp").write_text(
        """
#include <gtest/gtest.h>

TEST(FooTest, MissingDescription) {
    EXPECT_EQ(1, 1);
}
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "descriptions"
    run_dir.mkdir(parents=True)
    (run_dir / "modified_files.txt").write_text("tests/foo_test.cpp\n", encoding="utf-8")

    result = preflight_check(root, run_id="descriptions", target="src/foo.cpp")

    assert result["passed"] is False
    assert "missing_test_case_description" in {item["check"] for item in result["audit"]["violations"]}
    assert "test_value" in (run_dir / "preflight_fix_task.md").read_text(encoding="utf-8")


def test_preflight_accepts_low_value_coverage_case_when_value_is_explicit(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "project_profile.yaml").write_text(
        """
project_name: described_tests_project
test_support:
  test_dirs:
    - tests
""",
        encoding="utf-8",
    )
    (root / "tests" / "foo_test.cpp").write_text(
        """
#include <gtest/gtest.h>

/*
Test Case: FooTest.CoversTrivialDefaultBranch
Value: coverage-only, low business value; keeps target line coverage honest.
Steps: Arrange default input; call target wrapper; observe stable result.
Inputs: default integer input and no dependency failures.
Expected Outputs: returns the default status without side effects.
*/
TEST(FooTest, CoversTrivialDefaultBranch) {
    EXPECT_EQ(1, 1);
}
""",
        encoding="utf-8",
    )
    run_dir = root / ".gtestcov" / "runs" / "descriptions-ok"
    run_dir.mkdir(parents=True)
    (run_dir / "modified_files.txt").write_text("tests/foo_test.cpp\n", encoding="utf-8")

    result = preflight_check(root, run_id="descriptions-ok", target="src/foo.cpp")

    assert result["passed"] is True
    assert not (run_dir / "preflight_fix_task.md").exists()


def test_diagnose_failure_writes_codrax_report(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- diagnosis: CMakeLists.txt:34 links test support; check missing fake source.
- next step: tests/energy_service_component_test.cpp:1 should stay test-side only.
""",
    )
    enable_codrax(root, command)
    run_dir = root / ".gtestcov" / "runs" / "failed"
    run_dir.mkdir(parents=True)
    (run_dir / "verify.json").write_text(
        json.dumps(
            {
                "passed": False,
                "commands": {"build": {"returncode": 1, "stdout": "", "stderr": "missing fake", "diagnostics": []}},
                "coverage": {},
                "audit": {"violations": []},
            }
        ),
        encoding="utf-8",
    )

    result = diagnose_failure(root, run_id="failed", target="src/energy_service.cpp")
    report = Path(result["diagnosis_path"]).read_text(encoding="utf-8")

    assert result["status"] == "ok"
    assert "CMakeLists.txt:34" in report
    assert (run_dir / "failure_diagnosis.json").exists()


def test_cli_module_entrypoint_runs_discover(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-m", "gtestcov.cli", "discover", "--project-root", str(root)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert '"inferred_build_system": "cmake"' in completed.stdout

    obligations = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "obligations",
            "--project-root",
            str(root),
            "--target",
            "src/energy_service.cpp",
            "--run-id",
            "cli-obligations",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert obligations.returncode == 0
    assert "lifecycle_init_shutdown_smoke" in obligations.stdout


def test_memory_cli_refresh_and_show(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text("project_name: memory_cli\n", encoding="utf-8")
    run_dir = root / ".gtestcov" / "runs" / "cli-memory"
    run_dir.mkdir(parents=True)
    write_coverage_goal(run_dir, "Src/Foo.cpp", 75)

    refresh = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "memory-refresh",
            "--project-root",
            str(root),
            "--run-id",
            "cli-memory",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    show = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "memory-show",
            "--project-root",
            str(root),
            "--run-id",
            "cli-memory",
            "--format",
            "json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert refresh.returncode == 0
    assert json.loads(refresh.stdout)["status"] == "initialized"
    assert show.returncode == 0
    shown = json.loads(show.stdout)
    assert shown["target"] == "Src/Foo.cpp"
    assert shown["coverage_goal"]["line_coverage"] == 75.0


def test_coverage_parser_and_generated_test_audit(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "project_profile.yaml").write_text(
        """
project_name: audit_project
build:
  coverage_xml: coverage.xml
coverage:
  changed_line: 70.0
""",
        encoding="utf-8",
    )
    (root / "coverage.xml").write_text('<coverage line-rate="0.75"></coverage>', encoding="utf-8")
    (root / "tests" / "bad_test.cpp").write_text(
        """
#include <gtest/gtest.h>
#define private public
TEST(BadTest, UsesDeath) {
    EXPECT_DEATH(Abort(), "");
}
""",
        encoding="utf-8",
    )

    parsed = parse_gcovr_xml(root / "coverage.xml")
    audit = audit_generated_tests(root)
    verify = verify_iteration(root, run_id="unit")

    assert parsed["line_rate_percent"] == 75.0
    assert {v["check"] for v in audit["violations"]} >= {"define_private_public", "ordinary_death_test"}
    assert verify["blocked_by_preflight"] is True
    assert verify["coverage"]["meets_threshold"] is False
    assert verify["commands"]["coverage"]["skipped"] is True
    assert verify["passed"] is False


def test_gcovr_summary_parser_reads_fprime_default_summary(tmp_path: Path) -> None:
    summary = tmp_path / "summary.txt"
    summary.write_text(
        """
lines: 98.5% (66 out of 67)
functions: 100.0% (10 out of 10)
branches: 85.2% (46 out of 54)
""",
        encoding="utf-8",
    )

    parsed = parse_gcovr_summary(summary)

    assert parsed["line_rate_percent"] == 98.5

    summary.write_text(
        """
------------------------------------------------------------------------------
                           GCC Code Coverage Report
------------------------------------------------------------------------------
File                                       Lines    Exec  Cover   Missing
------------------------------------------------------------------------------
Svc/ComStub/ComStub.cpp                       67      66    98%   34
------------------------------------------------------------------------------
TOTAL                                         67      66    98%
------------------------------------------------------------------------------
""",
        encoding="utf-8",
    )

    parsed = parse_gcovr_summary(summary)

    assert parsed["line_rate_percent"] == 98.0


def test_verify_fails_when_test_command_finds_no_tests(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project_profile.yaml").write_text(
        f"""
project_name: no_tests_project
build:
  test_command: '"{sys.executable}" -c "print(\\"No tests were found!!!\\")"'
coverage:
  changed_line: 70.0
""",
        encoding="utf-8",
    )

    verify = verify_iteration(root, run_id="no_tests")

    assert verify["commands"]["test"]["returncode"] == 1
    assert verify["commands"]["test"]["diagnostics"] == ["test command completed without discovering tests"]
    assert verify["passed"] is False
