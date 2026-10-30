"""`adlc init`: adopt an existing repository without damaging it (FR-29, FR-39).

`detect` looks at the repo and picks a stack profile; `propose_config` turns that into a
`.adlc/config.yaml`; `file_plan` lists every file that would be added or changed; `adopt` delivers
exactly that plan as a branch and a pull request. Nothing is written until `adopt` is confirmed, and
it only ever adds `.adlc/`, `docs/work-items/`, a `GLOSSARY.md` if missing and a pointer in
`CLAUDE.md`. It never commits to the base branch.
"""

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from adlc.commands import CommandError
from adlc.config import Git
from adlc.gitwork import commit, create_worktree, remove_worktree
from adlc.pr import Forge, PullRequest

POINTER_MARK = "<!-- adlc:init -->"
WORKTREE_KEY = "ADLC-000"  # names the temporary worktree folder only
BRANCH_SLUG = "adlc-init"
DEFAULT_BOARD = "docs/WORK-ITEMS.md"
BRANCH_TYPES = ("feature", "bugfix", "hotfix", "refactor", "spike", "docs", "test")
_SKIP = {".git", "node_modules", "bin", "obj", ".venv", "venv", "__pycache__"}
_SCRIPT = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.sh")
_PR_TEMPLATES = (
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md",
    "pull_request_template.md",
)
_ADR_DIRS = ("adr", "docs/adr", "docs/decisions", "doc/adr")
_CI_FILES = ("azure-pipelines.yml", "build/azure-pipelines.yml", ".gitlab-ci.yml")
_BOARDS = (DEFAULT_BOARD, "WORK-ITEMS.md")
_POINTER = (
    "## ADLC\n\n"
    "Work items here are carried out by ADLC: its settings are in `.adlc/config.yaml` and the "
    f"task briefs in `docs/work-items/`. {POINTER_MARK}\n"
)
_GLOSSARY = "# Glossary\n\nThe project's own terms, one entry each. Agents use these words.\n"
_IGNORE = (
    "# ADLC keeps its settings in git and its run state out of it.\n*\n!.gitignore\n!config.yaml\n"
)

Protection = Callable[[str], "bool | None"]


@dataclass(frozen=True)
class Detection:
    profile: str
    base_branch: str
    base_protected: bool | None  # None: the code host was not asked, or did not say
    ci: tuple[str, ...]
    board: str | None
    pr_template: str | None
    adr_dir: str | None
    contributing: bool
    claude_md: bool
    glossary: bool
    overrides: dict = field(default_factory=dict)  # what differs from the profile


@dataclass(frozen=True)
class Change:
    path: str
    action: str  # add or change
    content: str
    reason: str


@dataclass(frozen=True)
class Adoption:
    branch: str
    changes: tuple[Change, ...]
    pr: PullRequest | None


# --- looking at the repo ------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _files(repo: Path, depth: int = 3):
    """Names of files up to `depth` folders down, skipping build output and dependencies."""
    base = len(repo.parts)
    for folder, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        if len(Path(folder).parts) - base >= depth:
            dirs[:] = []
        yield from names


def _stack(repo: Path) -> str:
    names = set(_files(repo))
    if any(n.endswith((".sln", ".csproj")) for n in names):
        return "dotnet"
    markers = {"pyproject.toml", "setup.py", "setup.cfg"}
    if markers & names or any(n.startswith("requirements") and n.endswith(".txt") for n in names):
        return "python"
    return "generic"


def _base_branch(repo: Path) -> str:
    remote = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote:
        return remote.removeprefix("origin/")
    for name in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    return _git(repo, "branch", "--show-current") or "main"


def _profile_dod(profile: str) -> dict[str, str]:
    text = (resources.files("adlc") / "profiles" / f"{profile}.yaml").read_text(encoding="utf-8")
    return (yaml.safe_load(text) or {}).get("dod") or {}


