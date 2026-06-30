from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyzer import analyze_target
from .codrax import codrax_check, codrax_doctor
from .cover import cover_target
from .detached_evidence import evidence_collect, evidence_start, evidence_status
from .diagnose import diagnose_failure
from .discovery import discover_project
from .file_index import index_build, index_refresh, index_status
from .memory import refresh_memory, show_memory
from .mcp_server import run_mcp_server
from .next_round import plan_next_round
from .opencode import write_opencode_files
from .preflight import preflight_check
from .profile_sync import profile_sync
from .profile import write_init_profile
from .run_status import show_status
from .search_backend import search_doctor, search_index, search_query
from .semantic_backend import semantic_doctor, semantic_overview, semantic_references
from .task import build_task
from .understanding import generate_project_understanding
from .upgrade import (
    install_doctor,
    rollback_apply,
    rollback_list,
    upgrade_apply,
    upgrade_inspect,
    restore_custom,
)
from .verify import verify_iteration
from .version import get_version_info, package_root


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gtestcov")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create project_profile.yaml and OpenCode command files.")
    init.add_argument("--project-root", default=".")
    init.add_argument("--overwrite", action="store_true")
    init.add_argument("--source-root", action="append", default=None, help="Project-local source scan root; repeat for multiple roots.")
    init.add_argument("--test-root", action="append", default=None, help="Project-local test scan root; repeat for multiple roots.")
    init.add_argument("--build-root", action="append", default=None, help="Project-local build/config scan root; repeat for multiple roots.")
    init.add_argument("--build-file", default=None, help="Project-local build entry file, for example CMakeLists.txt.")
    init.add_argument("--exclude-dir", action="append", default=None, help="Project-local directory to exclude from scans; repeat for multiple dirs.")
    init.add_argument("--max-files", type=_positive_int, default=None, help="Maximum files to include in project scans.")
    init.add_argument("--max-file-bytes", type=_positive_int, default=None, help="Maximum file bytes to inspect for content probes.")

    discover = sub.add_parser("discover", help="Discover project style.")
    discover.add_argument("--project-root", default=".")

    analyze = sub.add_parser("analyze", help="Analyze a target and write a decision report.")
    analyze.add_argument("--project-root", default=".")
    analyze.add_argument("--target", required=True)
    analyze.add_argument("--run-id", default="")

    obligations = sub.add_parser("obligations", help="Build a test obligation matrix for a target without generating tests.")
    obligations.add_argument("--project-root", default=".")
    obligations.add_argument("--target", required=True)
    obligations.add_argument("--run-id", default="")

    evidence = sub.add_parser("evidence", help="Collect CODRAX-assisted evidence for a target without generating tests.")
    evidence.add_argument("--project-root", default=".")
    evidence.add_argument("--target", default="")
    evidence.add_argument("--run-id", default="")
    evidence_sub = evidence.add_subparsers(dest="evidence_command")
    evidence_start_cmd = evidence_sub.add_parser("start", help="Start detached CODRAX evidence collection.")
    evidence_start_cmd.add_argument("--project-root", default=".")
    evidence_start_cmd.add_argument("--target", required=True)
    evidence_start_cmd.add_argument("--run-id", default="")
    evidence_status_cmd = evidence_sub.add_parser("status", help="Poll detached CODRAX evidence status.")
    evidence_status_cmd.add_argument("--project-root", default=".")
    evidence_status_cmd.add_argument("--run-id", required=True)
    evidence_collect_cmd = evidence_sub.add_parser("collect", help="Collect detached CODRAX evidence result after completion.")
    evidence_collect_cmd.add_argument("--project-root", default=".")
    evidence_collect_cmd.add_argument("--run-id", required=True)
    evidence_collect_cmd.add_argument("--target", default="")
    evidence_collect_cmd.add_argument("--background-worker", action="store_true", help=argparse.SUPPRESS)

    profile_sync_cmd = sub.add_parser("profile-sync", help="Use CODRAX evidence to update project_profile.yaml.")
    profile_sync_cmd.add_argument("--project-root", default=".")
    profile_sync_cmd.add_argument("--target", required=True)
    profile_sync_cmd.add_argument("--line-coverage", type=float, default=None)
    profile_sync_cmd.add_argument("--build-file", default="")
    profile_sync_cmd.add_argument("--run-id", default="")

    cover = sub.add_parser("cover", help="Prepare a single-file coverage task package.")
    cover.add_argument("--project-root", default=".")
    cover.add_argument("--target", required=True)
    cover.add_argument("--line-coverage", type=float, required=True)
    cover.add_argument("--build-file", default="")
    cover.add_argument("--run-id", default="")

    codrax_cmd = sub.add_parser("codrax", help="CODRAX diagnostics and explicit integration checks.")
    codrax_sub = codrax_cmd.add_subparsers(dest="codrax_command", required=True)
    codrax_doctor_cmd = codrax_sub.add_parser(
        "doctor",
        help="Lightweight CODRAX CLI/protocol check; does not read the repository or require file:line evidence.",
        description="Lightweight CODRAX CLI/protocol check. It does not read the repository and does not require file:line evidence.",
    )
    codrax_doctor_cmd.add_argument("--project-root", default=".")
    codrax_doctor_cmd.add_argument("--run-id", default="")

    codrax_check_cmd = sub.add_parser(
        "codrax-check",
        help="Compatibility entry point; no flags runs lightweight doctor, --quick checks explicit files, --deep runs repository citation probe.",
        description=(
            "Compatibility entry point. With no flags it runs the lightweight doctor check. "
            "--quick checks only explicit target/build-file inputs. --deep explicitly runs the long-running repository citation probe."
        ),
    )
    codrax_check_cmd.add_argument("--project-root", default=".")
    codrax_check_cmd.add_argument("--run-id", default="")
    codrax_check_mode = codrax_check_cmd.add_mutually_exclusive_group()
    codrax_check_mode.add_argument("--quick", action="store_true", help="Check only explicit target/build-file inputs; not a full repository search.")
    codrax_check_mode.add_argument("--deep", action="store_true", help="Run the long-running repository citation probe explicitly.")
    codrax_check_cmd.add_argument("--target", default="")
    codrax_check_cmd.add_argument("--build-file", default="")

    task = sub.add_parser("task", help="Build an OpenCode task package.")
    task.add_argument("--project-root", default=".")
    task.add_argument("--target", required=True)
    task.add_argument("--run-id", default="")
    task.add_argument("--line-coverage", type=float, default=None)

    verify = sub.add_parser("verify", help="Run build/test/coverage and generated-test audit.")
    verify.add_argument("--project-root", default=".")
    verify.add_argument("--run-id", default="latest")
    verify.add_argument("--target", default="")
    verify.add_argument("--line-coverage", type=float, default=None)
    verify.add_argument("--max-stagnant-rounds", type=int, default=None)
    verify.add_argument("--min-improvement", type=float, default=None)
    verify.add_argument("--build-timeout", type=int, default=None)
    verify.add_argument("--test-timeout", type=int, default=None)
    verify.add_argument("--coverage-timeout", type=int, default=None)

    check = sub.add_parser("check", help="Run preflight checks before build/test/coverage.")
    check.add_argument("--project-root", default=".")
    check.add_argument("--run-id", default="latest")
    check.add_argument("--target", default="")
    check.add_argument("--no-codrax", action="store_true", help="Run only local preflight checks and skip CODRAX review.")

    diagnose = sub.add_parser("diagnose-failure", help="Use CODRAX to diagnose a failed verify iteration.")
    diagnose.add_argument("--project-root", default=".")
    diagnose.add_argument("--run-id", default="latest")
    diagnose.add_argument("--target", default="")

    next_round = sub.add_parser("next-round", help="Plan the next coverage iteration after an unmet target.")
    next_round.add_argument("--project-root", default=".")
    next_round.add_argument("--run-id", default="latest")
    next_round.add_argument("--max-stagnant-rounds", type=int, default=None)
    next_round.add_argument("--min-improvement", type=float, default=None)

    memory_refresh = sub.add_parser("memory-refresh", help="Refresh run handoff and project memory files.")
    memory_refresh.add_argument("--project-root", default=".")
    memory_refresh.add_argument("--run-id", default="latest")

    memory_show = sub.add_parser("memory-show", help="Show run handoff memory.")
    memory_show.add_argument("--project-root", default=".")
    memory_show.add_argument("--run-id", default="latest")
    memory_show.add_argument("--format", choices=["md", "json"], default="md")

    status = sub.add_parser("status", help="Show current gtestcov and CODRAX run status.")
    status.add_argument("--project-root", default=".")
    status.add_argument("--run-id", default="latest")

    index = sub.add_parser("index", help="Build, refresh, or inspect the gtestcov file index.")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    index_build_cmd = index_sub.add_parser("build", help="Build .gtestcov/cache/file_index.json.")
    index_build_cmd.add_argument("--project-root", default=".")
    index_refresh_cmd = index_sub.add_parser("refresh", help="Refresh .gtestcov/cache/file_index.json incrementally.")
    index_refresh_cmd.add_argument("--project-root", default=".")
    index_status_cmd = index_sub.add_parser("status", help="Show file index readiness and hit/miss reason.")
    index_status_cmd.add_argument("--project-root", default=".")

    search = sub.add_parser("search", help="Optional search backend commands.")
    search_sub = search.add_subparsers(dest="search_command", required=True)
    search_doctor_cmd = search_sub.add_parser("doctor", help="Diagnose optional Zoekt search backend and fallback.")
    search_doctor_cmd.add_argument("--project-root", default=".")
    search_index_cmd = search_sub.add_parser("index", help="Build search indexes, falling back to local file_index.")
    search_index_cmd.add_argument("--project-root", default=".")
    search_query_cmd = search_sub.add_parser("query", help="Query search backend and return EvidenceHit results.")
    search_query_cmd.add_argument("--project-root", default=".")
    search_query_cmd.add_argument("--query", required=True)
    search_query_cmd.add_argument("--limit", type=int, default=80)
    search_query_cmd.add_argument("--regex", action="store_true")

    semantic = sub.add_parser("semantic", help="Optional C/C++ semantic backend commands.")
    semantic_sub = semantic.add_subparsers(dest="semantic_command", required=True)
    semantic_doctor_cmd = semantic_sub.add_parser("doctor", help="Diagnose optional Serena/clangd/ccls backends.")
    semantic_doctor_cmd.add_argument("--project-root", default=".")
    semantic_doctor_cmd.add_argument("--backend", choices=["auto", "serena", "clangd", "ccls"], default="auto")
    semantic_references_cmd = semantic_sub.add_parser("references", help="Find candidate references for a symbol.")
    semantic_references_cmd.add_argument("--project-root", default=".")
    semantic_references_cmd.add_argument("--symbol", required=True)
    semantic_references_cmd.add_argument("--limit", type=int, default=80)
    semantic_references_cmd.add_argument("--backend", choices=["auto", "serena", "clangd", "ccls"], default="auto")
    semantic_overview_cmd = semantic_sub.add_parser("overview", help="Summarize candidate symbols in a target file.")
    semantic_overview_cmd.add_argument("--project-root", default=".")
    semantic_overview_cmd.add_argument("--target", required=True)
    semantic_overview_cmd.add_argument("--limit", type=int, default=80)
    semantic_overview_cmd.add_argument("--backend", choices=["auto", "serena", "clangd", "ccls"], default="auto")

    version = sub.add_parser("version", help="Show gtestcov version and installation details.")
    version.add_argument("--tool-root", default="")
    version.add_argument("--install-mode", choices=["auto", "zip", "git", "unknown"], default="auto")

    install = sub.add_parser("install", help="Install and environment checks.")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    install_doctor_cmd = install_sub.add_parser("doctor", help="Check the active gtestcov installation.")
    install_doctor_cmd.add_argument("--project-root", default="")
    install_doctor_cmd.add_argument("--tool-root", default="")
    install_doctor_cmd.add_argument("--tool-home", default="")

    upgrade = sub.add_parser("upgrade", help="Inspect, apply, or restore a controlled gtestcov upgrade.")
    upgrade_sub = upgrade.add_subparsers(dest="upgrade_command", required=True)
    upgrade_inspect_cmd = upgrade_sub.add_parser("inspect", help="Generate the old-version detection report.")
    upgrade_inspect_cmd.add_argument("--tool-root", default="")
    upgrade_inspect_cmd.add_argument("--project-root", default=".")
    upgrade_inspect_cmd.add_argument("--target-ref", default="main")
    upgrade_inspect_cmd.add_argument("--install-mode", choices=["auto", "zip", "git"], default="auto")
    upgrade_inspect_cmd.add_argument("--upgrade-id", default="")
    upgrade_inspect_cmd.add_argument("--tool-home", default="")

    upgrade_apply_cmd = upgrade_sub.add_parser("apply", help="Apply an inspected upgrade after explicit approval.")
    upgrade_apply_cmd.add_argument("--upgrade-id", required=True)
    upgrade_apply_cmd.add_argument("--approve-overwrite-tool-modifications", action="store_true")
    upgrade_apply_cmd.add_argument("--project-root", default=".")
    upgrade_apply_cmd.add_argument("--tool-root", default="")
    upgrade_apply_cmd.add_argument("--tool-home", default="")
    upgrade_apply_cmd.add_argument("--source-tool-root", default="")
    upgrade_apply_cmd.add_argument("--source-zip", default="")
    upgrade_apply_cmd.add_argument("--install-mode", choices=["auto", "zip", "git"], default="auto")
    upgrade_apply_cmd.add_argument("--venv", default="", help="Existing reusable virtual environment to refresh after slot switch.")
    upgrade_apply_cmd.add_argument("--skip-venv-refresh", action="store_true", help="Do not refresh the active venv entry point.")

    restore_custom_cmd = upgrade_sub.add_parser("restore-custom", help="Restore reviewed custom tool changes after upgrade.")
    restore_custom_cmd.add_argument("--upgrade-id", required=True)
    restore_custom_cmd.add_argument("--tool-home", default="")

    rollback = sub.add_parser("rollback", help="List or apply gtestcov upgrade rollback points.")
    rollback_sub = rollback.add_subparsers(dest="rollback_command", required=True)
    rollback_list_cmd = rollback_sub.add_parser("list", help="List project rollback points.")
    rollback_list_cmd.add_argument("--project-root", default=".")
    rollback_list_cmd.add_argument("--tool-home", default="")
    rollback_apply_cmd = rollback_sub.add_parser("apply", help="Restore old tool slot and old project .gtestcov state.")
    rollback_apply_cmd.add_argument("--upgrade-id", required=True)
    rollback_apply_cmd.add_argument("--project-root", default=".")
    rollback_apply_cmd.add_argument("--tool-home", default="")
    rollback_apply_cmd.add_argument("--approve", action="store_true")
    rollback_apply_cmd.add_argument("--venv", default="", help="Existing reusable virtual environment to refresh after rollback.")
    rollback_apply_cmd.add_argument("--skip-venv-refresh", action="store_true", help="Do not refresh the active venv entry point.")

    sub.add_parser("mcp", help="Start the stdio MCP server.")

    args = parser.parse_args(argv)
    if args.command == "init":
        root = Path(args.project_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        try:
            profile_path, _profile, profile_updated = write_init_profile(
                root,
                overwrite=args.overwrite,
                source_roots=args.source_root,
                test_roots=args.test_root,
                build_roots=args.build_root,
                exclude_dirs=args.exclude_dir,
                build_file=args.build_file,
                max_files=args.max_files,
                max_file_bytes=args.max_file_bytes,
            )
        except ValueError as exc:
            parser.error(str(exc))
        opencode = write_opencode_files(root, overwrite=args.overwrite)
        print(json.dumps({"profile": str(profile_path), "profile_updated": profile_updated, **opencode}, indent=2))
    elif args.command == "discover":
        print(discover_project(Path(args.project_root)).model_dump_json(indent=2))
    elif args.command == "analyze":
        report = analyze_target(Path(args.project_root), args.target, args.run_id or None)
        print(report.model_dump_json(indent=2))
    elif args.command == "obligations":
        report = analyze_target(Path(args.project_root), args.target, args.run_id or None)
        print(
            json.dumps(
                {
                    "run_id": report.run_id,
                    "target": report.target,
                    "test_obligations": [item.model_dump(mode="json") for item in report.test_obligations],
                },
                indent=2,
            )
        )
    elif args.command == "evidence":
        if args.evidence_command == "start":
            print(json.dumps(evidence_start(Path(args.project_root), args.target, args.run_id or None), indent=2))
        elif args.evidence_command == "status":
            print(json.dumps(evidence_status(Path(args.project_root), args.run_id), indent=2))
        elif args.evidence_command == "collect":
            print(
                json.dumps(
                    evidence_collect(
                        Path(args.project_root),
                        args.run_id,
                        target=args.target,
                        background_worker=args.background_worker,
                    ),
                    indent=2,
                )
            )
        else:
            if not args.target:
                parser.error("gtestcov evidence requires --target or a start/status/collect subcommand")
            understanding, evidence_path = generate_project_understanding(Path(args.project_root), args.target, args.run_id or None)
            data = understanding.model_dump(mode="json")
            data["evidence_path"] = str(evidence_path)
            print(json.dumps(data, indent=2))
    elif args.command == "profile-sync":
        print(
            json.dumps(
                profile_sync(Path(args.project_root), args.target, args.run_id or None, args.line_coverage, args.build_file or None),
                indent=2,
            )
        )
    elif args.command == "cover":
        print(
            json.dumps(
                cover_target(Path(args.project_root), args.target, args.line_coverage, args.run_id or None, args.build_file or None),
                indent=2,
            )
        )
    elif args.command == "codrax":
        if args.codrax_command == "doctor":
            print(json.dumps(codrax_doctor(Path(args.project_root), run_id=args.run_id or None), indent=2))
    elif args.command == "codrax-check":
        mode = "quick" if args.quick else "deep" if args.deep else "doctor"
        print(
            json.dumps(
                codrax_check(
                    Path(args.project_root),
                    run_id=args.run_id or None,
                    mode=mode,
                    target=args.target,
                    build_file=args.build_file,
                ),
                indent=2,
            )
        )
    elif args.command == "task":
        report, task_path = build_task(Path(args.project_root), args.target, args.run_id or None, args.line_coverage)
        data = report.model_dump(mode="json")
        data["task_path"] = str(task_path)
        print(json.dumps(data, indent=2))
    elif args.command == "verify":
        print(
            json.dumps(
                verify_iteration(
                    Path(args.project_root),
                    args.run_id,
                    args.target,
                    args.line_coverage,
                    args.max_stagnant_rounds,
                    args.min_improvement,
                    args.build_timeout,
                    args.test_timeout,
                    args.coverage_timeout,
                ),
                indent=2,
            )
        )
    elif args.command == "check":
        print(json.dumps(preflight_check(Path(args.project_root), args.run_id, args.target, include_codrax=not args.no_codrax), indent=2))
    elif args.command == "diagnose-failure":
        print(json.dumps(diagnose_failure(Path(args.project_root), args.run_id, args.target), indent=2))
    elif args.command == "next-round":
        print(
            json.dumps(
                plan_next_round(
                    Path(args.project_root),
                    args.run_id,
                    max_stagnant_rounds=args.max_stagnant_rounds,
                    min_improvement=args.min_improvement,
                ),
                indent=2,
            )
        )
    elif args.command == "memory-refresh":
        print(json.dumps(refresh_memory(Path(args.project_root), args.run_id), indent=2))
    elif args.command == "memory-show":
        result = show_memory(Path(args.project_root), args.run_id, args.format)
        if args.format == "json":
            print(json.dumps(result["content"], indent=2))
        else:
            print(result["content"])
    elif args.command == "status":
        print(json.dumps(show_status(Path(args.project_root), args.run_id), indent=2))
    elif args.command == "index":
        if args.index_command == "build":
            print(json.dumps(index_build(Path(args.project_root)), indent=2))
        elif args.index_command == "refresh":
            print(json.dumps(index_refresh(Path(args.project_root)), indent=2))
        elif args.index_command == "status":
            print(json.dumps(index_status(Path(args.project_root)), indent=2))
    elif args.command == "search":
        if args.search_command == "doctor":
            print(json.dumps(search_doctor(Path(args.project_root)), indent=2))
        elif args.search_command == "index":
            print(json.dumps(search_index(Path(args.project_root)), indent=2))
        elif args.search_command == "query":
            print(json.dumps(search_query(Path(args.project_root), args.query, limit=args.limit, regex=args.regex), indent=2))
    elif args.command == "semantic":
        if args.semantic_command == "doctor":
            print(json.dumps(semantic_doctor(Path(args.project_root), backend=args.backend), indent=2))
        elif args.semantic_command == "references":
            print(
                json.dumps(
                    semantic_references(Path(args.project_root), args.symbol, limit=args.limit, backend=args.backend),
                    indent=2,
                )
            )
        elif args.semantic_command == "overview":
            print(
                json.dumps(
                    semantic_overview(Path(args.project_root), args.target, limit=args.limit, backend=args.backend),
                    indent=2,
                )
            )
    elif args.command == "version":
        tool_root = Path(args.tool_root).resolve() if args.tool_root else package_root()
        print(json.dumps(get_version_info(tool_root, args.install_mode).as_dict(), indent=2))
    elif args.command == "install":
        project_root = Path(args.project_root).resolve() if args.project_root else None
        tool_root = Path(args.tool_root).resolve() if args.tool_root else None
        tool_home = Path(args.tool_home).expanduser() if args.tool_home else None
        print(json.dumps(install_doctor(project_root, tool_root, tool_home), indent=2))
    elif args.command == "upgrade":
        tool_home = Path(args.tool_home).expanduser() if getattr(args, "tool_home", "") else None
        if args.upgrade_command == "inspect":
            tool_root = Path(args.tool_root).resolve() if args.tool_root else package_root()
            print(
                json.dumps(
                    upgrade_inspect(
                        tool_root=tool_root,
                        project_root=Path(args.project_root),
                        target_ref=args.target_ref,
                        install_mode=args.install_mode,
                        upgrade_id=args.upgrade_id or None,
                        tool_home=tool_home,
                    ),
                    indent=2,
                )
            )
        elif args.upgrade_command == "apply":
            tool_root = Path(args.tool_root).resolve() if args.tool_root else package_root()
            source_tool_root = Path(args.source_tool_root).resolve() if args.source_tool_root else None
            source_zip = Path(args.source_zip).resolve() if args.source_zip else None
            print(
                json.dumps(
                    upgrade_apply(
                        upgrade_id=args.upgrade_id,
                        approve_overwrite_tool_modifications=args.approve_overwrite_tool_modifications,
                        tool_home=tool_home,
                        project_root=Path(args.project_root),
                        tool_root=tool_root,
                        source_tool_root=source_tool_root,
                        source_zip=source_zip,
                        install_mode=None if args.install_mode == "auto" else args.install_mode,
                        venv_path=Path(args.venv).expanduser() if args.venv else None,
                        skip_venv_refresh=args.skip_venv_refresh,
                    ),
                    indent=2,
                )
            )
        elif args.upgrade_command == "restore-custom":
            print(json.dumps(restore_custom(args.upgrade_id, tool_home), indent=2))
    elif args.command == "rollback":
        tool_home = Path(args.tool_home).expanduser() if args.tool_home else None
        if args.rollback_command == "list":
            print(json.dumps(rollback_list(Path(args.project_root), tool_home), indent=2))
        elif args.rollback_command == "apply":
            print(
                json.dumps(
                    rollback_apply(
                        args.upgrade_id,
                        Path(args.project_root),
                        args.approve,
                        tool_home,
                        Path(args.venv).expanduser() if args.venv else None,
                        args.skip_venv_refresh,
                    ),
                    indent=2,
                )
            )
    elif args.command == "mcp":
        run_mcp_server()


if __name__ == "__main__":
    main()
