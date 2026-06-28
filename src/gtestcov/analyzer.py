from __future__ import annotations

import json
import re
from pathlib import Path

from .adapters import apply_project_adapters
from .codrax import render_codrax_evidence, write_codrax_evidence
from .dependency import classify_default_symbols, parse_dependency_xml
from .discovery import discover_project
from .fs import CPP_SUFFIXES, ensure_run_dir, iter_files, read_text
from .models import AnalysisReport, TargetFeatures, TestObligation, relpath
from .obligations import build_test_obligations, render_test_obligations, write_test_obligations
from .profile import ProjectProfile, load_profile
from .understanding import collect_project_understanding, render_project_understanding, write_project_understanding


COMPLEX_RE = re.compile(
    r"\b(class|struct)\b"
    r"|::|std::unique_ptr|std::shared_ptr|Singleton|GetInstance|global|g_"
)
MESSAGE_RE = re.compile(
    r"\b(send|receive|rx|tx|CAN|LIN|UART|SPI|Modbus|frame|payload|queue|command|response|event|port)\b"
    r"|\b[A-Za-z0-9_]*(send|receive|publish|subscribe)[A-Za-z0-9_]*\b",
    re.I,
)
CONFORMANCE_RE = re.compile(r"\b(encode|decode|pack|unpack|endian|bitfield|CRC|checksum|scale|offset)", re.I)
LIFECYCLE_RE = re.compile(r"\b(Init|Start|Stop|Shutdown|DeInit|Reset|Suspend|Resume|Run)\b")
FAULT_RE = re.compile(r"\b(error|fail|timeout|queue full|invalid|crc error|unavailable|overflow)\b", re.I)
HARDWARE_RE = re.compile(r"\b(register|MMIO|GPIO|volatile|IRQ|ISR)\b")
IO_RE = re.compile(r"\b(file|socket|network|storage|NVM|Flash|clock|timer|thread|task|RTOS|OSAL)\b", re.I)
DEPENDENCY_SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9]+_[A-Za-z0-9_]+\b")
COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)