def missing_scripts(profile: str, exists: Callable[[str], bool]) -> dict[str, None]:
    """Profile commands that run a script `exists` denies, to switch off."""
    gone = {}
    for name, command in _profile_dod(profile).items():
        scripts = _SCRIPT.findall(command)
        if scripts and not all(exists(s) for s in scripts):
            gone[name] = None
    return gone


def _missing_scripts(repo: Path, profile: str) -> dict[str, None]:
    return missing_scripts(profile, lambda script: (repo / script).is_file())


def _npm_commands(repo: Path) -> dict[str, str]:
    try:
        scripts = json.loads((repo / "package.json").read_text(encoding="utf-8")).get("scripts")
    except (OSError, ValueError, AttributeError):
        return {}
    scripts = scripts if isinstance(scripts, dict) else {}
    found = {}
    if "test" in scripts:
        found["test"] = "npm test"
    if "lint" in scripts:
        found["lint"] = "npm run lint"
    return found


def _first(repo: Path, candidates) -> str | None:
    return next((c for c in candidates if (repo / c).exists()), None)


def detect(repo: Path, *, protection: Protection | None = None) -> Detection:
    """What the repository already has, and the config that would fit it."""
    repo = Path(repo)
    profile = _stack(repo)
    base = _base_branch(repo)
    board = _first(repo, _BOARDS)
    workflows = repo / ".github" / "workflows"
    ci = (
        sorted(f".github/workflows/{p.name}" for p in workflows.glob("*.y*ml") if p.is_file())
        if workflows.is_dir()
        else []
    )
    ci += [c for c in _CI_FILES if (repo / c).is_file()]

    overrides: dict = {}
    dod = _missing_scripts(repo, profile)
    if profile == "generic":
        dod.update(_npm_commands(repo))
    if dod:
        overrides["dod"] = dod
    if base != "main":
        overrides["git"] = {"base_branch": base}
    if board and board != DEFAULT_BOARD:
        overrides["board"] = {"path": board}
    return Detection(
        profile=profile,
        base_branch=base,
        base_protected=protection(base) if protection else None,
        ci=tuple(ci),
        board=board,
        pr_template=_first(repo, _PR_TEMPLATES),
        adr_dir=_first(repo, _ADR_DIRS),
        contributing=(repo / "CONTRIBUTING.md").is_file(),
        claude_md=(repo / "CLAUDE.md").is_file(),
        glossary=(repo / "GLOSSARY.md").is_file(),
        overrides=overrides,
    )


def propose_config(found: Detection) -> str:
    """The `.adlc/config.yaml` that extends the chosen profile and overrides what differs."""
    body = yaml.safe_dump({"extends": found.profile, **found.overrides}, sort_keys=False)
    return "# Proposed by `adlc init`; edit freely.\n" + body


