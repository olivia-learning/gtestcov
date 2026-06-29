from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import gtestcov.version as version_module
from gtestcov.cover import cover_target
from gtestcov.coverage_goal import write_coverage_goal
from gtestcov.diagnose import diagnose_failure
from gtestcov.analyzer import analyze_target
from gtestcov.codrax import codrax_check, generate_codrax_evidence
from gtestcov.dependency import classify_symbol, parse_dependency_xml
from gtestcov.discovery import discover_project
from gtestcov.memory import refresh_memory, show_memory
from gtestcov.preflight import preflight_check
from gtestcov.profile_sync import profile_sync
import gtestcov.profile_sync as profile_sync_module
from gtestcov.profile import load_profile, profile_to_yaml
from gtestcov.task import build_task, render_task
from gtestcov.understanding import generate_project_understanding
from gtestcov.models import (
    AnalysisReport,
    CodraxEvidence,
    DependencyReport,
    DiscoveryReport,
    ProjectUnderstanding,
    TargetFeatures,
)
from gtestcov.upgrade import (
    build_install_manifest,
    install_doctor,
    rollback_apply,
    rollback_list,
    restore_custom,
    upgrade_apply,
    upgrade_inspect,
    write_install_manifest,
)
from gtestcov.version import get_version_info
from gtestcov.verify import audit_generated_tests, parse_gcovr_summary, parse_gcovr_xml, verify_iteration


REPO_ROOT = Path(__file__).resolve().parents[1]
MINI_REPO = REPO_ROOT / "examples" / "energy_mini_repo"


def copy_mini_repo(tmp_path: Path) -> Path:
    dst = tmp_path / "energy_mini_repo"
    shutil.copytree(MINI_REPO, dst)
    disable_codrax(dst)
    return dst


