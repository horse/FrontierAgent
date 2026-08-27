from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical_json import canonical_json_bytes, sha256_bytes
from .decisions import decide_issue
from .doctor import run_doctor
from .errors import (
    IdempotencyConflictError,
    ProjectLockedError,
    RevisionConflictError,
)
from .issues import iter_issues
from .layout import initialize_project
from .models import RequestRecord, RequestStatus
from .publication import approve_author, finalize_publication
from .requests import begin_request, complete_request, fail_request, lookup_request
from .runlog import RecordingRunner, RunTracker
from .runtime import StandaloneRuntime
from .service import EditorialService, StageResult, gate_status
from .store import ProjectStore

EXIT_OK = 0
EXIT_USAGE = 3
EXIT_CONFLICT = 4
EXIT_RUNTIME = 5
EXIT_INTEGRITY = 6


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def _add_mutation_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--if-revision", type=int)
    _add_json(parser)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="frontier-vsi")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--book", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--project-id", required=True)
    _add_json(init)

    status = commands.add_parser("status")
    status.add_argument("--book", required=True)
    _add_json(status)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--book", required=True)
    _add_json(doctor)

    ingest = commands.add_parser("ingest")
    ingest.add_argument("--book", required=True)
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--file", required=True)
    _add_mutation_common(ingest)

    run = commands.add_parser("run")
    run_stages = run.add_subparsers(dest="stage", required=True)

    commission = run_stages.add_parser("commission")
    commission.add_argument("--book", required=True)
    brief = commission.add_mutually_exclusive_group(required=True)
    brief.add_argument("--brief")
    brief.add_argument("--brief-file")
    _add_mutation_common(commission)

    research = run_stages.add_parser("research")
    research.add_argument("--book", required=True)
    research.add_argument("--focus", required=True)
    research.add_argument("--max-parallel", type=int, default=4)
    _add_mutation_common(research)

    for stage_name in ("architecture", "style", "chapters", "full-audit"):
        stage_parser = run_stages.add_parser(stage_name)
        stage_parser.add_argument("--book", required=True)
        _add_mutation_common(stage_parser)

    control = run_stages.add_parser("control-chapter")
    control.add_argument("--book", required=True)
    control.add_argument("--chapter")
    _add_mutation_common(control)

    chapter = run_stages.add_parser("chapter")
    chapter.add_argument("--book", required=True)
    chapter.add_argument("--chapter", required=True)
    _add_mutation_common(chapter)

    issues = commands.add_parser("issues")
    issues.add_argument("--book", required=True)
    issues.add_argument("--scope")
    _add_json(issues)

    decide = commands.add_parser("decide")
    decide.add_argument("--book", required=True)
    decide.add_argument("--issue", required=True)
    decide.add_argument(
        "--disposition",
        required=True,
        choices=("REPAIR", "RESEARCH_GAP", "RESOLVED", "REJECTED"),
    )
    decide.add_argument("--rationale", required=True)
    decide.add_argument("--decided-by", required=True)
    _add_mutation_common(decide)

    approval = commands.add_parser("approve-author")
    approval.add_argument("--book", required=True)
    approval.add_argument("--approved-by", required=True)
    approval.add_argument("--note", default="")
    _add_mutation_common(approval)

    export = commands.add_parser("export")
    export.add_argument("--book", required=True)
    _add_mutation_common(export)

    request = commands.add_parser("request")
    request.add_argument("--book", required=True)
    request.add_argument("--request-id", required=True)
    _add_json(request)

    return parser


def _emit(payload: dict[str, Any], *, json_mode: bool) -> None:
    if json_mode:
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "error_class": type(exc).__name__,
        "error": str(exc),
    }


def _command_payload(args: argparse.Namespace) -> dict[str, object]:
    omitted = {"json", "request_id"}
    return {
        key: value
        for key, value in sorted(vars(args).items())
        if key not in omitted
    }


def _command_fingerprint(args: argparse.Namespace) -> str:
    return sha256_bytes(canonical_json_bytes(_command_payload(args)))


