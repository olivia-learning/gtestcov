from __future__ import annotations

import json
import re
from pathlib import Path

from .fs import read_text
from .models import CodraxEvidence, ProjectProfile, TargetFeatures, TestObligation, relpath


LIFECYCLE_RE = re.compile(r"\b(Init|Start|Stop|Shutdown|DeInit|Reset|Suspend|Resume)\b")
MESSAGE_RE = re.compile(
    r"\b(send|receive|rx|tx|CAN|LIN|UART|SPI|Modbus|frame|payload|queue|command|response|event|port)\b"
    r"|\b[A-Za-z0-9_]*(send|receive)[A-Za-z0-9_]*\b",
    re.I,
)
CONFORMANCE_RE = re.compile(r"\b(encode|decode|pack|unpack|endian|bitfield|CRC|checksum|scale|offset)", re.I)
FAULT_RE = re.compile(r"\b(error|fail|timeout|queue full|invalid|crc error|unavailable|overflow)\b", re.I)
HARDWARE_RE = re.compile(r"\b(register|MMIO|GPIO|volatile|IRQ|ISR)\b")


def build_test_obligations(
    project_root: Path,
    target: str,
    selected_test_type: str,
    features: TargetFeatures,
    evidence: CodraxEvidence,
    profile: ProjectProfile,
) -> list[TestObligation]:
    target_path = _resolve_target_path(project_root, target, features)
    fallback_refs = _fallback_refs(project_root, target_path, features)
    codrax_refs = evidence.file_line_refs[:12] if evidence.status == "ok" else []
    obligations: list[TestObligation] = []

    if "SIL / HIL" in selected_test_type or features.hardware_signals:
        obligations.append(
            TestObligation(
                obligation_id="hardware_boundary_requires_hal_fake_or_hil",
                title="Direct hardware boundary requires HAL fake or SIL/HIL plan",
                kind="hardware_boundary",
                status="hardware_only",
                evidence=_dedupe([*_line_refs(project_root, target_path, HARDWARE_RE), *codrax_refs, *fallback_refs])[:12],
                required_support=["HAL fake or explicit SIL/HIL environment"],
                risk_tags=["hardware", "host_gtest_boundary"],
                assertions=["Do not generate a plain host gtest that touches board registers or real device IO."],
                notes=["Write manual_review_needed.md unless a cited HAL fake or HIL harness is available."],
            )
        )

    if "Message Conformance" in selected_test_type:
        obligations.append(
            TestObligation(
                obligation_id="message_layout_and_crc_conformance",
                title="Message layout, length, payload, endian, and CRC/checksum behavior",
                kind="message_conformance",
                evidence=_dedupe([*_line_refs(project_root, target_path, CONFORMANCE_RE), *codrax_refs, *fallback_refs])[:12],
                required_support=["real protocol constants", "table-driven fixtures"],
                risk_tags=["protocol", "abi_layout"],
                assertions=["Assert ID, DLC/length, payload bytes, endian/bitfield mapping, and CRC/checksum."],
            )
        )

    if "Lifecycle" in selected_test_type or features.lifecycle_signals:
        obligations.append(
            TestObligation(
                obligation_id="lifecycle_init_shutdown_smoke",
                title="Init success/failure and Shutdown or teardown behavior",
                kind="lifecycle",
                evidence=_dedupe([*_line_refs(project_root, target_path, LIFECYCLE_RE), *codrax_refs, *fallback_refs])[:12],
                required_support=["fixture", "fake external boundaries"],
                risk_tags=["init_shutdown", "state"],
                assertions=["Call Init with ASSERT before behavior checks.", "Call Shutdown/Stop/DeInit in teardown when available."],
            )
        )

    if "Message Interface" in selected_test_type or features.message_signals:
        obligations.append(
            TestObligation(
                obligation_id="message_boundary_observable_effects",
                title="Message input/output boundary and observable side effects",
                kind="message_interface",
                evidence=_dedupe([*_line_refs(project_root, target_path, MESSAGE_RE), *codrax_refs, *fallback_refs])[:12],
                required_support=["fake bus / fake peer", "fixture"],
                risk_tags=["message_boundary"],
                assertions=["Assert sent/received messages and observable effects through fakes or harness histories."],
            )
        )

    if "Fault" in selected_test_type or features.fault_signals:
        obligations.append(
            TestObligation(
                obligation_id="failure_path_fault_injection",
                title="Failure path or invalid input behavior",
                kind="fault_injection",
                evidence=_dedupe([*_line_refs(project_root, target_path, FAULT_RE), *codrax_refs, *fallback_refs])[:12],
                required_support=["fake downstream dependency", "fault injection data"],
                risk_tags=["fault_path"],
                assertions=["Inject downstream failure or invalid data and assert safe false/error behavior without a crash."],
            )
        )

    if "Unit" in selected_test_type:
        obligations.append(
            TestObligation(
                obligation_id="table_driven_pure_logic_behavior",
                title="Pure logic behavior over normal and boundary inputs",
                kind="unit_table",
                evidence=_dedupe([*_function_refs(project_root, target_path), *fallback_refs])[:8],
                required_support=["table-driven test data"],
                risk_tags=["pure_logic"],
                assertions=["Cover nominal, boundary, zero/invalid, and representative edge inputs."],
            )
        )

    if not obligations and "Component" in selected_test_type:
        obligations.append(
            TestObligation(
                obligation_id="component_behavior_with_cited_boundaries",
                title="Component behavior through public boundary and cited collaborators",
                kind="component",
                evidence=_dedupe([*_dependency_refs(project_root, target_path, features), *codrax_refs, *fallback_refs])[:12],
                required_support=["fixture", "fake external boundaries"],
                risk_tags=["component_boundary"],
                assertions=["Assert observable behavior through public APIs and cited fake boundaries."],
            )
        )

    if evidence.status == "ok" and evidence.harnesses:
        obligations.append(
            TestObligation(
                obligation_id="reuse_existing_harness_or_fixture",
                title="Reuse existing CODRAX-cited test harness or fixture",
                kind="harness_reuse",
                evidence=_refs_matching(evidence.file_line_refs, ["test", "tester", "harness", "fixture"])[:12],
                required_support=["reuse cited harness before creating a new one"],
                risk_tags=["harness"],
                assertions=["Prefer extending or following the cited harness pattern over inventing a parallel harness."],
            )
        )

    if evidence.status == "ok" and evidence.dependencies:
        obligations.append(
            TestObligation(
                obligation_id="honor_external_dependency_contracts",
                title="Honor CODRAX-cited dependency/API contracts",
                kind="dependency_contract",
                evidence=_dedupe([*_refs_matching(evidence.file_line_refs, ["external", "include", "deps", "third_party"]), *codrax_refs])[:12],
                required_support=["real dependency declarations or exact host shims"],
                risk_tags=["dependency_contract"],
                assertions=["Use real declarations or exact shims; do not copy structs, enums, or macros into tests."],
            )
        )

    if evidence.enabled and evidence.status not in {"ok", "disabled"}:
        obligations.append(
            TestObligation(
                obligation_id="codrax_evidence_needs_manual_review",
                title="CODRAX evidence was requested but is not usable",
                kind="manual_review",
                status="manual_review_needed",
                evidence=[],
                required_support=["manual source review or working CODRAX evidence"],
                risk_tags=["insufficient_evidence"],
                assertions=["Do not invent missing dependencies, lifecycle rules, or harnesses."],
                notes=[f"CODRAX status: {evidence.status}"],
            )
        )

    return _dedupe_obligations(obligations)


