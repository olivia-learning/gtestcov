from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


if not hasattr(BaseModel, "model_validate"):
    def _model_validate(cls, value):
        return cls.parse_obj(value)

    def _model_dump(self, mode: str = "python", **kwargs):
        kwargs.pop("mode", None)
        return self.dict(**kwargs)

    def _model_dump_json(self, **kwargs):
        return self.json(**kwargs)

    def _model_copy(self, *, update=None, deep: bool = False):
        return self.copy(update=update, deep=deep)

    BaseModel.model_validate = classmethod(_model_validate)  # type: ignore[attr-defined]
    BaseModel.model_dump = _model_dump  # type: ignore[attr-defined]
    BaseModel.model_dump_json = _model_dump_json  # type: ignore[attr-defined]
    BaseModel.model_copy = _model_copy  # type: ignore[attr-defined]


class StyleConfig(BaseModel):
    preferred_macros: list[str] = Field(default_factory=lambda: ["TEST", "TEST_F", "TEST_P"])
    forbidden_macros: list[str] = Field(default_factory=list)
    fixture_naming: str = "{Target}Test"
    test_file_suffix: str = "_test.cpp"


class BuildConfig(BaseModel):
    system: str = "unknown"
    build_file: str = ""
    candidate_build_files: list[str] = Field(default_factory=list)
    build_command: str = ""
    incremental_build_command: str = ""
    test_command: str = ""
    filtered_test_command: str = ""
    coverage_command: str = ""
    target_coverage_command: str = ""
    coverage_xml: str = "coverage.xml"
    test_target_pattern: str = "{module}_test"


class DependencyConfig(BaseModel):
    manifest: str = ""
    manifest_candidates: list[str] = Field(default_factory=list)
    dependency_root: list[str] = Field(default_factory=list)
    use_real_dependency_headers: bool = True
    host_shim_dir: str = ""
    exclude_from_coverage: list[str] = Field(default_factory=list)


class TestSupportConfig(BaseModel):
    test_dirs: list[str] = Field(default_factory=list)
    fake_dir: str = ""
    harness_dir: str = ""
    guard_dir: str = ""
    builder_dir: str = ""
    dependency_shim_dir: str = ""
    test_build_config_paths: list[str] = Field(default_factory=list)


class CoverageThresholds(BaseModel):
    changed_line: float = 70.0
    protocol_line: float = 80.0
    max_iterations: int = 3
    max_stagnant_rounds: int = 3
    min_iteration_improvement: float = 5.0
    bootstrap_threshold: float = 15.0
    characterization_threshold: float = 40.0
    branch_expansion_threshold: float = 70.0


class CodraxDirectModeConfig(BaseModel):
    enabled: bool = False
    require_audit_log: bool = True


class CodraxEvidenceConfig(BaseModel):
    enabled: bool = False
    command: str = "codrax"
    invocation: str = "auto"
    args_template: list[str] = Field(default_factory=list)
    model_policy: str = "self_hosted_ok"
    max_context: str = "targeted"
    require_file_line: bool = True
    timeout_seconds: int = 180
    probe_timeout_seconds: int = 20
    max_output_chars: int = 12000
    direct_mode: CodraxDirectModeConfig = Field(default_factory=CodraxDirectModeConfig)


class EvidenceConfig(BaseModel):
    codrax: CodraxEvidenceConfig = Field(default_factory=CodraxEvidenceConfig)


class TargetsConfig(BaseModel):
    default_line_coverage: float = 70.0


class EmbeddedPolicy(BaseModel):
    hardware_access: str = "require_hal_fake_or_hil"
    rtos_access: str = "fake_osal_for_host_gtest"
    time_access: str = "use_fake_clock"
    async_access: str = "use_fake_executor_or_join_in_teardown"
    memory_api: dict[str, str] = Field(default_factory=dict)


