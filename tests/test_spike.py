"""Spike tasks: `research` writes a sourced note, `prototype` records an answer and is deleted
(ADLC-023, FR-35)."""

import subprocess
from pathlib import Path

import pytest

from adlc.board import Board
from adlc.commands import CommandError
from adlc.config import load_config
from adlc.fake_runner import FakeRunner, error, ok
from adlc.spike import (
    SpikeError,
    check_research,
    kind_of,
    run_spike,
    topic_of,
)
from adlc.tasks import load_tasks, parse_brief, select
from test_commands import brief_text, make_repo

NOTE = "# Topic\n\nFindings.\n\n## Sources\n- https://example.com/doc\n"


def g(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.com")


def spike_brief(kind="research", topic="queue-options", key="S-001", extra=""):
    text = brief_text(key).replace("type: feature", "type: spike")
    fields = f"spike: {kind}\n" + (f"topic: {topic}\n" if topic else "") + extra
    return text.replace("adr: none\n", f"adr: none\n{fields}", 1)


class Scene:
    def __init__(self, tmp_path, kind, topic="queue-options", extra=""):
        self.tmp = tmp_path
        self.repo = make_repo(
            tmp_path, [("S-001", "IN_PROGRESS")], {"S-001": spike_brief(kind, topic, extra=extra)}
        )
        (self.repo / ".gitignore").write_text(".adlc/\n")
        g(self.repo.parent, "init", "-q", "-b", "main", str(self.repo))
        g(self.repo, "add", "-A")
        g(self.repo, "commit", "-q", "-m", "init")
        self.settings = load_config(self.repo)

    def state(self) -> str:
        row = next(
            r for r in Board(self.repo / "docs" / "WORK-ITEMS.md").rows() if r.key == "S-001"
        )
        return row.cells["Status"]

    def notes(self) -> str:
        row = next(
            r for r in Board(self.repo / "docs" / "WORK-ITEMS.md").rows() if r.key == "S-001"
        )
        return row.cells["Notes"]

    def go(self, *steps):
        runner = FakeRunner(list(steps))
        done = run_spike(
            self.repo,
            "S-001",
            self.settings,
            runner,
            root=self.tmp / "wt",
            fetch=False,
            skills_root=self.tmp / "no-skills",
        )
        return done, runner

    def branches(self) -> list[str]:
        return g(self.repo, "branch", "--format=%(refname:short)").split()


def writes(path: str, text: str):
    """A runner step that writes `path` inside the agent's working folder."""

    def step(request):
        target = Path(request.cwd) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return ok("done")

    return step


def answers(text: str, *, also: str | None = None):
    def step(request):
        if also:
            (Path(request.cwd) / also).write_text("scratch")
        return ok(text)

    return step


# --- reading the brief ------------------------------------------------------------------------


def parsed(text):
    return parse_brief(text, "brief.md")


def test_kind_comes_from_the_brief():
    assert kind_of(parsed(spike_brief("research"))) == "research"
    assert kind_of(parsed(spike_brief("prototype"))) == "prototype"


def test_only_a_spike_brief_has_a_kind():
    with pytest.raises(SpikeError, match="not a spike"):
        kind_of(parsed(brief_text("S-001")))


@pytest.mark.parametrize("kind", ["", "explore"])
def test_a_spike_needs_a_known_kind(kind):
    text = spike_brief("research").replace("spike: research\n", f"spike: {kind}\n" if kind else "")
    with pytest.raises(SpikeError, match="research or prototype"):
        kind_of(parsed(text))


def test_topic_is_given_or_taken_from_the_title():
    assert topic_of(parsed(spike_brief(topic="queue-options")), "Anything") == "queue-options"
    assert topic_of(parsed(spike_brief(topic="")), "Which Queue to Use?") == "which-queue-to-use"


@pytest.mark.parametrize("topic", ["../escape", "a/b", "Has Space", "UPPER"])
def test_a_topic_cannot_leave_the_research_folder(topic):
    with pytest.raises(SpikeError, match="topic"):
        topic_of(parsed(spike_brief(topic=topic)), "x")


# --- the research rules (pure) ----------------------------------------------------------------


def test_research_may_change_only_its_own_note(tmp_path):
    settings = load_config(make_repo(tmp_path, [("S-001", "IN_PROGRESS")]))
    mine = "docs/research/queue-options.md"
    assert check_research({mine: "A"}, "queue-options", NOTE, settings) == []
    problems = check_research({mine: "A", "src/app.py": "A"}, "queue-options", NOTE, settings)
    assert problems and "src/app.py" in problems[0]
    other = check_research({"docs/research/other.md": "A"}, "queue-options", NOTE, settings)
    assert other and "other.md" in other[0]


def test_research_needs_a_note_with_sources(tmp_path):
    settings = load_config(make_repo(tmp_path, [("S-001", "IN_PROGRESS")]))
    mine = {"docs/research/queue-options.md": "A"}
    assert check_research({}, "queue-options", NOTE, settings)  # nothing written
    no_heading = check_research(mine, "queue-options", "# T\n\nhttps://example.com\n", settings)
    assert any("Sources" in p for p in no_heading)
    no_link = check_research(mine, "queue-options", "# T\n\n## Sources\n- my memory\n", settings)
    assert any("link" in p for p in no_link)


# --- research, end to end ---------------------------------------------------------------------


def test_research_commits_a_sourced_note_and_nothing_else(tmp_path):
    scene = Scene(tmp_path, "research")
    done, runner = scene.go(writes("docs/research/queue-options.md", NOTE))
    assert done.status == "ready"
    assert done.branch == "spike/queue-options"
    assert g(scene.repo, "diff", "--name-only", "main", done.branch) == (
        "docs/research/queue-options.md"
    )
    assert scene.state() == "IN_REVIEW"
    assert (scene.tmp / "wt" / "S-001").exists()  # kept: the PR step merges the note
    assert runner.requests[0].cwd == scene.tmp / "wt" / "S-001"


def test_research_that_touches_code_is_rejected_and_not_committed(tmp_path):
    scene = Scene(tmp_path, "research")

    def sneaky(request):
        writes("docs/research/queue-options.md", NOTE)(request)
        writes("src/app.py", "print(1)")(request)
        return ok("done")

    done, _ = scene.go(sneaky)
    assert done.status == "rejected"
    assert any("src/app.py" in p for p in done.problems)
    assert scene.state() == "IN_PROGRESS"
    assert g(scene.repo, "rev-list", "--count", "main..spike/queue-options") == "0"


def test_research_without_sources_is_rejected(tmp_path):
    scene = Scene(tmp_path, "research")
    done, _ = scene.go(writes("docs/research/queue-options.md", "# T\n\nJust my opinion.\n"))
    assert done.status == "rejected"
    assert scene.state() == "IN_PROGRESS"


def test_a_failed_agent_run_changes_nothing_on_the_board(tmp_path):
    scene = Scene(tmp_path, "research")
    done, _ = scene.go(error("boom"))
    assert done.status == "agent_failed"
    assert "boom" in done.detail
    assert scene.state() == "IN_PROGRESS"


# --- prototype, end to end --------------------------------------------------------------------


def test_prototype_records_the_answer_and_deletes_the_branch(tmp_path):
    scene = Scene(tmp_path, "prototype", topic="state-model")
    before = g(scene.repo, "rev-parse", "main")
    done, runner = scene.go(answers("Use a plain dict.\nIt is enough.", also="scratch.py"))
    assert done.status == "done"
    assert scene.state() == "DONE"
    record = scene.repo / ".adlc" / "spikes" / "S-001.md"
    assert "Use a plain dict." in record.read_text()
    assert "Use a plain dict." in scene.notes()
    assert not (scene.tmp / "wt" / "S-001").exists()
    assert "spike/state-model" not in scene.branches()
    assert g(scene.repo, "rev-parse", "main") == before  # never merged, nothing on main
    assert runner.requests[0].cwd == scene.tmp / "wt" / "S-001"


def test_prototype_without_an_answer_keeps_the_work(tmp_path):
    scene = Scene(tmp_path, "prototype", topic="state-model")
    done, _ = scene.go(answers("   "))
    assert done.status == "rejected"
    assert scene.state() == "IN_PROGRESS"
    assert (scene.tmp / "wt" / "S-001").exists()
    assert "spike/state-model" in scene.branches()


def test_the_recorded_answer_has_secrets_removed(tmp_path):
    scene = Scene(tmp_path, "prototype", topic="state-model")
    scene.go(answers("Works. Use token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 to call it."))
    record = (scene.repo / ".adlc" / "spikes" / "S-001.md").read_text()
    assert "ghp_abcdef" not in record
    assert "[REDACTED]" in record


def test_a_prototype_with_a_pull_request_is_never_deleted(tmp_path):
    scene = Scene(tmp_path, "prototype", topic="state-model")
    (scene.repo / ".adlc" / "prs").mkdir(parents=True)
    (scene.repo / ".adlc" / "prs" / "S-001.json").write_text("{}")
    with pytest.raises(CommandError, match="pull request"):
        scene.go(answers("An answer."))
    assert scene.state() == "IN_PROGRESS"


def test_only_an_in_progress_spike_runs(tmp_path):
    scene = Scene(tmp_path, "research")
    Board(scene.repo / "docs" / "WORK-ITEMS.md").set_cell("S-001", "Status", "BACKLOG")
    with pytest.raises(CommandError, match="BACKLOG"):
        scene.go()


# --- dependants -------------------------------------------------------------------------------


def test_a_task_that_depends_on_a_spike_unblocks_when_it_is_done(tmp_path):
    briefs = {"S-001": spike_brief("prototype"), "T-002": brief_text("T-002", deps="[S-001]")}
    waiting = make_repo(tmp_path / "a", [("S-001", "IN_PROGRESS"), ("T-002", "BACKLOG")], briefs)
    loaded = load_tasks(waiting / "docs" / "WORK-ITEMS.md", waiting / "docs" / "work-items")
    held = select(loaded)
    assert [t.key for t in held.ready] == [] and "S-001" in held.waiting["T-002"]

    done = make_repo(tmp_path / "b", [("S-001", "DONE"), ("T-002", "BACKLOG")], briefs)
    loaded = load_tasks(done / "docs" / "WORK-ITEMS.md", done / "docs" / "work-items")
    assert [t.key for t in select(loaded).ready] == ["T-002"]