def _begin_owned_mutation(
    store: ProjectStore,
    args: argparse.Namespace,
) -> tuple[str, RequestRecord | None]:
    fingerprint = _command_fingerprint(args)
    record, is_owner = begin_request(
        store.layout.root,
        args.request_id,
        fingerprint,
    )
    if not is_owner:
        if record.status == RequestStatus.COMPLETED:
            return fingerprint, record
        if record.status == RequestStatus.FAILED:
            raise RuntimeError(
                f"request {args.request_id!r} already failed: {record.error or 'unknown error'}"
            )
        raise IdempotencyConflictError(
            f"request {args.request_id!r} is already in progress"
        )

    if args.if_revision is not None:
        current = store.snapshot().state.project_revision
        if current != args.if_revision:
            exc = RevisionConflictError(
                f"expected revision {args.if_revision}, current is {current}"
            )
            fail_request(
                store.layout.root,
                args.request_id,
                fingerprint,
                error=str(exc),
            )
            raise exc
    return fingerprint, None


def _replayed_result(record: RequestRecord) -> dict[str, object]:
    payload = dict(record.result or {})
    payload["replayed"] = True
    return payload


def _complete_mutation(
    store: ProjectStore,
    args: argparse.Namespace,
    fingerprint: str,
    result: dict[str, object],
) -> dict[str, object]:
    result["replayed"] = False
    complete_request(
        store.layout.root,
        args.request_id,
        fingerprint,
        result=result,
    )
    return result


def _fail_mutation(
    store: ProjectStore,
    args: argparse.Namespace,
    fingerprint: str,
    exc: BaseException,
) -> None:
    fail_request(
        store.layout.root,
        args.request_id,
        fingerprint,
        error=f"{type(exc).__name__}: {exc}",
    )


def _stage_payload(
    store: ProjectStore,
    args: argparse.Namespace,
    result: StageResult,
    *,
    run_id: str,
) -> dict[str, object]:
    return {
        "ok": True,
        "command": "run",
        "stage": result.stage,
        "status": result.status,
        "project_id": store.snapshot().state.project_id,
        "revision": result.project_revision,
        "details": result.details,
        "run_id": run_id,
        "request_id": args.request_id,
    }


def _read_commission_brief(args: argparse.Namespace) -> str:
    if args.brief is not None:
        return str(args.brief)
    return Path(args.brief_file).read_text(encoding="utf-8")


async def _call_stage(
    service: EditorialService,
    store: ProjectStore,
    args: argparse.Namespace,
) -> StageResult:
    if args.stage == "commission":
        return await service.commission(store, brief=_read_commission_brief(args))
    if args.stage == "research":
        return await service.research(
            store,
            focus=args.focus,
            max_parallel=args.max_parallel,
        )
    if args.stage == "architecture":
        return await service.architecture(store)
    if args.stage == "style":
        return await service.style(store)
    if args.stage == "control-chapter":
        return await service.control_chapter(store, chapter_id=args.chapter)
    if args.stage == "chapter":
        return await service.chapter(store, chapter_id=args.chapter)
    if args.stage == "chapters":
        return await service.chapters(store)
    if args.stage == "full-audit":
        return await service.full_audit(store)
    raise ValueError(f"unsupported stage: {args.stage}")


def _execute_stage(store: ProjectStore, args: argparse.Namespace) -> dict[str, object]:
    fingerprint, replay = _begin_owned_mutation(store, args)
    if replay is not None:
        return _replayed_result(replay)

    tracker: RunTracker | None = None
    try:
        with StandaloneRuntime() as runtime:
            if runtime.runner is None:
                raise RuntimeError("standalone FrontierAgent runner did not initialize")
            tracker = RunTracker.start(
                store,
                stage=args.stage,
                command=f"run {args.stage}",
                request_id=args.request_id,
                runtime_info=runtime.info,
            )
            runner = RecordingRunner(runtime.runner, tracker)
            service = EditorialService(runner)
            stage_result = asyncio.run(_call_stage(service, store, args))
            result = _stage_payload(
                store,
                args,
                stage_result,
                run_id=tracker.run_id,
            )
            tracker.complete(store, result)
            return _complete_mutation(store, args, fingerprint, result)
    except Exception as exc:
        if tracker is not None:
            tracker.fail(exc)
        _fail_mutation(store, args, fingerprint, exc)
        raise