def write_fake_codrax(tmp_path: Path, output: str, returncode: int = 0) -> str:
    script = tmp_path / "fake_codrax.py"
    script.write_text(
        "import argparse\n"
        "import pathlib\n"
        "import sys\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--repo')\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--log-dir')\n"
        "args = parser.parse_args()\n"
        f"output = {output!r}\n"
        "if args.log_dir:\n"
        "    log_dir = pathlib.Path(args.log_dir)\n"
        "    log_dir.mkdir(parents=True, exist_ok=True)\n"
        "    (log_dir / 'fake_codrax.log').write_text(output, encoding='utf-8')\n"
        "sys.stdout.write(output)\n"
        f"sys.exit({returncode})\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def write_fake_codrax_build_anchor_retry(tmp_path: Path) -> str:
    script = tmp_path / "fake_codrax_anchor_retry.py"
    script.write_text(
        "import argparse\n"
        "import pathlib\n"
        "import sys\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--repo')\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--log-dir')\n"
        "args = parser.parse_args()\n"
        "if args.log_dir:\n"
        "    log_dir = pathlib.Path(args.log_dir)\n"
        "    log_dir.mkdir(parents=True, exist_ok=True)\n"
        "    (log_dir / 'fake_codrax.log').write_text(args.request, encoding='utf-8')\n"
        "if 'build-file anchor verification' in args.request:\n"
        "    sys.stdout.write('build.candidate_build_files: CMakeLists.txt  # CMakeLists.txt:1\\n')\n"
        "else:\n"
        "    sys.stdout.write('profile fields not visible yet\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def write_fake_streaming_codrax(tmp_path: Path, events: list[tuple[str, str, float]], returncode: int = 0) -> str:
    script = tmp_path / "fake_codrax_streaming.py"
    script.write_text(
        "import argparse\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--repo')\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--log-dir')\n"
        "args = parser.parse_args()\n"
        "log_file = None\n"
        "if args.log_dir:\n"
        "    log_dir = pathlib.Path(args.log_dir)\n"
        "    log_dir.mkdir(parents=True, exist_ok=True)\n"
        "    log_file = log_dir / 'fake_codrax_streaming.log'\n"
        f"events = {events!r}\n"
        "for stream, text, delay in events:\n"
        "    target = sys.stderr if stream == 'stderr' else sys.stdout\n"
        "    print(text, file=target, flush=True)\n"
        "    if log_file:\n"
        "        with log_file.open('a', encoding='utf-8') as handle:\n"
        "            handle.write(f'[{stream}] {text}\\n')\n"
        "    if delay:\n"
        "        time.sleep(delay)\n"
        f"sys.exit({returncode})\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def write_fake_long_running_codrax(tmp_path: Path) -> str:
    script = tmp_path / "fake_codrax_long.py"
    script.write_text(
        "import argparse\n"
        "import pathlib\n"
        "import sys\n"
        "import time\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--repo')\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--log-dir')\n"
        "args = parser.parse_args()\n"
        "log_file = None\n"
        "if args.log_dir:\n"
        "    log_dir = pathlib.Path(args.log_dir)\n"
        "    log_dir.mkdir(parents=True, exist_ok=True)\n"
        "    log_file = log_dir / 'fake_codrax_long.log'\n"
        "for index in range(300):\n"
        "    line = f'heartbeat {index}: src/energy_service.cpp:1 still working'\n"
        "    print(line, flush=True)\n"
        "    if log_file:\n"
        "        with log_file.open('a', encoding='utf-8') as handle:\n"
        "            handle.write(line + '\\n')\n"
        "    time.sleep(0.2)\n",
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
        "parser.add_argument('--log-dir')\n"
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
        "parser.add_argument('--log-dir')\n"
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


def disable_codrax(root: Path) -> None:
    profile = load_profile(root)
    profile.evidence.codrax.enabled = False
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


def test_default_profile_enables_codrax() -> None:
    profile = load_profile(Path("__missing_project_profile__"))

    assert profile.evidence.codrax.enabled is True
    assert profile.evidence.codrax.command == "codrax"


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
        REPO_ROOT / "src" / "gtestcov" / "upgrade.py",
        REPO_ROOT / "src" / "gtestcov" / "verify.py",
        REPO_ROOT / "src" / "gtestcov" / "version.py",
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

    assert analysis.codrax_evidence.status == "command_not_found"
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

    check = codrax_check(root, run_id="codrax-check-unit")
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
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "codrax-check",
            "--project-root",
            str(root),
            "--run-id",
            "codrax-check-cli",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    check_run_dir = root / ".gtestcov" / "runs" / "codrax-check-unit"
    cli_run_dir = root / ".gtestcov" / "runs" / "codrax-check-cli"

    assert check["status"] == "ok"
    assert check["run_id"] == "codrax-check-unit"
    assert Path(check["status_path"]).exists()
    assert Path(check["final_log_path"]).exists()
    assert "CMakeLists.txt:1" in check["file_line_refs"]
    assert (check_run_dir / "codrax_check.json").exists()
    assert (check_run_dir / "gtestcov_status.json").exists()
    assert (check_run_dir / "codrax_final_outputs" / "index.json").exists()
    assert evidence.returncode == 0
    assert json.loads(evidence.stdout)["status"] == "ok"
    evidence_run_dir = root / ".gtestcov" / "runs" / "evidence-cli"
    assert (evidence_run_dir / "codrax_evidence.json").exists()
    evidence_status = json.loads((evidence_run_dir / "gtestcov_status.json").read_text(encoding="utf-8"))
    assert evidence_status["phase"] == "evidence.done"
    assert evidence_status["codrax_status"] == "ok"
    assert cli_check.returncode == 0
    cli_check_data = json.loads(cli_check.stdout)
    assert cli_check_data["status"] == "ok"
    assert cli_check_data["run_id"] == "codrax-check-cli"
    assert (cli_run_dir / "codrax_check.json").exists()
    assert (cli_run_dir / "gtestcov_status.json").exists()
    assert (cli_run_dir / "codrax_final_outputs" / "index.json").exists()


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
    assert check["discovery"]["help_probes"] == []


def test_codrax_records_native_log_status_and_final_summary(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_streaming_codrax(
        tmp_path,
        [
            ("stdout", "- target: src/energy_service.cpp:12 handles Init.", 0.1),
            ("stderr", "progress: indexing tests/energy_service_component_test.cpp:20", 0.1),
            ("stdout", "- harness: tests/support/harness/energy_service_harness.hpp:1 can be reused.", 0),
        ],
    )
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    profile.evidence.codrax.idle_timeout_seconds = 2
    profile.evidence.codrax.max_runtime_seconds = 10
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    evidence, _ = generate_codrax_evidence(root, "src/energy_service.cpp", run_id="codrax-live")

    status_path = Path(evidence.status_path)
    native_log_dir = Path(evidence.native_log_dir)
    final_log = Path(evidence.final_log_path)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    native_text = "\n".join(path.read_text(encoding="utf-8") for path in native_log_dir.rglob("*") if path.is_file())
    final_text = final_log.read_text(encoding="utf-8")

    assert evidence.status == "ok"
    assert evidence.timeout_kind == ""
    assert "src/energy_service.cpp:12" in evidence.file_line_refs
    assert evidence.final_log_path
    assert status["status"] == "ok"
    assert native_log_dir.exists()
    assert evidence.native_log_files
    assert "progress: indexing" in native_text
    assert final_log.exists()
    assert "CODRAX Final Diagnostic Log" in final_text
    assert "progress: indexing" in final_text
    assert evidence.final_log_truncated is False
    assert evidence.final_log_size_bytes == final_log.stat().st_size


def test_codrax_long_running_with_activity_completes_and_records_outer_status(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_streaming_codrax(
        tmp_path,
        [
            ("stdout", "- phase 1: src/energy_service.cpp:12 indexing target.", 0.45),
            ("stderr", "progress: native log says repo map is still active", 0.45),
            ("stdout", "- phase 2: tests/energy_service_component_test.cpp:20 checking harness.", 0.45),
            ("stderr", "progress: native log says final answer is being prepared", 0.45),
            ("stdout", "- final: src/energy_service.cpp:18 cites shutdown behavior.", 0),
        ],
    )
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    profile.evidence.codrax.idle_timeout_seconds = 1
    profile.evidence.codrax.max_runtime_seconds = 10
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    understanding, _ = generate_project_understanding(root, "src/energy_service.cpp", run_id="codrax-long-complete")

    run_dir = root / ".gtestcov" / "runs" / "codrax-long-complete"
    codrax_status = json.loads((run_dir / "codrax_status.json").read_text(encoding="utf-8"))
    run_status = json.loads((run_dir / "gtestcov_status.json").read_text(encoding="utf-8"))
    index = json.loads((run_dir / "codrax_final_outputs" / "index.json").read_text(encoding="utf-8"))

    assert understanding.status == "ok"
    assert codrax_status["status"] == "ok"
    assert codrax_status["phase"] == "done"
    assert codrax_status["elapsed_seconds"] >= 1
    assert codrax_status["timeout_kind"] == ""
    assert run_status["phase"] == "evidence.done"
    assert run_status["codrax_status"] == "ok"
    assert index[-1]["status"] == "ok"
    assert Path(index[-1]["final_log_path"]).exists()


def test_codrax_idle_timeout_uses_activity_not_fixed_request_timeout(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_streaming_codrax(
        tmp_path,
        [("stdout", "- target: src/energy_service.cpp:12 starts analysis.", 2.0)],
    )
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    profile.evidence.codrax.idle_timeout_seconds = 1
    profile.evidence.codrax.max_runtime_seconds = 10
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    evidence, _ = generate_codrax_evidence(root, "src/energy_service.cpp", run_id="codrax-idle")

    assert evidence.status == "idle_timeout"
    assert evidence.timeout_kind == "idle"
    assert "no output for 1 seconds" in " ".join(evidence.notes)
    assert Path(evidence.status_path).exists()
    assert Path(evidence.final_log_path).exists()
    assert json.loads(Path(evidence.status_path).read_text(encoding="utf-8"))["status"] == "idle_timeout"


def test_codrax_max_runtime_timeout_stops_chatty_process(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    events = [
        ("stdout", f"- progress {index}: src/energy_service.cpp:12 still running.", 0.2)
        for index in range(30)
    ]
    command = write_fake_streaming_codrax(tmp_path, events)
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    profile.evidence.codrax.idle_timeout_seconds = 5
    profile.evidence.codrax.max_runtime_seconds = 1
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    evidence, _ = generate_codrax_evidence(root, "src/energy_service.cpp", run_id="codrax-max")

    assert evidence.status == "max_runtime_timeout"
    assert evidence.timeout_kind == "max_runtime"
    assert "max runtime of 1 seconds" in " ".join(evidence.notes)
    assert Path(evidence.status_path).exists()
    assert Path(evidence.final_log_path).exists()
    assert json.loads(Path(evidence.status_path).read_text(encoding="utf-8"))["status"] == "max_runtime_timeout"


def test_codrax_signal_termination_records_final_log(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    enable_codrax(root, write_fake_long_running_codrax(tmp_path))
    env = {**dict(subprocess.os.environ), "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.Popen(
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
            "signal-stop",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    status_path = root / ".gtestcov" / "runs" / "signal-stop" / "codrax_status.json"
    for _ in range(100):
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("status") == "running":
                break
        time.sleep(0.05)
    else:
        proc.kill()
        proc.wait(timeout=5)
        raise AssertionError("CODRAX status did not enter running state")

    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=10)

    assert proc.returncode in {128 + signal.SIGTERM, -signal.SIGTERM}, (stdout, stderr)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "terminated_by_signal"
    assert status["phase"] == "interrupted"
    assert status["final_log_path"]
    final_log = Path(status["final_log_path"]).read_text(encoding="utf-8")
    assert "terminated_by_signal" in final_log
    index_path = root / ".gtestcov" / "runs" / "signal-stop" / "codrax_final_outputs" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index[-1]["status"] == "terminated_by_signal"
    run_status = json.loads((root / ".gtestcov" / "runs" / "signal-stop" / "gtestcov_status.json").read_text(encoding="utf-8"))
    assert run_status["phase"] == "codrax.interrupted"
    assert run_status["codrax_status"] == "terminated_by_signal"


def test_codrax_final_log_is_bounded_and_keeps_tail(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    events = [("stdout", "- early build anchor: CMakeLists.txt:1 dependency evidence", 0)] + [
        ("stdout", f"- trace {index}: src/energy_service.cpp:{12 + index % 3} dependency detail " + ("x" * 40), 0)
        for index in range(80)
    ]
    command = write_fake_streaming_codrax(tmp_path, events)
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    profile.evidence.codrax.final_log_max_bytes = 700
    profile.evidence.codrax.native_log_tail_bytes = 4000
    profile.evidence.codrax.max_output_chars = 4000
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    evidence, _ = generate_codrax_evidence(root, "src/energy_service.cpp", run_id="codrax-trim")

    final_log = Path(evidence.final_log_path)
    log_text = final_log.read_text(encoding="utf-8", errors="replace")
    assert evidence.status == "ok"
    assert evidence.final_log_truncated is True
    assert evidence.final_log_size_bytes <= profile.evidence.codrax.final_log_max_bytes
    assert "CMakeLists.txt:1" in evidence.file_line_refs
    assert "[gtestcov] final CODRAX diagnostic log truncated" in log_text
    assert "trace 79" in log_text
    assert "trace 0" not in log_text


def test_codrax_file_line_evidence_comes_from_final_stdout_not_native_log(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_streaming_codrax(
        tmp_path,
        [
            ("stderr", "internal prompt mentions src/energy_service.cpp:12 but final answer does not cite it", 0),
            ("stdout", "I cannot cite a repository file line from the final answer.", 0),
        ],
    )
    profile = load_profile(root)
    profile.evidence.codrax.enabled = True
    profile.evidence.codrax.command = command
    profile.evidence.codrax.require_file_line = True
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    evidence, _ = generate_codrax_evidence(root, "src/energy_service.cpp", run_id="codrax-final-only")

    assert evidence.status == "insufficient"
    assert evidence.file_line_refs == []
    assert Path(evidence.final_log_path).exists()


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


def test_profile_sync_accepts_user_build_file_cited_in_codrax_final_output(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
The target uses the user-provided build file `CMakeLists.txt`; it registers the unit test at CMakeLists.txt:1.
No strict field list was emitted.
""",
    )
    enable_codrax(root, command)

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-prose-candidate", line_coverage=82, build_file="CMakeLists.txt")
    profile = load_profile(root)
    run_dir = root / ".gtestcov" / "runs" / "profile-sync-prose-candidate"

    assert result["status"] == "ok"
    assert result["updated"] is True
    assert result["build_file_comparison"]["status"] == "matched"
    assert result["updates"]["build.candidate_build_files"]["source"] == "codrax_final_output_fallback"
    assert profile.build.build_file == "CMakeLists.txt"
    assert profile.build.candidate_build_files == ["CMakeLists.txt"]
    assert not (run_dir / "manual_review_needed.md").exists()


def test_profile_sync_derives_test_dirs_from_codrax_existing_harness_evidence(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.candidate_build_files: CMakeLists.txt # CMakeLists.txt:1

Existing tests and harnesses:
- tests/energy_service_component_test.cpp:1 defines a GoogleTest component harness for this target.
- tests/support/fakes/fake_hal.hpp:1 provides a fake hardware boundary used by the tests.
- cmake/API.cmake:542 defines a unit test build helper and must remain build evidence only.
""",
    )
    enable_codrax(root, command)

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-test-dir-fallback", line_coverage=82, build_file="CMakeLists.txt")
    profile = load_profile(root)

    assert result["status"] == "ok"
    assert result["updated"] is True
    assert profile.test_support.test_dirs == ["tests"]
    assert result["updates"]["test_support.test_dirs"]["source"] == "codrax_existing_tests_fallback"
    assert result["updates"]["test_support.test_dirs"]["evidence"] == ["tests/energy_service_component_test.cpp:1"]


def test_profile_sync_reasks_codrax_when_build_file_candidates_are_missing(tmp_path: Path) -> None:
    root = copy_mini_repo(tmp_path)
    enable_codrax(root, write_fake_codrax_build_anchor_retry(tmp_path))

    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-anchor-retry", line_coverage=82, build_file="CMakeLists.txt")
    profile = load_profile(root)
    run_dir = root / ".gtestcov" / "runs" / "profile-sync-anchor-retry"
    index = json.loads((run_dir / "codrax_final_outputs" / "index.json").read_text(encoding="utf-8"))

    assert result["status"] == "ok"
    assert result["updated"] is True
    assert result["build_file_comparison"]["status"] == "matched"
    assert profile.build.build_file == "CMakeLists.txt"
    assert profile.build.candidate_build_files == ["CMakeLists.txt"]
    assert {entry["operation"] for entry in index} >= {"profile_sync", "profile_sync_build_file_anchor"}


def test_profile_sync_backup_falls_back_when_copy_metadata_fails(tmp_path: Path, monkeypatch) -> None:
    root = copy_mini_repo(tmp_path)
    command = write_fake_codrax(
        tmp_path,
        """
- build.candidate_build_files: CMakeLists.txt # CMakeLists.txt:1
""",
    )
    enable_codrax(root, command)

    def fail_copy2(_src, _dst):
        raise PermissionError("metadata copy denied")

    monkeypatch.setattr(profile_sync_module.shutil, "copy2", fail_copy2)
    result = profile_sync(root, "src/energy_service.cpp", run_id="profile-sync-copy-fallback", line_coverage=82, build_file="CMakeLists.txt")

    assert result["status"] == "ok"
    backup = Path(result["backup_path"])
    assert backup.exists()
    assert backup.read_text(encoding="utf-8")


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
    run_dir = root / ".gtestcov" / "runs" / "cover"
    task = Path(result["task_path"]).read_text(encoding="utf-8")
    goal = json.loads((run_dir / "coverage_goal.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "gtestcov_status.json").read_text(encoding="utf-8"))
    events = [json.loads(line)["phase"] for line in (run_dir / "gtestcov_events.ndjson").read_text(encoding="utf-8").splitlines() if line.strip()]
    final_output_index = json.loads((run_dir / "codrax_final_outputs" / "index.json").read_text(encoding="utf-8"))
    final_output_logs = sorted((run_dir / "codrax_final_outputs").glob("*.md"))
    cli_status = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "status",
            "--project-root",
            str(root),
            "--run-id",
            "cover",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

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
    assert status["phase"] == "cover.task_ready"
    assert "cover.start" in events
    assert "profile_sync.start" in events
    assert "analyze.codrax_understanding" in events
    assert "task.done" in events
    assert events.count("codrax.finished") >= 2
    assert len(final_output_logs) >= 2
    assert {entry["operation"] for entry in final_output_index} >= {"profile_sync", "project_understanding"}
    assert all(Path(entry["final_log_path"]).exists() for entry in final_output_index)
    assert cli_status.returncode == 0
    assert json.loads(cli_status.stdout)["gtestcov_status"]["phase"] == "cover.task_ready"


def test_task_allowed_paths_include_codrax_cited_test_dirs_from_project_understanding() -> None:
    profile = load_profile(Path("__missing_project_profile__"))
    evidence = CodraxEvidence(
        status="ok",
        harnesses=[
            "Existing test harness is Svc/ActiveRateGroup/test/ut/ActiveRateGroupTestMain.cpp:49.",
            "Build helper cmake/API.cmake:542 mentions unit tests but is not a test source directory.",
        ],
    )
    analysis = AnalysisReport(
        run_id="run",
        target="Svc/ActiveRateGroup/ActiveRateGroup.cpp",
        project_style=DiscoveryReport(project_root="."),
        dependency_resolution=DependencyReport(),
        observed_features=TargetFeatures(target="Svc/ActiveRateGroup/ActiveRateGroup.cpp"),
        observed_symbols=[],
        selected_test_type="Component Test",
        reason=[],
        required_support=[],
        safety_risks=[],
        planned_files=[],
        project_understanding=ProjectUnderstanding(codrax_evidence=evidence),
    )

    task = render_task(analysis, profile, line_coverage=70)

    assert "- Svc/ActiveRateGroup/test/ut" in task
    assert "- cmake" not in task


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
    build_script = root / "build_cmd.py"
    test_script = root / "test_cmd.py"
    coverage_script = root / "coverage_cmd.py"
    build_script.write_text("print('incremental build')\n", encoding="utf-8")
    test_script.write_text("print('filtered test')\n", encoding="utf-8")
    coverage_script.write_text("print('target coverage')\n", encoding="utf-8")
    (root / "project_profile.yaml").write_text(
        f"""
project_name: verify_project
build:
  incremental_build_command: '"{sys.executable}" "{build_script}"'
  filtered_test_command: '"{sys.executable}" "{test_script}"'
  target_coverage_command: '"{sys.executable}" "{coverage_script}"'
  coverage_xml: coverage.xml
targets:
  default_line_coverage: 80.0
evidence:
  codrax:
    enabled: false
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


def test_verify_command_timeout_is_reported_and_cli_override_works(tmp_path: Path) -> None:
    root = tmp_path / "timeout_project"
    root.mkdir()
    profile = load_profile(root)
    profile.build.build_command = f'"{sys.executable}" -c "import time; print(\'build started\', flush=True); time.sleep(2)"'
    profile.build.build_timeout_seconds = 30
    profile.evidence.codrax.enabled = False
    (root / "project_profile.yaml").write_text(profile_to_yaml(profile), encoding="utf-8")

    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "verify",
            "--project-root",
            str(root),
            "--run-id",
            "cli-timeout",
            "--build-timeout",
            "1",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    verify = json.loads(cli.stdout)
    verify_json = json.loads((root / ".gtestcov" / "runs" / "cli-timeout" / "verify.json").read_text(encoding="utf-8"))
    assert cli.returncode == 0
    assert verify["commands"]["build"]["timeout"] is True
    assert verify["commands"]["build"]["returncode"] == 124
    assert verify["commands"]["build"]["timeout_seconds"] == 1
    assert "timed out after 1 seconds" in verify["commands"]["build"]["diagnostics"][0]
    assert "build started" in verify["commands"]["build"]["stdout"]
    assert verify_json["commands"]["build"]["timeout"] is True
    assert verify["passed"] is False


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
    profile.evidence.codrax.enabled = False
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
    assert verify["next_round"]["codrax_evidence"]["status"] == "command_not_found"
    assert "CODRAX Evidence" in analysis


def test_verify_stops_next_round_when_verification_commands_are_not_configured(tmp_path: Path) -> None:
    root = make_coverage_project(tmp_path)

    verify = verify_iteration(root, run_id="coverage-loop")
    run_dir = root / ".gtestcov" / "runs" / "coverage-loop"

    assert verify["coverage"]["found"] is False
    assert verify["next_round"]["status"] == "verification_not_configured"
    assert verify["next_round"]["codrax_evidence"]["status"] == "skipped_verification_not_configured"
    assert (run_dir / "manual_review_needed.md").exists()
    assert not (run_dir / "next_task.md").exists()
    assert not (run_dir / "codrax_final_outputs").exists()


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
    enabled: false
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
evidence:
  codrax:
    enabled: false
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
evidence:
  codrax:
    enabled: false
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
evidence:
  codrax:
    enabled: false
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


def test_preflight_skips_codrax_when_local_violations_already_block(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    command = write_fake_codrax(
        tmp_path,
        "- preflight_blocker: tests/foo_test.cpp:3 should not be queried when local checks already block.\n",
    )
    (root / "project_profile.yaml").write_text(
        f"""
project_name: local_block_project
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
    run_dir = root / ".gtestcov" / "runs" / "local-block"
    run_dir.mkdir(parents=True)
    (run_dir / "task.md").write_text("# generated task\n", encoding="utf-8")

    result = preflight_check(root, run_id="local-block", target="src/foo.cpp")

    assert result["passed"] is False
    assert result["codrax_evidence"]["status"] == "skipped_due_local_violations"
    assert "codrax_preflight_blocker" not in {item["check"] for item in result["audit"]["violations"]}
    assert not (run_dir / "codrax_final_outputs").exists()


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
evidence:
  codrax:
    enabled: false
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
evidence:
  codrax:
    enabled: false
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
evidence:
  codrax:
    enabled: false
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
evidence:
  codrax:
    enabled: false
""",
        encoding="utf-8",
    )

    verify = verify_iteration(root, run_id="no_tests")

    assert verify["commands"]["test"]["returncode"] == 1
    assert verify["commands"]["test"]["diagnostics"] == ["test command completed without discovering tests"]
    assert verify["passed"] is False


def make_fake_tool_root(tmp_path: Path, name: str, marker: str) -> Path:
    tool = tmp_path / name
    package = tool / "src" / "gtestcov"
    package.mkdir(parents=True)
    (tool / "pyproject.toml").write_text(
        """
[project]
name = "gtestcov"
version = "9.9.9"
""",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("__version__ = '9.9.9'\n", encoding="utf-8")
    (package / "cli.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    return tool


def test_version_and_install_doctor_report_environment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project_profile.yaml").write_text("project_name: version_project\n", encoding="utf-8")

    info = get_version_info(REPO_ROOT)
    doctor = install_doctor(project_root=project, tool_root=REPO_ROOT, tool_home=tmp_path / "tool_home")

    assert info.version == "0.2.1"
    assert info.version_source == "pyproject.toml"
    assert info.memory_schema_version >= 1
    assert isinstance(info.git_dirty, bool)
    assert isinstance(info.git_modified_count, int)
    assert doctor["status"] == "ok"
    assert doctor["version"]["install_path"] == str(REPO_ROOT)
    assert doctor["version"]["version_source"] == "pyproject.toml"
    assert "git_dirty" in doctor["version"]
    assert doctor["project_root"] == str(project)


def test_version_info_reports_git_dirty_state(monkeypatch, tmp_path: Path) -> None:
    tool = tmp_path / "tool"
    tool.mkdir()
    (tool / ".git").mkdir()
    (tool / "pyproject.toml").write_text(
        """
[project]
name = "gtestcov"
version = "1.2.3"
""",
        encoding="utf-8",
    )

    def fake_git_status(_tool_root: Path, include_diff: bool = True) -> dict[str, object]:
        return {
            "available": True,
            "branch": "feature/version",
            "commit": "abc123",
            "modified_files": ["src/gtestcov/version.py"],
            "untracked_files": ["LOCAL.md"],
            "raw_status": [" M src/gtestcov/version.py", "?? LOCAL.md"],
            "diff": "" if not include_diff else "diff --git",
        }

    monkeypatch.setattr(version_module, "git_status", fake_git_status)
    monkeypatch.setattr(version_module, "git_identity", lambda _tool_root: {"available": True, "remote": "origin-url"})

    info = version_module.get_version_info(tool)
    data = info.as_dict()

    assert data["version"] == "1.2.3"
    assert data["version_source"] == "pyproject.toml"
    assert data["git_commit"] == "abc123"
    assert data["git_branch"] == "feature/version"
    assert data["git_remote"] == "origin-url"
    assert data["git_dirty"] is True
    assert data["git_modified_count"] == 2


def test_install_doctor_warns_on_zip_manifest_version_mismatch(tmp_path: Path) -> None:
    tool = make_fake_tool_root(tmp_path, "zip_tool", "zip")
    (tool / "gtestcov_install_manifest.json").write_text(
        json.dumps({"install_mode": "zip", "version": "0.1.0", "source": "gtestcov-v0.1.0.zip", "files": []}),
        encoding="utf-8",
    )

    doctor = install_doctor(tool_root=tool, tool_home=tmp_path / "tool_home")

    assert doctor["status"] == "warning"
    assert doctor["version"]["version"] == "9.9.9"
    assert doctor["version"]["zip_manifest_version"] == "0.1.0"
    assert any("does not match runtime tool version" in warning for warning in doctor["doctor_warnings"])


def test_zip_upgrade_inspect_apply_restore_and_rollback(tmp_path: Path) -> None:
    old_tool = make_fake_tool_root(tmp_path, "old_tool", "old")
    manifest = build_install_manifest(old_tool, source="gtestcov-v9.9.9.zip")
    write_install_manifest(old_tool, manifest)
    (old_tool / "src" / "gtestcov" / "cli.py").write_text("MARKER = 'user-custom'\n", encoding="utf-8")
    (old_tool / "LOCAL_NOTES.md").write_text("local note\n", encoding="utf-8")

    new_tool = make_fake_tool_root(tmp_path, "new_tool", "new")
    project = tmp_path / "cpp_project"
    run_dir = project / ".gtestcov" / "runs" / "old-run"
    run_dir.mkdir(parents=True)
    (project / "project_profile.yaml").write_text("project_name: upgrade_project\n", encoding="utf-8")
    (run_dir / "keep.txt").write_text("old run evidence\n", encoding="utf-8")

    tool_home = tmp_path / "tool_home"
    inspected = upgrade_inspect(
        tool_root=old_tool,
        project_root=project,
        target_ref="v0.2.1",
        install_mode="zip",
        upgrade_id="up-test",
        tool_home=tool_home,
    )
    report = Path(inspected["report_md"]).read_text(encoding="utf-8")
    refused = upgrade_apply(
        upgrade_id="up-test",
        approve_overwrite_tool_modifications=False,
        tool_home=tool_home,
        project_root=project,
    )

    assert inspected["status"] == "inspected"
    assert "Local dirty state: `true`" in report
    assert "src/gtestcov/cli.py" in report
    assert "LOCAL_NOTES.md" in report
    assert refused["status"] == "refused"
    assert (project / ".gtestcov" / "upgrade_slots" / "up-test" / "old" / ".gtestcov" / "runs" / "old-run" / "keep.txt").exists()

    applied = upgrade_apply(
        upgrade_id="up-test",
        approve_overwrite_tool_modifications=True,
        tool_home=tool_home,
        project_root=project,
        source_tool_root=new_tool,
        install_mode="zip",
        skip_venv_refresh=True,
    )
    assert applied["status"] == "applied"
    assert (tool_home / "current_slot").read_text(encoding="utf-8").strip() == applied["active_tool_slot"]
    assert (Path(applied["active_tool_path"]) / "src" / "gtestcov" / "cli.py").read_text(encoding="utf-8") == "MARKER = 'new'\n"
    assert applied["venv_refresh"]["status"] == "skipped"

    restored = restore_custom("up-test", tool_home=tool_home)
    listed = rollback_list(project, tool_home=tool_home)
    rolled_back = rollback_apply("up-test", project, approve=True, tool_home=tool_home, skip_venv_refresh=True)

    assert restored["status"] == "conflicts"
    assert any("src/gtestcov/cli.py" in item for item in restored["conflicts"])
    assert any("LOCAL_NOTES.md" in item for item in restored["actions"])
    assert listed["upgrade_count"] == 1
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["venv_refresh"]["status"] == "skipped"
    assert (project / ".gtestcov" / "runs" / "old-run" / "keep.txt").read_text(encoding="utf-8") == "old run evidence\n"
    assert (tool_home / "current_slot").read_text(encoding="utf-8").strip() == inspected["old_tool_slot"]


def test_upgrade_apply_blocks_invalid_memory_migration(tmp_path: Path) -> None:
    old_tool = make_fake_tool_root(tmp_path, "old_tool", "old")
    write_install_manifest(old_tool, build_install_manifest(old_tool, source="old.zip"))
    new_tool = make_fake_tool_root(tmp_path, "new_tool", "new")
    project = tmp_path / "cpp_project"
    memory_dir = project / ".gtestcov" / "memory"
    memory_dir.mkdir(parents=True)
    (project / "project_profile.yaml").write_text("project_name: bad_memory\n", encoding="utf-8")
    (memory_dir / "project_memory.json").write_text("{bad json", encoding="utf-8")
    tool_home = tmp_path / "tool_home"

    upgrade_inspect(
        tool_root=old_tool,
        project_root=project,
        target_ref="v0.2.1",
        install_mode="zip",
        upgrade_id="bad-memory",
        tool_home=tool_home,
    )
    applied = upgrade_apply(
        upgrade_id="bad-memory",
        approve_overwrite_tool_modifications=True,
        tool_home=tool_home,
        project_root=project,
        source_tool_root=new_tool,
        install_mode="zip",
        skip_venv_refresh=True,
    )

    assert applied["status"] == "blocked"
    assert "migration_report" in applied
    assert Path(applied["migration_report"]).exists()


def test_upgrade_apply_refreshes_reused_venv_when_provided(tmp_path: Path) -> None:
    old_tool = make_fake_tool_root(tmp_path, "old_tool", "old")
    write_install_manifest(old_tool, build_install_manifest(old_tool, source="old.zip"))
    new_tool = make_fake_tool_root(tmp_path, "new_tool", "new")
    project = tmp_path / "cpp_project"
    (project / ".gtestcov").mkdir(parents=True)
    (project / "project_profile.yaml").write_text("project_name: venv_refresh\n", encoding="utf-8")
    tool_home = tmp_path / "tool_home"
    log_path = tmp_path / "pip_args.json"
    fake_python = tmp_path / "fake_python"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import pathlib\n"
        "import sys\n"
        f"pathlib.Path({str(log_path)!r}).write_text(json.dumps(sys.argv), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    upgrade_inspect(
        tool_root=old_tool,
        project_root=project,
        target_ref="v0.2.1",
        install_mode="zip",
        upgrade_id="venv-refresh",
        tool_home=tool_home,
    )
    applied = upgrade_apply(
        upgrade_id="venv-refresh",
        approve_overwrite_tool_modifications=True,
        tool_home=tool_home,
        project_root=project,
        source_tool_root=new_tool,
        install_mode="zip",
        venv_path=fake_python,
    )

    pip_args = json.loads(log_path.read_text(encoding="utf-8"))
    assert applied["status"] == "applied"
    assert applied["venv_refresh"]["status"] == "refreshed"
    assert pip_args[1:5] == ["-m", "pip", "install", "--no-deps"]
    assert pip_args[-2] == "-e"
    assert pip_args[-1] == applied["active_tool_path"]


def test_upgrade_cli_refuses_apply_without_approval(tmp_path: Path) -> None:
    old_tool = make_fake_tool_root(tmp_path, "old_tool", "old")
    write_install_manifest(old_tool, build_install_manifest(old_tool, source="old.zip"))
    project = tmp_path / "project"
    (project / ".gtestcov").mkdir(parents=True)
    (project / "project_profile.yaml").write_text("project_name: cli_upgrade\n", encoding="utf-8")
    tool_home = tmp_path / "tool_home"

    inspect_completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "upgrade",
            "inspect",
            "--tool-root",
            str(old_tool),
            "--project-root",
            str(project),
            "--target-ref",
            "v0.2.1",
            "--install-mode",
            "zip",
            "--upgrade-id",
            "cli-up",
            "--tool-home",
            str(tool_home),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    apply_completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtestcov.cli",
            "upgrade",
            "apply",
            "--upgrade-id",
            "cli-up",
            "--project-root",
            str(project),
            "--tool-home",
            str(tool_home),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert inspect_completed.returncode == 0
    assert json.loads(inspect_completed.stdout)["status"] == "inspected"
    assert apply_completed.returncode == 0
    assert json.loads(apply_completed.stdout)["status"] == "refused"
