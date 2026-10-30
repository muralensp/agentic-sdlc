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
from adlc import refine as refine_module
from adlc.baseline import STORE, take_baseline
from adlc.board import BoardError
from adlc.commands import (
    CommandError,
    add_item,
    find_repo,
    read_log,
    render_status,
    status_report,
)
from adlc.config import ConfigError, load_config
from adlc.escalation import approve, edit, reject, render_report, report, retry
from adlc.init import adopt, detect, file_plan, gh_protection, render_adoption
from adlc.methods import (
    MethodsError,
    check,
    index_warnings,
    load_registry,
    pins_yaml,
    supply_chain_warnings,
)
from adlc.plan import plan_run, render_plan
from adlc.pr import GhCli
from adlc.recovery import handoff, reset, resume
from adlc.runlog import loop_status
from adlc.scaffold import STACKS, create_repo, default_code, render_scaffold, scaffold
from adlc.states import IllegalTransition
from adlc.tasks import TYPES
from adlc.trust import enable
from adlc.trust import status as trust_status


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
    doctor.add_argument(
        "--baseline",
        action="store_true",
        help="run the profile's Definition of Done commands once and save the result",
    )
    doctor.add_argument("--only", action="append", default=[], metavar="NAME", help="repeatable")
    doctor.add_argument("--skip", action="append", default=[], metavar="NAME", help="repeatable")
    doctor.add_argument(
        "--timeout", type=float, default=20, metavar="MINUTES", help="per command (default 20)"
    )
    doctor.add_argument(
        "--allow-dirty", action="store_true", help="baseline even with uncommitted changes"
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
    refine = commands.add_parser(
        "refine", help="grill an idea or DRAFT item into a ready brief (a person must be present)"
    )
    refine.add_argument("target", help="a work item key, or the text of a new idea")
    refine.add_argument("--code", help="context code for a new idea (default: board.code)")
    refine.add_argument("--new-code", action="store_true", help="allow a code with no items yet")
    status_loop = commands.add_parser(
        "loop-status", help="show a run's iterations, usage, last failure and stop reason"
    )
    status_loop.add_argument("key")
    status_loop.add_argument("--run", type=int, help="a specific run number (default: the newest)")
    run = commands.add_parser("run", help="plan or run a task")
    run.add_argument("key")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="show the task, branch and prompts without calling a model or touching git",
    )
    run.add_argument("--skills-root", type=Path, help="where plugin skills are installed")
    approve_cmd = commands.add_parser("approve", help="let an escalated task merge as it is")
    retry_cmd = commands.add_parser("retry", help="run an escalated task again, with a note")
    retry_cmd.add_argument("--note", help="feedback carried into the next run")
    retry_cmd.add_argument(
        "--refine", action="store_true", help="reopen the grilling session (spec problems)"
    )
    edit_cmd = commands.add_parser("edit", help="send an escalated task back to DRAFT to change it")
    reject_cmd = commands.add_parser("reject", help="give up on an escalated task (FAILED)")
    reject_cmd.add_argument("--reason", default="", help="why, for the board's Notes")
    for command in (approve_cmd, retry_cmd, edit_cmd, reject_cmd):
        command.add_argument("key")
    report_cmd = commands.add_parser("report", help="escalation rates and how often you overrode")
    init_cmd = commands.add_parser("init", help="adopt this repository (dry run unless --yes)")
    init_cmd.add_argument("--yes", action="store_true", help="deliver the plan as a branch and PR")
    new_cmd = commands.add_parser("new", help="scaffold a fresh repository (dry run unless --yes)")
    new_cmd.add_argument("name", help="lower case words joined by -; names the folder")
    new_cmd.add_argument("--stack", required=True, choices=STACKS)
    new_cmd.add_argument("--code", help="board key prefix (default: from the name)")
    new_cmd.add_argument("--dest", type=Path, default=Path.cwd(), help="parent folder (default: .)")
    new_cmd.add_argument("--yes", action="store_true", help="create the folder and first commit")
    trust_cmd = commands.add_parser("trust", help="turn auto-merge on, once it is earned")
    trust_cmd.add_argument("--yes", action="store_true", help="turn it on (default: report)")
    resume_cmd = commands.add_parser("resume", help="continue an interrupted or paused task")
    resume_cmd.add_argument("--force", action="store_true", help="ignore the usage-window limits")
    reset_cmd = commands.add_parser("reset", help="discard a started task's worktree and branch")
    reset_cmd.add_argument(
        "--yes", action="store_true", help="really delete (nothing is lost without it)"
    )
    handoff_cmd = commands.add_parser("handoff", help="write a short resume note for a task")
    for command in (resume_cmd, reset_cmd, handoff_cmd):
        command.add_argument("key")
    for command in (
        add,
        status,
        log,
        refine,
        status_loop,
        run,
        approve_cmd,
        retry_cmd,
        edit_cmd,
        reject_cmd,
        report_cmd,
        resume_cmd,
        reset_cmd,
        handoff_cmd,
        init_cmd,
        trust_cmd,
    ):
        command.add_argument("--repo", type=Path, help="default: the repository around this folder")
    return parser


