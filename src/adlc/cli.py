"""Command-line entry point. Commands are added by later work items.

Exit codes: 0 done, 1 the request cannot be carried out (a mistake), 2 the config or board is
unusable. Messages for the person go to stderr, prefixed `adlc:`.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from adlc import __version__
from adlc.board import BoardError
from adlc.commands import (
    CommandError,
    add_item,
    find_repo,
    read_log,
    render_status,
    status_report,
)
from adlc.config import ConfigError
from adlc.methods import MethodsError, check, load_registry, pins_yaml
from adlc.tasks import TYPES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adlc",
        description="Carry work items from a Markdown board to a merged pull request.",
    )
    parser.add_argument("--version", action="version", version=f"adlc {__version__}")
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser("doctor", help="verify the pinned methods (skills)")
    doctor.add_argument("--repo", type=Path, default=Path.cwd(), help="target repository")
    doctor.add_argument("--config", type=Path, help="default: <repo>/.adlc/config.yaml")
    doctor.add_argument("--skills-root", type=Path, help="where plugin skills are installed")
    doctor.add_argument(
        "--print-pins", action="store_true", help="print a methods block with current hashes"
    )
    add = commands.add_parser("add", help="add a DRAFT work item and its brief skeleton")
    add.add_argument("code", help="context code, e.g. PLAT")
    add.add_argument("title")
    add.add_argument("--type", dest="kind", choices=TYPES, default="feature")
    add.add_argument("--priority", type=int, help="default: after every existing item")
    add.add_argument("--depends", action="append", default=[], metavar="KEY", help="repeatable")
    add.add_argument("--prd", default="", help="text for the Linked PRD column")
    add.add_argument("--new-code", action="store_true", help="allow a code with no existing item")
    status = commands.add_parser("status", help="show the board, problems first")
    status.add_argument("--all", action="store_true", help="also list DONE items")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    log = commands.add_parser("log", help="print the latest run summary of an item")
    log.add_argument("key")
    log.add_argument("--run", type=int, help="a specific run number (default: the newest)")
    for command in (add, status, log):
        command.add_argument("--repo", type=Path, help="default: the repository around this folder")
    return parser


def run_doctor(args: argparse.Namespace) -> int:
    config = args.config or args.repo / ".adlc" / "config.yaml"
    try:
        registry = load_registry(config)
        if args.print_pins:
            print(pins_yaml(registry, args.repo, args.skills_root), end="")
            return 0
        results = check(registry, args.repo, args.skills_root)
    except MethodsError as exc:
        print(f"adlc doctor: {exc}", file=sys.stderr)
        return 2
    print(f"{'STAGE':<15}{'SKILL':<22}{'STATUS':<15}{'REQUIRED':<10}FALLBACK")
    for r in results:
        print(
            f"{r.stage:<15}{r.skill:<22}{r.status:<15}{'yes' if r.required else 'no':<10}"
            f"{'yes' if r.fallback else 'no'}" + (f"   {r.detail}" if r.detail else "")
        )
    bad = [r for r in results if not r.ok]
    print(
        f"\n{len(results) - len(bad)} of {len(results)} methods ok"
        + ("" if not bad else "; FAILED")
    )
    return 1 if bad else 0


def _add(args: argparse.Namespace, repo: Path) -> int:
    key = add_item(
        repo,
        args.code,
        args.title,
        kind=args.kind,
        priority=args.priority,
        depends=args.depends,
        prd=args.prd,
        new_code=args.new_code,
    )
    print(key)
    print(f"fill in the acceptance criteria in the brief for {key}, then move it to BACKLOG")
    return 0


def _status(args: argparse.Namespace, repo: Path) -> int:
    report = status_report(repo)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_status(report, repo=repo, all_items=args.all), end="")
    return 0


def _log(args: argparse.Namespace, repo: Path) -> int:
    text = read_log(repo, args.key, args.run)
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


_BOARD_COMMANDS = {"add": _add, "status": _status, "log": _log}


def run_board_command(args: argparse.Namespace) -> int:
    try:
        repo = args.repo or find_repo(Path.cwd())
        return _BOARD_COMMANDS[args.command](args, repo)
    except CommandError as exc:
        print(f"adlc: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print("adlc: invalid configuration", file=sys.stderr)
        for problem in exc.problems:
            print(f"adlc:   {problem}", file=sys.stderr)
        return 2
    except BoardError as exc:
        print(f"adlc: {exc}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command in _BOARD_COMMANDS:
        return run_board_command(args)
    return 0