def _validate_ingest_target(value: str) -> str:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("ingest path must be a safe project-relative path")
    normalized = pure.as_posix()
    if not normalized.startswith(("samples/", "corpus/")):
        raise ValueError("ingest path must live under samples/ or corpus/")
    return normalized


def _execute_ingest(store: ProjectStore, args: argparse.Namespace) -> dict[str, object]:
    fingerprint, replay = _begin_owned_mutation(store, args)
    if replay is not None:
        return _replayed_result(replay)
    tracker = RunTracker.start(
        store,
        stage="ingest",
        command="ingest",
        request_id=args.request_id,
    )
    try:
        target = _validate_ingest_target(args.path)
        source = Path(args.file)
        data = source.read_bytes()
        snapshot = store.snapshot()
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={target: data},
            actor="human:ingest",
            reason=f"ingest external file into {target}",
        )
        ref = store.snapshot().artifacts[target]
        result: dict[str, object] = {
            "ok": True,
            "command": "ingest",
            "status": "INGESTED",
            "project_id": state.project_id,
            "revision": state.project_revision,
            "path": target,
            "sha256": ref.sha256,
            "run_id": tracker.run_id,
            "request_id": args.request_id,
        }
        tracker.complete(store, result)
        return _complete_mutation(store, args, fingerprint, result)
    except Exception as exc:
        tracker.fail(exc)
        _fail_mutation(store, args, fingerprint, exc)
        raise


def _execute_decide(store: ProjectStore, args: argparse.Namespace) -> dict[str, object]:
    fingerprint, replay = _begin_owned_mutation(store, args)
    if replay is not None:
        return _replayed_result(replay)
    tracker = RunTracker.start(
        store,
        stage="decide",
        command="decide",
        request_id=args.request_id,
    )
    try:
        decision = decide_issue(
            store,
            issue_id=args.issue,
            disposition=args.disposition,
            rationale=args.rationale,
            decided_by=args.decided_by,
        )
        result: dict[str, object] = {
            "ok": True,
            "command": "decide",
            "status": "DECIDED",
            "project_id": store.snapshot().state.project_id,
            "revision": store.snapshot().state.project_revision,
            "decision_id": decision.decision_id,
            "issue_id": decision.issue_id,
            "disposition": decision.disposition,
            "run_id": tracker.run_id,
            "request_id": args.request_id,
        }
        tracker.complete(store, result)
        return _complete_mutation(store, args, fingerprint, result)
    except Exception as exc:
        tracker.fail(exc)
        _fail_mutation(store, args, fingerprint, exc)
        raise


def _execute_author_approval(
    store: ProjectStore,
    args: argparse.Namespace,
) -> dict[str, object]:
    fingerprint, replay = _begin_owned_mutation(store, args)
    if replay is not None:
        return _replayed_result(replay)
    tracker = RunTracker.start(
        store,
        stage="approve-author",
        command="approve-author",
        request_id=args.request_id,
    )
    try:
        revision = approve_author(
            store,
            approved_by=args.approved_by,
            note=args.note,
        )
        result: dict[str, object] = {
            "ok": True,
            "command": "approve-author",
            "status": "AUTHOR_APPROVED",
            "project_id": store.snapshot().state.project_id,
            "revision": revision,
            "approved_by": args.approved_by,
            "run_id": tracker.run_id,
            "request_id": args.request_id,
        }
        tracker.complete(store, result)
        return _complete_mutation(store, args, fingerprint, result)
    except Exception as exc:
        tracker.fail(exc)
        _fail_mutation(store, args, fingerprint, exc)
        raise


