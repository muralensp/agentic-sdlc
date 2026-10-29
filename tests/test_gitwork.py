"""Branch names, commit messages and one worktree per task (ADLC-013)."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from adlc.commands import CommandError
from adlc.config import Git
from adlc.gitwork import (
    branch_name,
    check_commit_message,
    commit,
    create_worktree,
    list_worktrees,
    remove_worktree,
    slugify,
)

PREFIXES = ("feature", "bugfix", "hotfix", "refactor", "spike", "docs", "test")
GIT = Git("main", PREFIXES, "conventional")


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def g(cwd: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if check:
        assert done.returncode == 0, done.stderr
    return done.stdout.strip()


# --- branch names -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "type_, expected",
    [
        ("feature", "feature/SHIP-001-shipment-creation"),
        ("bugfix", "bugfix/SHIP-001-shipment-creation"),
        ("hotfix", "hotfix/SHIP-001-shipment-creation"),
        ("refactor", "refactor/shipment-creation"),
        ("spike", "spike/shipment-creation"),
        ("docs", "docs/shipment-creation"),
        ("test", "test/shipment-creation"),
    ],
)
def test_branch_names_follow_the_boss_convention(type_, expected):
    assert branch_name("SHIP-001", type_, "Shipment creation", GIT) == expected


def test_an_explicit_slug_replaces_the_one_from_the_title():
    assert branch_name("ADLC-013", "feature", "Anything", GIT, slug="worktrees-and-commits") == (
        "feature/ADLC-013-worktrees-and-commits"
    )


@pytest.mark.parametrize("bad", ["Has Spaces", "UPPER", "-lead", "trail-", "a--b", "a_b", ""])
def test_a_bad_explicit_slug_is_refused(bad):
    with pytest.raises(CommandError, match="slug"):
        branch_name("ADLC-013", "feature", "t", GIT, slug=bad)


def test_slugify_lowercases_and_replaces_everything_else_with_one_dash():
    assert slugify("Git worktree handling, branch naming & Conventional Commits!") == (
        "git-worktree-handling-branch-naming"
    )
    assert slugify("  Ünïcode — dashes  ") == "unicode-dashes"
    assert slugify("a   b___c") == "a-b-c"


def test_slugify_cuts_at_a_word_boundary_within_the_limit():
    slug = slugify("alpha bravo charlie delta echo foxtrot golf hotel india", 40)
    assert len(slug) <= 40 and slug == "alpha-bravo-charlie-delta-echo-foxtrot"
    assert slugify("x" * 60, 40) == "x" * 40


def test_a_title_with_no_usable_characters_is_refused():
    with pytest.raises(CommandError, match="slug"):
        branch_name("ADLC-013", "feature", "!!! ???", GIT)


def test_a_type_outside_the_configured_prefixes_is_refused():
    narrow = Git("main", ("feature",), "conventional")
    with pytest.raises(CommandError, match="type"):
        branch_name("ADLC-013", "docs", "t", narrow)
    with pytest.raises(CommandError, match="type"):
        branch_name("ADLC-013", "chore", "t", GIT)


@pytest.mark.parametrize("key", ["adlc-013", "ADLC013", "ADLC-1", "ADLC-013-x", ""])
def test_a_bad_key_is_refused(key):
    with pytest.raises(CommandError, match="key"):
        branch_name(key, "feature", "t", GIT)


def test_slug_only_types_ignore_the_key_but_still_check_it():
    assert branch_name("ADLC-013", "docs", "Fix the readme", GIT) == "docs/fix-the-readme"
    with pytest.raises(CommandError, match="key"):
        branch_name("nope", "docs", "t", GIT)


# --- commit messages ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "feat(runner): add the agent runner",
        "fix(declaration): handle ICEGATE timeout",
        "feat: no scope is fine",
        "feat(api)!: breaking change",
        "refactor(rule-engine): simplify evaluator",
        "docs(prd): update chapter 7",
        "test(duty): add GST calculation tests",
        "perf(search): optimize HS lookup",
        "build(ci): add code coverage",
        "chore(deps): upgrade EF Core",
        "ci(actions): pin versions",
        "style(format): wrap lines",
        "revert(runner): undo it",
        "feat(runner): subject\n\nA body.\n\nCo-Authored-By: Someone <a@b.c>",
    ],
)
def test_good_commit_messages_pass(message):
    assert check_commit_message(message) is None


@pytest.mark.parametrize(
    "message",
    [
        "",
        "   ",
        "added some stuff",
        "Feat(runner): capital type",
        "feature(runner): wrong type word",
        "feat(runner) add missing colon",
        "feat(runner):no space",
        "feat(runner):  ",
        "feat(): empty scope",
        "feat(Bad Scope): spaces",
        "feat(runner): " + "x" * 80,
        "fixup! feat(runner): add it",
        "squash! feat(runner): add it",
        "Merge branch 'main' into feature/x",
        "\nfeat(runner): leading blank line",
    ],
)
def test_bad_commit_messages_are_rejected_with_a_reason(message):
    reason = check_commit_message(message)
    assert reason and isinstance(reason, str)


# --- a throwaway repo with an origin ------------------------------------------------------------


class World:
    def __init__(self, tmp_path: Path):
        self.origin = tmp_path / "origin.git"
        self.repo = tmp_path / "repo"
        g(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        g(tmp_path, "init", "-b", "main", str(self.repo))
        (self.repo / "a.txt").write_text("one\n")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "b.txt").write_text("two\n")
        g(self.repo, "add", "-A")
        g(self.repo, "commit", "-m", "chore: first")
        g(self.repo, "remote", "add", "origin", str(self.origin))
        g(self.repo, "push", "-u", "origin", "main")
        self.tmp = tmp_path

    def other_pushes(self, name: str = "later.txt") -> str:
        """Another clone adds a commit to origin; returns its id."""
        other = self.tmp / "other"
        if not other.exists():
            g(self.tmp, "clone", str(self.origin), str(other))
        (other / name).write_text("new\n")
        g(other, "add", "-A")
        g(other, "commit", "-m", "chore: more")
        g(other, "push", "origin", "main")
        return g(other, "rev-parse", "HEAD")

    def snapshot(self) -> dict:
        """Everything about the primary checkout's code that must not change."""
        files = {}
        for path in sorted(self.repo.rglob("*")):
            if ".git" in path.parts or not path.is_file():
                continue
            files[path.relative_to(self.repo).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        return {
            "files": files,
            "status": g(self.repo, "status", "--porcelain"),
            "head": g(self.repo, "rev-parse", "HEAD"),
            "branch": g(self.repo, "branch", "--show-current"),
            "main": g(self.repo, "rev-parse", "main"),
        }

    def worktree(self, key="ADLC-013", type_="feature", title="Worktrees", **kwargs):
        return create_worktree(self.repo, key, type_, title, GIT, **kwargs)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# --- creating a worktree ------------------------------------------------------------------------


def test_a_worktree_is_made_on_the_named_branch_outside_the_repo(world):
    made = world.worktree()
    assert made.branch == "feature/ADLC-013-worktrees"
    assert made.path == world.tmp / ".adlc-worktrees" / "repo" / "ADLC-013"
    assert made.path.is_dir() and (made.path / "a.txt").is_file()
    assert g(made.path, "branch", "--show-current") == made.branch
    assert world.repo not in made.path.parents


def test_the_root_can_be_chosen(world, tmp_path):
    root = tmp_path / "elsewhere"
    made = world.worktree(root=root)
    assert made.path == root / "ADLC-013"


def test_it_branches_from_the_fetched_origin_not_from_a_stale_local_main(world):
    later = world.other_pushes()
    made = world.worktree()
    assert g(made.path, "rev-parse", "HEAD") == later
    assert (made.path / "later.txt").is_file()
    assert made.base == "origin/main"


def test_local_main_and_the_primary_checkout_are_left_alone(world):
    world.other_pushes()
    before = world.snapshot()
    made = world.worktree()
    assert world.snapshot() == before
    assert not (world.repo / "later.txt").exists()
    assert made.path.is_dir()


def test_the_new_branch_does_not_track_the_base_branch(world):
    made = world.worktree()
    assert g(made.path, "config", "--get", f"branch.{made.branch}.remote", check=False) == ""


def test_without_an_origin_the_local_base_is_used(tmp_path):
    repo = tmp_path / "solo"
    g(tmp_path, "init", "-b", "main", str(repo))
    (repo / "a.txt").write_text("x\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-m", "chore: first")
    made = create_worktree(repo, "ADLC-013", "docs", "Notes", GIT)
    assert made.base == "main" and made.branch == "docs/notes"
    assert g(made.path, "rev-parse", "HEAD") == g(repo, "rev-parse", "main")


def test_a_failing_fetch_stops_the_task_instead_of_using_a_stale_base(world):
    g(world.repo, "remote", "set-url", "origin", str(world.tmp / "gone.git"))
    with pytest.raises(CommandError, match="fetch"):
        world.worktree()
    assert not (world.tmp / ".adlc-worktrees").exists()


def test_an_existing_branch_is_refused(world):
    g(world.repo, "branch", "feature/ADLC-013-worktrees")
    with pytest.raises(CommandError, match="reset the task"):
        world.worktree()


def test_an_existing_worktree_for_the_key_is_refused(world):
    world.worktree()
    with pytest.raises(CommandError, match="reset the task"):
        world.worktree(title="Another title")


def test_an_existing_folder_is_refused_and_left_alone(world):
    folder = world.tmp / ".adlc-worktrees" / "repo" / "ADLC-013"
    folder.mkdir(parents=True)
    (folder / "mine.txt").write_text("keep")
    with pytest.raises(CommandError, match="reset the task"):
        world.worktree()
    assert (folder / "mine.txt").read_text() == "keep"


def test_an_empty_existing_folder_is_refused_too(world):
    folder = world.tmp / ".adlc-worktrees" / "repo" / "ADLC-013"
    folder.mkdir(parents=True)
    with pytest.raises(CommandError, match="reset the task"):
        world.worktree()


def test_a_missing_base_branch_is_an_error(world):
    nobase = Git("develop", GIT.branch_prefixes, "conventional")
    with pytest.raises(CommandError, match="develop"):
        create_worktree(world.repo, "ADLC-013", "feature", "t", nobase)


def test_creating_a_worktree_in_a_folder_that_is_not_a_repo_is_an_error(tmp_path):
    with pytest.raises(CommandError, match="git"):
        create_worktree(tmp_path, "ADLC-013", "feature", "t", GIT)


def test_long_paths_are_enabled_for_the_worktree_checkout(world):
    made = world.worktree()
    assert g(made.path, "config", "--get", "core.longpaths", check=False) in ("true", "")


# --- committing ---------------------------------------------------------------------------------


def test_a_good_commit_lands_on_the_task_branch_only(world):
    made = world.worktree()
    before = world.snapshot()
    (made.path / "new.py").write_text("x = 1\n")
    sha = commit(made.path, "feat(runner): add new.py\n\nCo-Authored-By: A <a@b.c>", GIT)
    assert g(made.path, "rev-parse", "HEAD") == sha
    assert g(made.path, "log", "-1", "--format=%s") == "feat(runner): add new.py"
    assert "new.py" in g(made.path, "show", "--name-only", "--format=", "HEAD")
    assert world.snapshot() == before


def test_a_bad_message_is_rejected_and_nothing_is_committed(world):
    made = world.worktree()
    (made.path / "new.py").write_text("x = 1\n")
    head = g(made.path, "rev-parse", "HEAD")
    with pytest.raises(CommandError, match="commit message"):
        commit(made.path, "added new file", GIT)
    assert g(made.path, "rev-parse", "HEAD") == head
    assert "new.py" in g(made.path, "status", "--porcelain")


def test_all_changes_are_committed_including_new_and_deleted_files(world):
    made = world.worktree()
    (made.path / "new.py").write_text("x = 1\n")
    (made.path / "a.txt").unlink()
    (made.path / "src" / "b.txt").write_text("changed\n")
    commit(made.path, "feat(runner): several changes", GIT)
    assert g(made.path, "status", "--porcelain") == ""


def test_committing_in_the_primary_checkout_is_refused(world):
    (world.repo / "oops.py").write_text("x\n")
    head = g(world.repo, "rev-parse", "HEAD")
    with pytest.raises(CommandError, match="primary"):
        commit(world.repo, "feat(runner): oops", GIT)
    assert g(world.repo, "rev-parse", "HEAD") == head


def test_a_detached_head_is_refused(world):
    made = world.worktree()
    g(made.path, "checkout", "--detach")
    (made.path / "x.py").write_text("x\n")
    with pytest.raises(CommandError, match="branch"):
        commit(made.path, "feat(runner): detached", GIT)


def test_a_worktree_on_the_base_branch_is_refused(world):
    made = world.worktree()
    (made.path / "x.py").write_text("x\n")
    as_base = Git(made.branch, GIT.branch_prefixes, "conventional")
    with pytest.raises(CommandError, match="base branch"):
        commit(made.path, "feat(runner): on base", as_base)


def test_nothing_to_commit_is_an_error(world):
    made = world.worktree()
    with pytest.raises(CommandError, match="^nothing to commit$"):
        commit(made.path, "feat(runner): empty", GIT)


# --- listing and cleaning up --------------------------------------------------------------------


def test_listing_shows_the_primary_and_each_task(world):
    made = world.worktree()
    found = list_worktrees(world.repo)
    assert [w.is_primary for w in found] == [True, False]
    assert found[0].path == world.repo.resolve() and found[0].branch == "main"
    assert found[1].path == made.path.resolve() and found[1].branch == made.branch
    assert len(found[1].head) == 40


def test_a_clean_worktree_is_removed_with_its_merged_branch(world):
    made = world.worktree()
    done = remove_worktree(world.repo, "ADLC-013")
    assert (done.worktree_removed, done.branch_deleted) == (True, True)
    assert not made.path.exists()
    assert [w.is_primary for w in list_worktrees(world.repo)] == [True]
    assert g(world.repo, "branch", "--list", made.branch) == ""
    assert g(world.repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_uncommitted_work_blocks_cleanup_unless_forced(world):
    made = world.worktree()
    (made.path / "wip.py").write_text("x\n")
    with pytest.raises(CommandError, match="pass force"):
        remove_worktree(world.repo, "ADLC-013")
    assert (made.path / "wip.py").is_file()
    done = remove_worktree(world.repo, "ADLC-013", force=True)
    assert done.worktree_removed and not made.path.exists()


def test_a_branch_with_unmerged_commits_is_kept_unless_forced(world):
    made = world.worktree()
    (made.path / "new.py").write_text("x\n")
    commit(made.path, "feat(runner): work", GIT)
    done = remove_worktree(world.repo, "ADLC-013")
    assert (done.worktree_removed, done.branch_deleted) == (True, False)
    assert not made.path.exists()
    assert g(world.repo, "branch", "--list", made.branch) != ""


def test_force_deletes_an_unmerged_branch(world):
    made = world.worktree()
    (made.path / "new.py").write_text("x\n")
    commit(made.path, "feat(runner): work", GIT)
    done = remove_worktree(world.repo, "ADLC-013", force=True)
    assert (done.worktree_removed, done.branch_deleted) == (True, True)
    assert g(world.repo, "branch", "--list", made.branch) == ""


def test_the_branch_can_be_kept_on_request(world):
    made = world.worktree()
    done = remove_worktree(world.repo, "ADLC-013", delete_branch=False)
    assert (done.worktree_removed, done.branch_deleted) == (True, False)
    assert g(world.repo, "branch", "--list", made.branch) != ""


def test_removing_something_already_gone_is_not_an_error(world):
    done = remove_worktree(world.repo, "ADLC-099")
    assert (done.worktree_removed, done.branch_deleted) == (False, False)
    world.worktree()
    remove_worktree(world.repo, "ADLC-013")
    again = remove_worktree(world.repo, "ADLC-013")
    assert (again.worktree_removed, again.branch_deleted) == (False, False)


def test_a_folder_deleted_by_hand_is_cleaned_from_git_too(world):
    import shutil

    made = world.worktree()
    shutil.rmtree(made.path)
    done = remove_worktree(world.repo, "ADLC-013")
    assert done.worktree_removed
    assert [w.is_primary for w in list_worktrees(world.repo)] == [True]


def test_cleanup_leaves_the_primary_checkout_untouched(world):
    before = world.snapshot()
    world.worktree()
    remove_worktree(world.repo, "ADLC-013")
    assert world.snapshot() == before


def test_the_primary_checkout_is_never_removed(world):
    with pytest.raises(CommandError, match="never removed"):
        remove_worktree(world.repo, "repo", root=world.repo.parent)
    assert world.repo.is_dir()


def test_a_task_that_ran_end_to_end_leaves_nothing_behind(world):
    made = world.worktree("ADLC-021", "docs", "Write the guide")
    (made.path / "guide.md").write_text("hi\n")
    commit(made.path, "docs(guide): add it", GIT)
    remove_worktree(world.repo, "ADLC-021", force=True)
    assert not made.path.exists()
    assert g(world.repo, "branch", "--list", "docs/*") == ""
    assert [w.is_primary for w in list_worktrees(world.repo)] == [True]


def test_a_plain_folder_at_the_worktree_path_is_not_removed(world):
    folder = world.tmp / ".adlc-worktrees" / "repo" / "ADLC-013"
    folder.mkdir(parents=True)
    (folder / "mine.txt").write_text("keep")
    with pytest.raises(CommandError, match="not a worktree"):
        remove_worktree(world.repo, "ADLC-013")
    assert (folder / "mine.txt").read_text() == "keep"


def test_a_body_must_follow_a_blank_line():
    assert check_commit_message("feat(x): a" + chr(10) + "body right away") is not None
    assert check_commit_message("feat(x): a" + chr(10) * 2 + "body") is None


def lose_the_registration_but_keep_the_files(monkeypatch, repo: Path):
    """Like Windows when a build server holds files: git forgets the worktree, the folder stays."""
    import subprocess as sp

    from adlc import gitwork

    real = gitwork._git
    real_rmtree = shutil.rmtree

    def flaky(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            real_rmtree(repo / ".git" / "worktrees", ignore_errors=True)
            return sp.CompletedProcess(args, 1, "", "Permission denied")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(gitwork, "_git", flaky)
    monkeypatch.setattr(gitwork.time, "sleep", lambda _: None)


def test_files_git_could_not_delete_are_deleted_after_a_wait(world, monkeypatch):
    made = world.worktree()
    (made.path / "obj").mkdir()
    (made.path / "obj" / "locked.dll").write_text("x")
    lose_the_registration_but_keep_the_files(monkeypatch, world.repo)
    done = remove_worktree(world.repo, "ADLC-013", force=True)
    assert (done.worktree_removed, done.branch_deleted) == (True, True)
    assert not made.path.exists()
    assert g(world.repo, "branch", "--list", made.branch) == ""


def test_a_folder_that_stays_locked_is_reported_and_the_branch_is_kept(world, monkeypatch):
    from adlc import gitwork

    made = world.worktree()
    lose_the_registration_but_keep_the_files(monkeypatch, world.repo)

    def locked(path, onexc=None):
        raise PermissionError("in use")

    monkeypatch.setattr(gitwork.shutil, "rmtree", locked)
    with pytest.raises(CommandError, match="could not be fully deleted"):
        remove_worktree(world.repo, "ADLC-013", force=True)
    assert g(world.repo, "branch", "--list", made.branch) != ""


def test_a_failing_remove_of_a_registered_worktree_reports_gits_reason(world, monkeypatch):
    import subprocess as sp

    from adlc import gitwork

    world.worktree()
    real = gitwork._git

    def failing(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            if kwargs.get("check", True):
                raise CommandError("git worktree remove failed: it is locked")
            return sp.CompletedProcess(args, 1, "", "locked")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(gitwork, "_git", failing)
    monkeypatch.setattr(gitwork.time, "sleep", lambda _: None)
    with pytest.raises(CommandError, match="it is locked"):
        remove_worktree(world.repo, "ADLC-013", force=True)
