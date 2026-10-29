"""One git worktree per task, correctly named branches and Conventional Commit messages (FR-7).

A task's work happens in a linked worktree outside the primary checkout, on a branch cut from the
fetched base branch. Nothing here changes the primary checkout's files, and nothing commits to the
base branch.
"""

import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from adlc.commands import CommandError
from adlc.config import Git
from adlc.tasks import KEY_PATTERN

KEYED_TYPES = ("feature", "bugfix", "hotfix")  # the others carry only a slug
MAX_SLUG = 40
MAX_SUBJECT_LINE = 72
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_COMMIT_TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"
_SUBJECT = re.compile(rf"^({_COMMIT_TYPES})(\([a-z0-9][a-z0-9._/-]*\))?!?: \S.*$")


@dataclass(frozen=True)
class Created:
    key: str
    path: Path
    branch: str
    base: str  # the ref the branch was cut from, e.g. origin/main


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str | None  # None when detached
    head: str
    is_primary: bool


@dataclass(frozen=True)
class Removal:
    worktree_removed: bool
    branch_deleted: bool


# --- names and messages (pure) ------------------------------------------------------------------


def slugify(text: str, max_len: int = MAX_SLUG) -> str:
    """Lower-case ASCII words joined by single dashes, cut at a word boundary within max_len."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > max_len:
        cut = slug[:max_len]
        if slug[max_len] != "-" and "-" in cut:
            cut = cut[: cut.rindex("-")]
        slug = cut.strip("-")
    return slug


def branch_name(key: str, type_: str, title: str, git: Git, *, slug: str | None = None) -> str:
    """`<type>/<KEY>-<slug>` for feature, bugfix and hotfix; `<type>/<slug>` for the rest."""
    if type_ not in git.branch_prefixes:
        raise CommandError(
            f"task type {type_!r} is not one of the configured branch types "
            f"({', '.join(git.branch_prefixes)})"
        )
    if not KEY_PATTERN.match(key):
        raise CommandError(f"{key!r} is not a valid task key (expected like ADLC-013)")
    if slug is None:
        slug = slugify(title)
        if not slug:
            raise CommandError(f"cannot make a branch slug from the title {title!r}")
    elif not _SLUG.match(slug):
        raise CommandError(f"{slug!r} is not a valid branch slug (lower case words joined by -)")
    return f"{type_}/{key}-{slug}" if type_ in KEYED_TYPES else f"{type_}/{slug}"


def check_commit_message(message: str) -> str | None:
    """None if the message is a Conventional Commit, else the reason it is not."""
    if not message.strip():
        return "the message is empty"
    first, _, rest = message.partition("\n")
    for prefix in ("fixup!", "squash!", "Merge "):
        if first.startswith(prefix):
            return f"{prefix.strip()} commits are not allowed; write `type(scope): subject`"
    if not _SUBJECT.match(first):
        return (
            f"first line {first!r} does not match `type(scope): subject` "
            f"(types: {_COMMIT_TYPES.replace('|', ', ')})"
        )
    if len(first) > MAX_SUBJECT_LINE:
        return f"first line is {len(first)} characters; the limit is {MAX_SUBJECT_LINE}"
    if rest and rest.split("\n")[0].strip():
        return "leave a blank line between the subject and the body"
    return None


# --- git ----------------------------------------------------------------------------------------


def _git(cwd: Path, *args: str, config=(), check=True, text_in=None):
    argv = ["git", *(a for c in config for a in ("-c", c)), *args]
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=text_in,
        )
    except OSError as exc:
        raise CommandError(f"could not run git: {exc}") from exc
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise CommandError(f"git {' '.join(args)} failed: {detail}")
    return done


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def default_root(repo: Path) -> Path:
    repo = Path(repo).resolve()
    return repo.parent / ".adlc-worktrees" / repo.name


def list_worktrees(repo: Path) -> list[Worktree]:
    """Every worktree of the repo; the first is the primary checkout."""
    out = _git(Path(repo), "worktree", "list", "--porcelain").stdout
    found = []
    for block in out.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, _, value = line.partition(" ")
            fields[name] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch")
        found.append(
            Worktree(
                Path(fields["worktree"]).resolve(),
                branch.removeprefix("refs/heads/") if branch else None,
                fields.get("HEAD", ""),
                is_primary=not found,
            )
        )
    return found


def _branch_exists(repo: Path, branch: str) -> bool:
    return (
        _git(
            repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        ).returncode
        == 0
    )


def create_worktree(
    repo: Path,
    key: str,
    type_: str,
    title: str,
    git: Git,
    *,
    slug: str | None = None,
    root: Path | None = None,
    fetch: bool = True,
) -> Created:
    """Cut a branch for the task from the up-to-date base and check it out in its own worktree."""
    repo = Path(repo).resolve()
    branch = branch_name(key, type_, title, git, slug=slug)
    remotes = _git(repo, "remote").stdout.split()
    base = git.base_branch
    if "origin" in remotes:
        if fetch:
            _git(repo, "fetch", "origin", base)
        base = f"origin/{base}"
    if _git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", check=False).returncode:
        raise CommandError(f"base branch {git.base_branch} was not found ({base})")
    path = (Path(root) if root else default_root(repo)) / key
    if path.exists() or any(_same(path, w.path) for w in list_worktrees(repo)):
        raise CommandError(f"{path} already exists; clean it up or reset the task first")
    if _branch_exists(repo, branch):
        raise CommandError(f"branch {branch} already exists; clean it up or reset the task first")
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(
        repo,
        "worktree",
        "add",
        "--no-track",
        "-b",
        branch,
        str(path),
        base,
        config=("core.longpaths=true",),
    )
    return Created(key, path.resolve(), branch, base)


def commit(worktree: Path, message: str, git: Git) -> str:
    """Commit everything in a task's worktree; the message must be a Conventional Commit."""
    worktree = Path(worktree)
    reason = check_commit_message(message)
    if reason:
        raise CommandError(f"commit message rejected: {reason}")
    git_dir, common = (
        (worktree / line).resolve()
        for line in _git(worktree, "rev-parse", "--git-dir", "--git-common-dir").stdout.split("\n")[
            :2
        ]
    )
    if git_dir == common:
        raise CommandError(f"{worktree} is the primary checkout; commit in a task worktree")
    branch = _git(worktree, "branch", "--show-current").stdout.strip()
    if not branch:
        raise CommandError("the worktree has no branch (detached HEAD); refusing to commit")
    if branch == git.base_branch:
        raise CommandError(f"the worktree is on the base branch {branch}; refusing to commit")
    _git(worktree, "add", "-A")
    if _git(worktree, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise CommandError("nothing to commit")
    _git(worktree, "commit", "-F", "-", text_in=message)
    return _git(worktree, "rev-parse", "HEAD").stdout.strip()


def remove_worktree(
    repo: Path,
    key: str,
    *,
    delete_branch: bool = True,
    force: bool = False,
    root: Path | None = None,
) -> Removal:
    """Remove a task's worktree and, if it is merged (or `force`), its branch. Safe to repeat."""
    repo = Path(repo).resolve()
    path = (Path(root) if root else default_root(repo)) / key
    entry = next((w for w in list_worktrees(repo) if _same(path, w.path)), None)
    if entry is not None and entry.is_primary:
        raise CommandError(f"{path} is the primary checkout and is never removed")
    if entry is None:
        if path.exists():
            raise CommandError(f"{path} exists but is not a worktree of this repo; leaving it")
        return Removal(False, False)
    if path.exists():
        dirty = _git(path, "status", "--porcelain").stdout.strip()
        if dirty and not force:
            raise CommandError(
                f"{path} has uncommitted changes; commit them or pass force to discard them"
            )
        args = ("worktree", "remove", *(("--force",) if force else ()), str(path))
        if _git(repo, *args, check=False).returncode:
            time.sleep(0.5)  # Windows may still hold the folder for a moment
            _git(repo, *args)
    _git(repo, "worktree", "prune")
    deleted = False
    if delete_branch and entry.branch:
        flag = "-D" if force else "-d"
        deleted = _git(repo, "branch", flag, entry.branch, check=False).returncode == 0
    return Removal(True, deleted)
