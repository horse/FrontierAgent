from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .doctor import run_doctor
from .errors import ProjectLockedError, RevisionConflictError
from .layout import ProjectLayout, initialize_project
from .store import ProjectStore

EXIT_OK = 0
EXIT_USAGE = 3
EXIT_CONFLICT = 4
EXIT_RUNTIME = 5
EXIT_INTEGRITY = 6


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="frontier-vsi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("--book", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--project-id", required=True)
    init.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status")
    status.add_argument("--book", required=True)
    status.add_argument("--json", action="store_true")

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--book", required=True)
    doctor.add_argument("--json", action="store_true")
    return parser


def _emit(payload: dict[str, Any], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "error_class": type(exc).__name__,
        "error": str(exc),
    }


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    json_mode = bool(args.json)
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

        if args.command == "status":
            snapshot = ProjectStore(root).snapshot()
            _emit(
                {
                    "ok": True,
                    "command": "status",
                    "project_id": snapshot.state.project_id,
                    "title": snapshot.state.title,
                    "revision": snapshot.state.project_revision,
                    "artifact_count": len(snapshot.artifacts),
                    "current_snapshot": snapshot.state.current_snapshot,
                    "last_commit_id": snapshot.state.last_commit_id,
                    "book": str(root),
                },
                json_mode=json_mode,
            )
            return EXIT_OK

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
    except (ProjectLockedError, RevisionConflictError) as exc:
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