def run_baseline(args: argparse.Namespace) -> int:
    try:
        baseline = take_baseline(
            args.repo,
            load_config(args.repo),
            timeout=args.timeout * 60,
            only=args.only,
            skip=args.skip,
            allow_dirty=args.allow_dirty,
            progress=lambda name: print(f"running {name} ...", flush=True),
        )
    except CommandError as exc:
        print(f"adlc doctor: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print("adlc doctor: invalid configuration", file=sys.stderr)
        for problem in exc.problems:
            print(f"adlc doctor:   {problem}", file=sys.stderr)
        return 2
    print(f"\n{'NAME':<18}{'STATUS':<10}{'EXIT':<7}{'SECONDS':<9}NOTE")
    for c in baseline.commands:
        note = (
            "flaky: passed on the retry" if c.flaky else "retried once" if c.attempts == 2 else ""
        )
        exit_code = "" if c.exit_code is None else str(c.exit_code)
        print(f"{c.name:<18}{c.status:<10}{exit_code:<7}{c.duration:<9.1f}{note}")
    ran = [c for c in baseline.commands if c.status != "skipped"]
    passed = [c for c in ran if c.status == "pass"]
    print(
        f"\n{len(passed)} of {len(ran)} passed; baseline saved to {STORE.as_posix()}/baseline.json"
    )
    return 0 if len(passed) == len(ran) else 1


def _advice(args: argparse.Namespace, registry) -> list[str]:
    """Warnings that do not fail the doctor: risky skill text and over-long index files."""
    try:
        limit = load_config(args.repo).limits.max_index_lines
    except ConfigError:
        limit = 150
    return [
        *supply_chain_warnings(registry, args.repo, args.skills_root),
        *index_warnings(args.repo, limit),
    ]


def run_doctor(args: argparse.Namespace) -> int:
    if args.baseline:
        return run_baseline(args)
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
    for warning in _advice(args, registry):
        print(f"WARN  {warning}")
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


def _refine(args: argparse.Namespace, repo: Path) -> int:
    refine_module.refine(repo, args.target, code=args.code, new_code=args.new_code)
    return 0


def _loop_status(args: argparse.Namespace, repo: Path) -> int:
    print(loop_status(repo, args.key, args.run), end="")
    return 0


def _run(args: argparse.Namespace, repo: Path) -> int:
    if not args.dry_run:
        raise CommandError(
            "running a task is not built yet; use --dry-run to see its branch and prompts"
        )
    print(render_plan(plan_run(repo, args.key, skills_root=args.skills_root)), end="")
    return 0


def _approve(args: argparse.Namespace, repo: Path) -> int:
    approve(repo, args.key)
    print(f"{args.key} approved; it stays NEEDS_HUMAN until the PR step merges it")
    return 0


def _retry(args: argparse.Namespace, repo: Path) -> int:
    retry(repo, args.key, note=args.note, refine=args.refine)
    print(f"{args.key} will run again with a fresh budget")
    return 0


def _edit(args: argparse.Namespace, repo: Path) -> int:
    print(f"{args.key} is DRAFT again; change {edit(repo, args.key)}, then move it to BACKLOG")
    return 0


def _reject(args: argparse.Namespace, repo: Path) -> int:
    reject(repo, args.key, reason=args.reason)
    print(f"{args.key} is FAILED")
    return 0


def _report(args: argparse.Namespace, repo: Path) -> int:
    print(render_report(report(repo)), end="")
    return 0


def _resume(args: argparse.Namespace, repo: Path) -> int:
    got = resume(repo, args.key, force=args.force)
    print(f"{got.key}: continue in {got.worktree} on branch {got.branch}")
    print(f"  progress.md: {'present' if got.progress else 'none yet'}")
    if got.last_failure:
        print(f"  last failure: {got.last_failure}")
    if got.was_paused:
        print("  the usage-window pause is cleared")
    return 0


def _reset(args: argparse.Namespace, repo: Path) -> int:
    done = reset(repo, args.key, yes=args.yes)
    print(f"{args.key} is back in BACKLOG; worktree removed: {done.worktree_removed}")
    return 0


def _init(args: argparse.Namespace, repo: Path) -> int:
    protection = gh_protection(repo)
    if not args.yes:
        found = detect(repo, protection=protection)
        print(render_adoption(found, file_plan(repo, found)), end="")
        return 0
    done = adopt(repo, confirm=True, forge=GhCli(), protection=protection)
    where = done.pr.url if done.pr else f"branch {done.branch}"
    print(f"adopted: {where}")
    print("after it merges, run `adlc doctor --baseline` to record which checks already pass")
    return 0


def _trust(args: argparse.Namespace, repo: Path) -> int:
    got = trust_status(repo)
    print(f"doctor baseline:         {got.baseline}")
    print(f"merges approved by hand: {got.merges} of {got.needed}")
    print(f"auto_merge:              {'on' if got.auto_merge else 'off'}")
    if got.auto_merge:
        print("auto-merge is already on")
        return 0
    if not got.ready:
        for reason in got.reasons:
            print(f"not yet: {reason}")
        return 1
    if not args.yes:
        print("earned. Run `adlc trust --yes` to turn auto_merge on.")
        return 0
    enable(repo)
    print("auto_merge is now on in .adlc/config.yaml")
    return 0


def _handoff(args: argparse.Namespace, repo: Path) -> int:
    print(handoff(repo, args.key).relative_to(repo).as_posix())
    return 0


_BOARD_COMMANDS = {
    "add": _add,
    "status": _status,
    "log": _log,
    "refine": _refine,
    "loop-status": _loop_status,
    "run": _run,
    "approve": _approve,
    "retry": _retry,
    "edit": _edit,
    "reject": _reject,
    "report": _report,
    "resume": _resume,
    "reset": _reset,
    "handoff": _handoff,
    "init": _init,
    "trust": _trust,
}


def run_board_command(args: argparse.Namespace) -> int:
    try:
        repo = args.repo or find_repo(Path.cwd())
        return _BOARD_COMMANDS[args.command](args, repo)
    except (CommandError, IllegalTransition) as exc:
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


def run_new(args: argparse.Namespace) -> int:
    code = args.code or default_code(args.name)
    try:
        files = scaffold(args.name, args.stack, code)
        if not args.yes:
            print(render_scaffold(args.name, args.stack, list(files)), end="")
            return 0
        made = create_repo(args.dest / args.name, args.name, args.stack, code)
    except CommandError as exc:
        print(f"adlc: {exc}", file=sys.stderr)
        return 1
    print(f"created {made.path} with {len(made.files)} files and one commit on main")
    print("next: a person creates the remote and protects main, with the wizard:")
    print(f"  cd {made.path} && bash .adlc/wizards/protect-main.sh --dry-run")
    print("then run `adlc doctor --baseline` to record which checks pass")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return run_doctor(args)
    if args.command == "new":
        return run_new(args)
    if args.command in _BOARD_COMMANDS:
        return run_board_command(args)
    return 0
