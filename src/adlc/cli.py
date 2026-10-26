"""Command-line entry point. Commands are added by later work items."""

import argparse
from collections.abc import Sequence

from adlc import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adlc",
        description="Carry work items from a Markdown board to a merged pull request.",
    )
    parser.add_argument("--version", action="version", version=f"adlc {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