class ProjectProfile(BaseModel):
    project_name: str = "embedded_cpp_project"
    language: str = "c++"
    test_framework: str = "gtest"
    mock_framework: str = "gmock"
    style: StyleConfig = Field(default_factory=StyleConfig)
    build: BuildConfig = Field(default_factory=BuildConfig)
    dependency: DependencyConfig = Field(default_factory=DependencyConfig)
    test_support: TestSupportConfig = Field(default_factory=TestSupportConfig)
    embedded_policy: EmbeddedPolicy = Field(default_factory=EmbeddedPolicy)
    coverage: CoverageThresholds = Field(default_factory=CoverageThresholds)
    targets: TargetsConfig = Field(default_factory=TargetsConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    common_test_types: list[str] = Field(
        default_factory=lambda: [
            "Unit Test",
            "Component Test",
            "Message Interface Test",
            "Message Conformance Test",
            "Lifecycle Test",
            "Resource Limit Test",
            "Fault Injection Test",
            "Regression Test",
            "Characterization Test",
            "Struct Layout / ABI Test",
            "Power-cycle / Recovery Test",
        ]
    )


class DiscoveryReport(BaseModel):
    project_root: str
    test_macros: dict[str, int] = Field(default_factory=dict)
    test_files: list[str] = Field(default_factory=list)
    gtest_includes: list[str] = Field(default_factory=list)
    gmock_includes: list[str] = Field(default_factory=list)
    build_files: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    support_dirs: dict[str, list[str]] = Field(default_factory=dict)
    inferred_build_system: str = "unknown"
    conflicts: list[str] = Field(default_factory=list)


class DependencyEntry(BaseModel):
    name: str = "unknown"
    version: str = ""
    local_path: str = ""
    include_paths: list[str] = Field(default_factory=list)
    source_paths: list[str] = Field(default_factory=list)
    library_paths: list[str] = Field(default_factory=list)
    host_build: str = "unknown"
    test_treatment: str = "inspect"


class DependencyReport(BaseModel):
    manifest_path: str = ""
    dependencies: list[DependencyEntry] = Field(default_factory=list)


class SymbolReport(BaseModel):
    symbol: str
    kind: str = "unknown"
    locations: list[str] = Field(default_factory=list)
    recommendation: str = "inspect manually"


class TargetFeatures(BaseModel):
    target: str
    resolved_path: str = ""
    pure_logic_signals: list[str] = Field(default_factory=list)
    complex_signals: list[str] = Field(default_factory=list)
    message_signals: list[str] = Field(default_factory=list)
    conformance_signals: list[str] = Field(default_factory=list)
    lifecycle_signals: list[str] = Field(default_factory=list)
    fault_signals: list[str] = Field(default_factory=list)
    hardware_signals: list[str] = Field(default_factory=list)
    dependency_symbols: list[str] = Field(default_factory=list)


class CodraxEvidence(BaseModel):
    enabled: bool = False
    available: bool = False
    status: str = "disabled"
    command: str = "codrax"
    invocation: str = ""
    request: str = ""
    returncode: int | None = None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    related_files: list[str] = Field(default_factory=list)
    file_line_refs: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    harnesses: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class UnderstandingFinding(BaseModel):
    kind: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    source: str = "codrax"
    confidence: str = "cited"
    notes: list[str] = Field(default_factory=list)


class ProjectUnderstanding(BaseModel):
    enabled: bool = False
    status: str = "disabled"
    source: str = "codrax"
    target: str = ""
    question_ids: list[str] = Field(default_factory=list)
    findings: list[UnderstandingFinding] = Field(default_factory=list)
    codrax_evidence: CodraxEvidence = Field(default_factory=CodraxEvidence)
    notes: list[str] = Field(default_factory=list)


class TestObligation(BaseModel):
    obligation_id: str
    title: str
    kind: str
    status: str = "ready"
    evidence: list[str] = Field(default_factory=list)
    required_support: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    run_id: str
    target: str
    project_style: DiscoveryReport
    dependency_resolution: DependencyReport
    observed_features: TargetFeatures
    observed_symbols: list[SymbolReport]
    selected_test_type: str
    reason: list[str]
    required_support: list[str]
    safety_risks: list[str]
    planned_files: list[str]
    codrax_evidence: CodraxEvidence = Field(default_factory=CodraxEvidence)
    project_understanding: ProjectUnderstanding = Field(default_factory=ProjectUnderstanding)
    test_obligations: list[TestObligation] = Field(default_factory=list)
    decision_report_path: str = ""


def model_to_plain(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