def _execute_export(store: ProjectStore, args: argparse.Namespace) -> dict[str, object]:
    fingerprint, replay = _begin_owned_mutation(store, args)
    if replay is not None:
        return _replayed_result(replay)
    tracker = RunTracker.start(
        store,
        stage="export",
        command="export",
        request_id=args.request_id,
    )
    try:
        final = finalize_publication(store)
        result: dict[str, object] = {
            "ok": True,
            "command": "export",
            "status": final.status,
            "project_id": store.snapshot().state.project_id,
            "revision": final.project_revision,
            "manuscript": "publication/MANUSCRIPT.md",
            "manifest": "publication/MANIFEST.json",
            "run_id": tracker.run_id,
            "request_id": args.request_id,
        }
        tracker.complete(store, result)
        return _complete_mutation(store, args, fingerprint, result)
    except Exception as exc:
        tracker.fail(exc)
        _fail_mutation(store, args, fingerprint, exc)
        raise


def _status_payload(store: ProjectStore) -> dict[str, object]:
    snapshot = store.snapshot()
    gates = {
        gate: gate_status(snapshot, gate).value
        for gate in (
            "FRAMING_READY",
            "RESEARCH_READY",
            "ARCHITECTURE_LOCKED",
            "STYLE_LOCKED",
            "CONTROL_CHAPTER_PASS",
            "AUTHOR_APPROVED",
            "PUBLICATION_READY",
        )
    }
    open_issues = [
        issue.issue_id
        for issue in iter_issues(store)
        if issue.status not in {"RESOLVED", "VERIFIED", "REJECTED"}
    ]
    return {
        "ok": True,
        "command": "status",
        "project_id": snapshot.state.project_id,
        "title": snapshot.state.title,
        "revision": snapshot.state.project_revision,
        "artifact_count": len(snapshot.artifacts),
        "current_snapshot": snapshot.state.current_snapshot,
        "last_commit_id": snapshot.state.last_commit_id,
        "gates": gates,
        "open_issue_ids": open_issues,
        "book": str(store.layout.root),
    }


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    json_mode = bool(getattr(args, "json", False))
    root = Path(args.book)
    try:
        if args.command == "init":
            state = initialize_project(root, project_id=args.project_id, title=args.title)
            _emit(
                {
                    "ok": True,
                    "command": "init",
                    "project_id": state.project_id,
                    "title": state.title,
                    "revision": state.project_revision,
                    "book": str(root),
                },
                json_mode=json_mode,
            )
            return EXIT_OK

        store = ProjectStore(root)
        if args.command == "status":
            _emit(_status_payload(store), json_mode=json_mode)
            return EXIT_OK
        if args.command == "doctor":
            report = run_doctor(root)
            _emit(
                {
                    "ok": report.ok,
                    "command": "doctor",
                    "checks": report.checks,
                    "errors": report.errors,
                    "warnings": report.warnings,
                    "book": str(root),
                },
                json_mode=json_mode,
            )
            return EXIT_OK if report.ok else EXIT_INTEGRITY
        if args.command == "issues":
            records = iter_issues(store, scope=args.scope)
            _emit(
                {
                    "ok": True,
                    "command": "issues",
                    "scope": args.scope,
                    "issues": [record.model_dump(mode="json") for record in records],
                },
                json_mode=json_mode,
            )
            return EXIT_OK
        if args.command == "request":
            record = lookup_request(root, args.request_id)
            if record is None:
                raise FileNotFoundError(f"request not found: {args.request_id}")
            _emit(
                {
                    "ok": True,
                    "command": "request",
                    "request": record.model_dump(mode="json"),
                },
                json_mode=json_mode,
            )
            return EXIT_OK
        if args.command == "ingest":
            result = _execute_ingest(store, args)
        elif args.command == "run":
            result = _execute_stage(store, args)
        elif args.command == "decide":
            result = _execute_decide(store, args)
        elif args.command == "approve-author":
            result = _execute_author_approval(store, args)
        elif args.command == "export":
            result = _execute_export(store, args)
        else:
            raise ValueError(f"unsupported command: {args.command}")
        _emit(result, json_mode=json_mode)
        return EXIT_OK
    except (ProjectLockedError, RevisionConflictError, IdempotencyConflictError) as exc:
        _emit(_error_payload(exc), json_mode=json_mode)
        return EXIT_CONFLICT
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        _emit(_error_payload(exc), json_mode=json_mode)
        return EXIT_USAGE
    except Exception as exc:
        _emit(_error_payload(exc), json_mode=json_mode)
        return EXIT_RUNTIME


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