def analyze_target(project_root: Path, target: str, run_id: str | None = None) -> AnalysisReport:
    root = project_root.resolve()
    profile = load_profile(root)
    run_id, run_dir = ensure_run_dir(root, run_id)

    discovery = discover_project(root)
    dependency = parse_dependency_xml(root, profile)
    features = inspect_target(root, target)
    project_understanding = collect_project_understanding(root, target, profile)
    codrax_evidence = project_understanding.codrax_evidence
    features = _merge_codrax_features(features, codrax_evidence)
    symbols = classify_default_symbols(root, profile, features.dependency_symbols)
    selected, reason, support, risks = classify_test_type(features, profile)
    reason, support, risks = _merge_codrax_guidance(codrax_evidence, reason, support, risks)
    adapter_guidance = apply_project_adapters(project_understanding, selected)
    if adapter_guidance.adapter_name:
        selected = adapter_guidance.selected_test_type or selected
        reason, support, risks = _merge_adapter_guidance(adapter_guidance, reason, support, risks)
    planned_files = _planned_files(root, target, selected, profile)
    if adapter_guidance.planned_files:
        planned_files = _dedupe([*adapter_guidance.planned_files, *planned_files])
    test_obligations = build_test_obligations(root, target, selected, features, codrax_evidence, profile)
    test_obligations = _dedupe_obligations([*test_obligations, *adapter_guidance.obligations])

    report = AnalysisReport(
        run_id=run_id,
        target=target,
        project_style=discovery,
        dependency_resolution=dependency,
        observed_features=features,
        observed_symbols=symbols,
        selected_test_type=selected,
        reason=reason,
        required_support=support,
        safety_risks=risks,
        planned_files=planned_files,
        codrax_evidence=codrax_evidence,
        project_understanding=project_understanding,
        test_obligations=test_obligations,
    )
    decision_path = run_dir / "decision_report.md"
    report.decision_report_path = relpath(decision_path, root)
    decision_text = render_decision_report(report, profile)
    decision_path.write_text(decision_text, encoding="utf-8")
    (run_dir / "analysis.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if codrax_evidence.enabled:
        write_codrax_evidence(run_dir, codrax_evidence)
        write_project_understanding(run_dir, project_understanding)
    write_test_obligations(run_dir, test_obligations)
    return report


def inspect_target(project_root: Path, target: str) -> TargetFeatures:
    path = _resolve_target_path(project_root, target)
    text = read_text(path) if path else _collect_symbol_context(project_root, target)
    signal_text = _strip_comments(text)
    target_label = relpath(path, project_root) if path else target
    dependency_symbols = sorted(set(DEPENDENCY_SYMBOL_RE.findall(signal_text)))
    return TargetFeatures(
        target=target,
        resolved_path=target_label if path else "",
        pure_logic_signals=_signals_absent(signal_text),
        complex_signals=_find_signals(COMPLEX_RE, signal_text),
        message_signals=_find_signals(MESSAGE_RE, signal_text),
        conformance_signals=_find_signals(CONFORMANCE_RE, signal_text),
        lifecycle_signals=_find_signals(LIFECYCLE_RE, signal_text),
        fault_signals=_find_signals(FAULT_RE, signal_text),
        hardware_signals=_find_signals(HARDWARE_RE, signal_text),
        dependency_symbols=dependency_symbols[:25],
    )


def _resolve_target_path(project_root: Path, target: str) -> Path | None:
    candidate = (project_root / target).resolve()
    if candidate.exists() and candidate.is_file():
        return candidate
    for path in iter_files(project_root, CPP_SUFFIXES):
        if path.name == target or relpath(path, project_root) == target:
            return path
    return None


def _collect_symbol_context(project_root: Path, symbol: str) -> str:
    chunks: list[str] = []
    for path in iter_files(project_root, CPP_SUFFIXES):
        text = read_text(path)
        if symbol not in text:
            continue
        chunks.append(f"// {relpath(path, project_root)}\n{text[:8000]}")
    return "\n".join(chunks)


def _find_signals(pattern: re.Pattern[str], text: str) -> list[str]:
    return sorted(set(match.group(0) for match in pattern.finditer(text)))[:20]


def _strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def _signals_absent(text: str) -> list[str]:
    if not text:
        return []
    absent = []
    if not IO_RE.search(text):
        absent.append("no obvious IO/time/thread/storage signal")
    if not HARDWARE_RE.search(text):
        absent.append("no obvious direct hardware signal")
    if not LIFECYCLE_RE.search(text):
        absent.append("no obvious lifecycle method")
    return absent


def classify_test_type(features: TargetFeatures, profile: ProjectProfile) -> tuple[str, list[str], list[str], list[str]]:
    reason: list[str] = []
    support: list[str] = []
    risks: list[str] = []

    if features.hardware_signals:
        return (
            "SIL / HIL-required",
            ["Target appears to touch hardware/register/HAL-level APIs directly."],
            ["HAL fake or explicit test seam"],
            ["Direct board IO cannot be validated by plain host gtest."],
        )

    label = f"{features.target} {features.resolved_path}".lower()
    if features.conformance_signals and any(token in label for token in ["codec", "protocol", "pack", "unpack"]):
        reason.append("Target contains encode/decode/pack/unpack/protocol-layout signals.")
        support.extend(["real protocol constants", "table-driven fixtures"])
        return "Message Conformance Test", reason, support, risks

    if features.lifecycle_signals:
        reason.append("Target has lifecycle methods; smoke/lifecycle coverage should come first.")
        support.extend(["TestHarness", "fixture", "fake external boundaries"])
        if features.message_signals:
            support.append("fake bus / fake peer")
            reason.append("Target also has message signals; add message-interface assertions after lifecycle smoke.")
        risks.append("Init success must use ASSERT and shutdown must run in teardown.")
        return "Lifecycle Test / Component Test", reason, support, risks

    if features.conformance_signals:
        reason.append("Target contains encode/decode/pack/unpack/protocol-layout signals.")
        support.extend(["real protocol constants", "table-driven fixtures"])
        return "Message Conformance Test", reason, support, risks

    if features.message_signals:
        reason.append("Target appears to send/receive inter-module messages or bus frames.")
        support.extend(["fake bus / fake peer", "fixture", "observable message assertions"])
        return "Message Interface Test", reason, support, risks

    if features.fault_signals:
        reason.append("Target contains failure/error-path signals.")
        support.extend(["fake downstream dependency", "fault injection data"])
        return "Fault Injection Test", reason, support, risks

    if features.complex_signals or features.dependency_symbols:
        reason.append("Target is not pure logic or has collaborator/dependency symbols.")
        support.extend(["TestHarness", "fixture", "fake external boundaries"])
        if features.dependency_symbols:
            risks.append("Dependency symbols must use real declarations or exact shims.")
        return "Component Test", reason, support, risks

    if len(features.pure_logic_signals) >= 3:
        reason.append("Target has no obvious IO/time/hardware/lifecycle signals.")
        support.extend(["table-driven test data"])
        return "Unit Test / Table-Driven Test", reason, support, risks

    reason.append("Defaulting to Component Test for stability.")
    support.extend(["fixture", "minimal harness"])
    return "Component Test", reason, support, risks


def _planned_files(project_root: Path, target: str, selected: str, profile: ProjectProfile) -> list[str]:
    base = Path(target).stem or "target"
    suffix = profile.style.test_file_suffix
    if not profile.test_support.test_dirs:
        return []
    test_dir = profile.test_support.test_dirs[0]
    files = [f"{test_dir}/{base}{suffix}"]
    if profile.test_support.harness_dir and (
        "Component" in selected or "Lifecycle" in selected or "Message Interface" in selected
    ):
        files.append(f"{profile.test_support.harness_dir}/{base}_harness.hpp")
    if profile.test_support.fake_dir and "Message" in selected:
        files.append(f"{profile.test_support.fake_dir}/{base}_fake_bus.hpp")
    return files


def render_decision_report(report: AnalysisReport, profile: ProjectProfile) -> str:
    symbols = "\n".join(
        f"- {symbol.symbol}: {symbol.kind}; {symbol.recommendation}; locations={symbol.locations or ['not found']}"
        for symbol in report.observed_symbols
    )
    deps = "\n".join(
        f"- {dep.name}: path={dep.local_path or 'unknown'}, version={dep.version or 'unknown'}, treatment={dep.test_treatment}"
        for dep in report.dependency_resolution.dependencies
    ) or "- No configured dependency manifest entries found."
    features = report.observed_features
    return f"""# gtestcov Decision Report

Run ID: `{report.run_id}`

## Target
- Target: `{report.target}`
- Resolved path: `{features.resolved_path or 'symbol or unresolved path'}`

## Project Style
- Test macros: {json.dumps(report.project_style.test_macros, sort_keys=True)}
- Test directories/files: {report.project_style.test_files or ['not found']}
- Build system: {report.project_style.inferred_build_system}
- Build files: {report.project_style.build_files or ['not found']}
- Similar tests: {report.project_style.gtest_includes or ['not found']}
- Conflicts: {report.project_style.conflicts or ['none']}

## Dependency Resolution
- Manifest path: `{report.dependency_resolution.manifest_path or profile.dependency.manifest + ' not found'}`
{deps}

## Observed Dependencies
- Complex signals: {features.complex_signals or ['none']}
- Message signals: {features.message_signals or ['none']}
- Conformance signals: {features.conformance_signals or ['none']}
- Lifecycle signals: {features.lifecycle_signals or ['none']}
- Fault signals: {features.fault_signals or ['none']}
- Hardware signals: {features.hardware_signals or ['none']}
- Dependency symbols: {features.dependency_symbols or ['none']}

## Observed Dependency Symbols
{symbols}

## CODRAX Evidence
{render_codrax_evidence(report.codrax_evidence).rstrip()}

## CODRAX Project Understanding
{render_project_understanding(report.project_understanding).rstrip()}

## Test Obligation Matrix
{render_test_obligations(report.test_obligations).rstrip()}

## Selected Test Type
- {report.selected_test_type}

## Reason
{_bullets(report.reason)}

## Required Support
{_bullets(report.required_support)}

## Safety Risks
{_bullets(report.safety_risks or ['No special risks detected by heuristics.'])}

## Planned Files
{_bullets(report.planned_files)}
"""


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _merge_codrax_features(features: TargetFeatures, evidence) -> TargetFeatures:
    if evidence.status != "ok":
        return features

    merged = features.model_copy(deep=True)
    evidence_text = " ".join(
        [
            *evidence.symbols,
            *evidence.dependencies,
            *evidence.harnesses,
            *evidence.risks,
            *evidence.notes,
        ]
    )
    for symbol in evidence.symbols:
        if re.match(r"^[A-Z][A-Z0-9]+_[A-Za-z0-9_]+$", symbol):
            _append_unique(merged.dependency_symbols, symbol)
    if re.search(r"\b(Init|Start|Stop|Shutdown|DeInit|Reset)\b", evidence_text):
        _append_unique(merged.lifecycle_signals, "CODRAX:lifecycle boundary")
    if re.search(r"\b(message|queue|port|CAN|LIN|UART|SPI|Modbus|frame|payload|protocol)\b", evidence_text, re.I):
        _append_unique(merged.message_signals, "CODRAX:message boundary")
    if re.search(r"\b(register|MMIO|volatile|GPIO|IRQ|ISR)\b", evidence_text, re.I):
        _append_unique(merged.hardware_signals, "CODRAX:direct hardware boundary")
    return merged


def _merge_codrax_guidance(evidence, reason: list[str], support: list[str], risks: list[str]):
    reason = list(reason)
    support = list(support)
    risks = list(risks)

    if evidence.status == "ok":
        if evidence.file_line_refs:
            reason.append("CODRAX supplied file:line evidence; use the CODRAX Evidence section before adding assumptions.")
        if evidence.harnesses:
            support.append("reuse CODRAX-cited existing tests, fixtures, or harnesses")
        if evidence.dependencies:
            support.append("honor CODRAX-cited dependency/API boundaries")
        for risk in evidence.risks[:5]:
            risks.append(f"CODRAX: {risk}")
    elif evidence.enabled:
        risks.append(f"CODRAX evidence status is {evidence.status}; weak AI must not invent missing dependencies or harnesses.")

    return _dedupe(reason), _dedupe(support), _dedupe(risks)


def _merge_adapter_guidance(adapter, reason: list[str], support: list[str], risks: list[str]):
    return (
        _dedupe([*reason, *adapter.reason]),
        _dedupe([*support, *adapter.required_support]),
        _dedupe([*risks, *adapter.safety_risks]),
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
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
