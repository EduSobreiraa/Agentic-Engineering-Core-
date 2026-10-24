"""Read-only operational command line interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .queries import OperationalReader


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aec")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    runs = commands.add_parser("runs").add_subparsers(dest="action", required=True)
    runs.add_parser("list")
    for action in ("inspect", "timeline"):
        runs.add_parser(action).add_argument("run_id")
    commands.add_parser("agents").add_subparsers(
        dest="action", required=True
    ).add_parser("list")
    commands.add_parser("approvals").add_subparsers(
        dest="action", required=True
    ).add_parser("pending")
    commands.add_parser("processes").add_subparsers(
        dest="action", required=True
    ).add_parser("inspect").add_argument("run_id")
    commands.add_parser("metrics").add_subparsers(
        dest="action", required=True
    ).add_parser("summary")
    commands.add_parser("methodologies").add_subparsers(
        dest="action", required=True
    ).add_parser("list")
    args = parser.parse_args(argv)
    try:
        reader = OperationalReader(args.database)
        result: Any
        if args.command == "status":
            result = reader.status()
        elif args.command == "runs":
            if args.action == "list":
                result = reader.runs()
            elif args.action == "inspect":
                result = reader.inspect(args.run_id)
            else:
                result = reader.timeline(args.run_id)
        elif args.command == "agents":
            result = reader.agents()
        elif args.command == "approvals":
            result = reader.pending_approvals()
        elif args.command == "processes":
            result = reader.processes(args.run_id)
        elif args.command == "metrics":
            result = reader.metrics()
        else:
            result = reader.methodologies()
    except (FileNotFoundError, KeyError, OSError) as exc:
        parser.exit(2, f"aec: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, indent=None if args.json else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