def render_test_obligations(obligations: list[TestObligation]) -> str:
    if not obligations:
        return "- No test obligations generated.\n"
    lines: list[str] = []
    for index, obligation in enumerate(obligations, start=1):
        lines.extend(
            [
                f"### {index}. {obligation.title}",
                f"- ID: `{obligation.obligation_id}`",
                f"- Kind: `{obligation.kind}`",
                f"- Status: `{obligation.status}`",
                f"- Evidence: {obligation.evidence or ['none']}",
                f"- Required support: {obligation.required_support or ['none']}",
                f"- Risk tags: {obligation.risk_tags or ['none']}",
                f"- Assertions: {obligation.assertions or ['observable behavior']}",
            ]
        )
        if obligation.notes:
            lines.append(f"- Notes: {obligation.notes}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_test_obligations(run_dir: Path, obligations: list[TestObligation]) -> Path:
    json_path = run_dir / "test_obligations.json"
    md_path = run_dir / "test_obligations.md"
    json_path.write_text(
        json.dumps([obligation.model_dump(mode="json") for obligation in obligations], indent=2),
        encoding="utf-8",
    )
    md_path.write_text("# Test Obligation Matrix\n\n" + render_test_obligations(obligations), encoding="utf-8")
    return json_path


def _resolve_target_path(project_root: Path, target: str, features: TargetFeatures) -> Path | None:
    candidates = [features.resolved_path, target]
    for candidate in candidates:
        if not candidate:
            continue
        path = (project_root / candidate).resolve()
        if path.exists() and path.is_file():
            return path
    return None


def _fallback_refs(project_root: Path, target_path: Path | None, features: TargetFeatures) -> list[str]:
    if target_path:
        return [f"{relpath(target_path, project_root)}:1"]
    if features.resolved_path:
        return [features.resolved_path]
    return []


def _line_refs(project_root: Path, target_path: Path | None, pattern: re.Pattern[str], limit: int = 8) -> list[str]:
    if not target_path:
        return []
    refs: list[str] = []
    for line_no, line in enumerate(read_text(target_path).splitlines(), start=1):
        if pattern.search(line):
            refs.append(f"{relpath(target_path, project_root)}:{line_no}")
        if len(refs) >= limit:
            break
    return refs


def _dependency_refs(project_root: Path, target_path: Path | None, features: TargetFeatures) -> list[str]:
    if not target_path:
        return []
    refs: list[str] = []
    text = read_text(target_path)
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#include") or any(symbol in line for symbol in features.dependency_symbols):
            refs.append(f"{relpath(target_path, project_root)}:{line_no}")
    return refs[:10]


def _function_refs(project_root: Path, target_path: Path | None) -> list[str]:
    if not target_path:
        return []
    refs: list[str] = []
    for line_no, line in enumerate(read_text(target_path).splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#include") or stripped.startswith("//"):
            continue
        if "(" in stripped and ")" in stripped and ("{" in stripped or stripped.endswith(")")):
            refs.append(f"{relpath(target_path, project_root)}:{line_no}")
            break
    return refs


def _refs_matching(refs: list[str], tokens: list[str]) -> list[str]:
    lowered = [token.lower() for token in tokens]
    return [ref for ref in refs if any(token in ref.lower() for token in lowered)]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _dedupe_obligations(obligations: list[TestObligation]) -> list[TestObligation]:
    seen: set[str] = set()
    result: list[TestObligation] = []
    for obligation in obligations:
        if obligation.obligation_id in seen:
            continue
        seen.add(obligation.obligation_id)
        result.append(obligation)
    return result
