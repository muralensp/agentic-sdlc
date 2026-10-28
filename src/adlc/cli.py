"""Command-line entry point. Commands are added by later work items."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from adlc import __version__
from adlc.methods import MethodsError, check, load_registry, pins_yaml


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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return run_doctor(args)
    return 0
