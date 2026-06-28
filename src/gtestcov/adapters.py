from __future__ import annotations

from dataclasses import dataclass, field

from .models import ProjectUnderstanding, TestObligation
from .understanding import finding_refs, finding_text


@dataclass
class AdapterGuidance:
    adapter_name: str = ""
    selected_test_type: str = ""
    reason: list[str] = field(default_factory=list)
    required_support: list[str] = field(default_factory=list)
    safety_risks: list[str] = field(default_factory=list)
    planned_files: list[str] = field(default_factory=list)
    obligations: list[TestObligation] = field(default_factory=list)


def apply_project_adapters(understanding: ProjectUnderstanding, base_selected_test_type: str) -> AdapterGuidance:
    if understanding.status != "ok":
        return AdapterGuidance()

    guidance = _apply_fprime_adapter(understanding, base_selected_test_type)
    if guidance.adapter_name:
        return guidance

    guidance = _apply_px4_adapter(understanding, base_selected_test_type)
    if guidance.adapter_name:
        return guidance

    return AdapterGuidance()


def _apply_fprime_adapter(understanding: ProjectUnderstanding, base_selected_test_type: str) -> AdapterGuidance:
    text = finding_text(understanding).lower()
    if not _has_any(text, ["f prime", "fprime", "gtestbase", "fw::", "drv::"]):
        return AdapterGuidance()

    refs = finding_refs(understanding, ["f prime", "fprime", "gtestbase", "tester", "test/ut", "fw::", "drv::"])
    planned = _paths_from_refs(refs, ["test/ut", "test", "tester", "gtestbase"])
    message_like = _has_any(text, ["port", "message", "send", "receive", "telemetry", "event"])
    selected = (
        "Message Interface Test / Component Test (CODRAX-cited F Prime Harness)"
        if message_like or "Message Interface" in base_selected_test_type
        else "Component Test (CODRAX-cited F Prime Harness)"
    )
    return AdapterGuidance(
        adapter_name="fprime",
        selected_test_type=selected,
        reason=[
            "CODRAX evidence identifies F Prime framework or generated harness concepts; reuse cited harness boundaries first.",
        ],
        required_support=[
            "CODRAX-cited F Prime Tester/GTestBase or component harness",
            "component port invocation helpers and history assertions when cited",
        ],
        safety_risks=[
            "Do not bypass a CODRAX-cited F Prime component harness with a standalone plain gtest.",
        ],
        planned_files=planned,
        obligations=[
            TestObligation(
                obligation_id="fprime_reuse_cited_component_harness",
                title="Reuse CODRAX-cited F Prime component harness",
                kind="fprime_harness",
                evidence=refs[:12],
                required_support=["cited Tester/GTestBase or component harness"],
                risk_tags=["project_adapter", "fprime", "harness"],
                assertions=[
                    "Use cited component harness APIs before adding new test-only access.",
                    "Assert observable port, event, telemetry, or history effects when CODRAX cites them.",
                ],
            )
        ],
    )


def _apply_px4_adapter(understanding: ProjectUnderstanding, base_selected_test_type: str) -> AdapterGuidance:
    text = finding_text(understanding).lower()
    if not _has_any(text, ["px4", "uorb", "orb_id", "moduleparams", "functional gtest", "param_"]):
        return AdapterGuidance()

    refs = finding_refs(understanding, ["px4", "uorb", "orb_id", "moduleparams", "functional gtest", "param_", "src/drivers"])
    driver_like = _has_any(text, ["src/drivers", "driver", "board", "device io", "register"])
    if driver_like:
        return AdapterGuidance(
            adapter_name="px4",
            selected_test_type="SIL / HIL-required (CODRAX-cited PX4 Driver Boundary)",
            reason=[
                "CODRAX evidence identifies a PX4 driver or board boundary; require SITL/HIL or a cited fake boundary.",
            ],
            required_support=["CODRAX-cited PX4 SITL/HIL setup or explicit driver fake"],
            safety_risks=["Do not generate host gtest that opens real PX4 device or board IO paths."],
            obligations=[
                TestObligation(
                    obligation_id="px4_cited_driver_boundary_requires_sitl_or_hil",
                    title="PX4 driver boundary requires cited SITL/HIL or fake",
                    kind="px4_driver_boundary",
                    status="hardware_only",
                    evidence=refs[:12],
                    required_support=["cited SITL/HIL environment or explicit driver fake"],
                    risk_tags=["project_adapter", "px4", "driver_boundary"],
                    assertions=["Write manual_review_needed.md unless CODRAX cites a safe fake or SITL/HIL path."],
                )
            ],
        )

    return AdapterGuidance(
        adapter_name="px4",
        selected_test_type="Message Interface Test / Component Test (CODRAX-cited PX4 Functional GTest)",
        reason=[
            "CODRAX evidence identifies PX4 module, parameter, or uORB concepts; follow cited functional gtest patterns.",
        ],
        required_support=["CODRAX-cited PX4 functional gtest pattern", "cited uORB or parameter setup/teardown"],
        safety_risks=["Do not invent uORB topics, PX4 parameters, or module lifecycle helpers without CODRAX file:line evidence."],
        obligations=[
            TestObligation(
                obligation_id="px4_cited_functional_gtest_boundary",
                title="PX4 behavior through CODRAX-cited functional gtest boundary",
                kind="px4_functional_gtest",
                evidence=refs[:12],
                required_support=["cited functional gtest pattern", "cited uORB setup/teardown", "parameter reset fixture if cited"],
                risk_tags=["project_adapter", "px4", "uorb", "module_lifecycle"],
                assertions=[
                    "Use CODRAX-cited functional gtest or fixture patterns.",
                    "Assert observable uORB publication/subscription or module effects only where evidence cites them.",
                ],
            )
        ],
    )


def _paths_from_refs(refs: list[str], tokens: list[str]) -> list[str]:
    lowered = [token.lower() for token in tokens]
    paths: list[str] = []
    for ref in refs:
        path = ref.rsplit(":", 1)[0]
        if any(token in path.lower() for token in lowered) and path not in paths:
            paths.append(path)
    return paths


def _has_any(text: str, tokens: list[str]) -> bool:
    return any(token in text for token in tokens)