def gh_protection(repo: Path, run=subprocess.run) -> Protection:
    """Ask the GitHub CLI whether a branch is protected; None when it cannot say."""

    def ask(branch: str) -> bool | None:
        try:
            done = run(
                ["gh", "api", f"repos/{{owner}}/{{repo}}/branches/{branch}/protection"],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        except OSError:
            return None  # no gh on this machine
        if done.returncode == 0:
            return True
        return False if "not protected" in (done.stderr or "").lower() else None

    return ask


# --- the plan -----------------------------------------------------------------------------------


def _append(existing: bytes) -> str:
    text = existing.decode("utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    gap = "" if text.endswith(("\n", "\r\n")) or not text else eol
    return text + gap + eol + _POINTER.replace("\n", eol)


def file_plan(repo: Path, found: Detection | None = None) -> list[Change]:
    """Every file `adopt` would add or change. Existing files are never replaced."""
    repo = Path(repo)
    found = found or detect(repo)
    plan: list[Change] = []
    if not (repo / ".adlc" / "config.yaml").exists():
        plan.append(Change(".adlc/config.yaml", "add", propose_config(found), "settings"))
    if not (repo / ".adlc" / ".gitignore").exists():
        plan.append(Change(".adlc/.gitignore", "add", _IGNORE, "keep run state out of git"))
    if not (repo / "docs" / "work-items").exists():
        plan.append(Change("docs/work-items/.gitkeep", "add", "", "folder for task briefs"))
    if not found.glossary:
        plan.append(Change("GLOSSARY.md", "add", _GLOSSARY, "shared terms (was missing)"))
    claude = repo / "CLAUDE.md"
    if not claude.exists():
        plan.append(Change("CLAUDE.md", "add", _POINTER, "points agents at ADLC"))
    elif POINTER_MARK not in claude.read_text(encoding="utf-8"):
        content = _append(claude.read_bytes())
        plan.append(Change("CLAUDE.md", "change", content, "pointer appended; nothing removed"))
    return plan


def render_adoption(found: Detection, plan: list[Change]) -> str:
    """What was found and what would be written, for the person to confirm."""
    protected = {True: "protected", False: "NOT protected", None: "protection unknown"}
    rows = [
        f"Stack profile:    {found.profile}",
        f"Base branch:      {found.base_branch} ({protected[found.base_protected]})",
        f"CI:               {', '.join(found.ci) or 'none found'}",
        f"Board:            {found.board or 'none found'}",
        f"PR template:      {found.pr_template or 'none found'}",
        f"ADR folder:       {found.adr_dir or 'none found'}",
        f"CONTRIBUTING.md:  {'found' if found.contributing else 'none found'}",
        f"CLAUDE.md:        {'found' if found.claude_md else 'none found'}",
        f"Overrides:        {found.overrides or 'none: the profile fits as it is'}",
        "",
        "Files to add or change:",
    ]
    rows += [f"  {c.action:<7}{c.path}  ({c.reason})" for c in plan] or ["  (none)"]
    rows += [
        "",
        "This is a dry run: nothing is written. Run again with --yes to deliver it as a PR.",
    ]
    return "\n".join(rows) + "\n"


# --- delivering it ------------------------------------------------------------------------------


def _write(root: Path, change: Change) -> None:
    target = root / change.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(change.content.encode("utf-8"))


def _body(plan: list[Change]) -> str:
    files = "\n".join(f"- `{c.path}` ({c.action})" for c in plan)
    return (
        "## Summary\nAdopt this repository into ADLC. Only additive files; nothing existing is "
        f"rewritten.\n\n{files}\n\n## Next\nAfter merging, run `adlc doctor --baseline` to record "
        "which checks already pass.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    )


def adopt(
    repo: Path,
    *,
    confirm: bool,
    forge: Forge | None = None,
    remote: bool = True,
    root: Path | None = None,
    fetch: bool = True,
    protection: Protection | None = None,
) -> Adoption:
    """Commit the plan on a `docs/adlc-init` branch and open a PR; never touch the base branch."""
    repo = Path(repo)
    if not confirm:
        raise CommandError("adlc init writes nothing until you confirm; pass --yes")
    found = detect(repo, protection=protection)
    plan = file_plan(repo, found)
    if not plan:
        raise CommandError("nothing to add: this repository already has everything adlc init adds")
    git = Git(found.base_branch, BRANCH_TYPES, "conventional")
    made = create_worktree(
        repo, WORKTREE_KEY, "docs", "adlc init", git, slug=BRANCH_SLUG, root=root, fetch=fetch
    )
    pr = None
    try:
        for change in plan:
            _write(made.path, change)
        commit(made.path, "chore(adlc): adopt ADLC in this repository", git)
        if remote and forge is not None:
            forge.push(made.path, made.branch)
            pr = forge.create_pr(
                made.path,
                base=found.base_branch,
                branch=made.branch,
                title="chore(adlc): adopt ADLC in this repository",
                body=_body(plan),
            )
    finally:
        remove_worktree(repo, WORKTREE_KEY, delete_branch=False, force=True, root=made.path.parent)
    return Adoption(made.branch, tuple(plan), pr)
