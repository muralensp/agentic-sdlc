"""The board commands: add, status and log (ADLC-007)."""

import json
from pathlib import Path

import pytest
import yaml

from adlc.board import Board, BoardError, BoardLocked
from adlc.commands import (
    CommandError,
    add_item,
    find_repo,
    read_log,
    render_status,
    status_report,
)
from adlc.states import State
from adlc.tasks import load_tasks, parse_brief

HEADER = (
    "# Log\n\nProse | stays.\n\n"
    "| Key | Context | Title | Status | Linked PRD Chapter / Day | Notes |\n"
    "|---|---|---|---|---|---|\n"
)


def brief_text(key, *, deps="[]", priority=1):
    return (
        f"---\nkey: {key}\ntype: feature\npriority: {priority}\ndepends_on: {deps}\n"
        f"adr: none\nrequires_approval: false\n---\n# {key}\n\n"
        "**Acceptance criteria:**\n- It works\n"
    )


def make_repo(tmp_path: Path, rows: list[tuple], briefs: dict[str, str] | None = None) -> Path:
    """rows: (key, status, notes, context). Briefs default to a valid one for every row."""
    repo = tmp_path / "repo"
    (repo / ".adlc").mkdir(parents=True)
    (repo / "docs" / "work-items").mkdir(parents=True)
    lines = [HEADER]
    for key, status, *rest in rows:
        notes = rest[0].replace("|", chr(92) + "|") if rest else ""
        context = rest[1] if len(rest) > 1 else f"{key.split('-')[0]} (Area)"
        lines.append(f"| {key} | {context} | Title of {key} | {status} |  | {notes} |\n")
    (repo / "docs" / "WORK-ITEMS.md").write_bytes(("".join(lines) + "\nAfter.\n").encode())
    given = briefs if briefs is not None else {k: brief_text(k) for k, *_ in rows}
    for key, text in given.items():
        (repo / "docs" / "work-items" / f"{key}.md").write_text(text)
    return repo


def board_text(repo: Path) -> str:
    return (repo / "docs" / "WORK-ITEMS.md").read_bytes().decode()


def brief_names(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / "docs" / "work-items").iterdir())


# --- find_repo --------------------------------------------------------------------------------


def test_find_repo_walks_up_to_the_first_adlc_or_git_folder(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    deep = repo / "a" / "b"
    deep.mkdir(parents=True)
    assert find_repo(deep) == repo
    (tmp_path / "plain" / ".git").mkdir(parents=True)
    assert find_repo(tmp_path / "plain") == tmp_path / "plain"


def test_find_repo_refuses_when_nothing_is_found(tmp_path):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    with pytest.raises(CommandError, match="repository"):
        find_repo(lonely, stop=tmp_path)


# --- add --------------------------------------------------------------------------------------


def test_add_allocates_the_next_key_and_creates_a_row_and_a_brief(tmp_path):
    repo = make_repo(
        tmp_path, [("PLAT-006", "Open"), ("PLAT-007", "DONE", "n", "Platform (Infra)")]
    )
    before = board_text(repo)
    assert add_item(repo, "PLAT", "Wire the thing") == "PLAT-008"
    row = (
        "| PLAT-008 | Platform (Infra) | Wire the thing | DRAFT |  "
        "| Brief: docs/work-items/PLAT-008.md |\n"
    )
    assert board_text(repo) == before.replace("\nAfter.\n", row + "\nAfter.\n")
    assert (repo / "docs" / "work-items" / "PLAT-008.md").is_file()


def test_the_new_brief_has_front_matter_and_unfilled_criteria(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")], {"T-001": brief_text("T-001", priority=6)})
    add_item(repo, "T", "A title", kind="bugfix", depends=["T-001"])
    text = (repo / "docs" / "work-items" / "T-002.md").read_text()
    front = yaml.safe_load(text.split("---\n")[1])
    assert front == {
        "key": "T-002",
        "type": "bugfix",
        "priority": 7,
        "depends_on": ["T-001"],
        "adr": "none",
        "requires_approval": False,
    }
    assert "# T-002: A title" in text and "- TBD" in text
    with pytest.raises(Exception, match="acceptance criteria"):
        parse_brief(text, "T-002.md")  # cannot be promoted until it is filled in


def test_a_draft_skeleton_loads_without_a_problem(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    add_item(repo, "T", "Idea")
    loaded = load_tasks(repo / "docs" / "WORK-ITEMS.md", repo / "docs" / "work-items")
    assert loaded.rejections == [] and loaded.states["T-002"] is State.DRAFT


def test_add_options_priority_and_prd(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    add_item(repo, "T", "One", priority=2, prd="FR-5")
    assert "| DRAFT | FR-5 |" in board_text(repo)
    assert "priority: 2\n" in (repo / "docs" / "work-items" / "T-002.md").read_text()


def test_an_unknown_code_is_refused_unless_new_code_is_given(tmp_path):
    repo = make_repo(tmp_path, [("PLAT-001", "Open")])
    before = board_text(repo)
    with pytest.raises(CommandError, match="PLTA.*--new-code"):
        add_item(repo, "PLTA", "Typo")
    assert board_text(repo) == before
    assert brief_names(repo) == ["PLAT-001.md"]
    assert add_item(repo, "SEC", "Fresh", new_code=True) == "SEC-001"
    assert "| SEC-001 | SEC | Fresh | DRAFT |" in board_text(repo)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "  "},
        {"title": "two" + chr(10) + "lines"},
        {"kind": "epic"},
        {"priority": 0},
        {"depends": ["nonsense"]},
        {"depends": ["T-099"]},
    ],
)
def test_bad_input_is_refused_and_nothing_is_written(tmp_path, kwargs):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    before = board_text(repo)
    with pytest.raises(CommandError):
        add_item(repo, "T", **{"title": "ok", **kwargs})
    assert board_text(repo) == before
    assert brief_names(repo) == ["T-001.md"]


def test_an_invalid_code_is_refused(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    with pytest.raises(CommandError, match="code"):
        add_item(repo, "t-1", "x")


def test_a_leftover_brief_file_is_never_overwritten(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    leftover = repo / "docs" / "work-items" / "T-002.md"
    leftover.write_text("precious")
    assert add_item(repo, "T", "x") == "T-003"  # allocation skips keys that already have a brief
    assert leftover.read_text() == "precious"


def test_a_locked_board_is_refused_and_leaves_no_brief(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    (repo / "docs" / ".WORK-ITEMS.md.lock").write_text("1")
    with pytest.raises(BoardLocked):
        add_item(repo, "T", "x")
    assert brief_names(repo) == ["T-001.md"]


def test_the_brief_is_rolled_back_when_the_row_cannot_be_added(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, [("T-001", "Open")])

    def boom(self, values):
        raise OSError("disk full")

    monkeypatch.setattr(Board, "add_row", boom)
    with pytest.raises(OSError):
        add_item(repo, "T", "x")
    assert brief_names(repo) == ["T-001.md"]


def test_a_missing_board_is_a_clear_error(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    (repo / "docs" / "WORK-ITEMS.md").unlink()
    with pytest.raises(BoardError, match="WORK-ITEMS.md"):
        add_item(repo, "T", "x")


def test_a_configured_board_location_is_used(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    (repo / "docs" / "WORK-ITEMS.md").rename(repo / "board.md")
    (repo / ".adlc" / "config.yaml").write_text("board: {path: board.md, briefs: briefs}\n")
    assert add_item(repo, "T", "x") == "T-002"
    assert (repo / "briefs" / "T-002.md").is_file()
    assert "| Brief: briefs/T-002.md |" in (repo / "board.md").read_text()


# --- status -----------------------------------------------------------------------------------


def sample(tmp_path: Path) -> Path:
    rows = [
        ("T-001", "DONE"),
        ("T-002", "Open"),
        ("T-003", "Open"),
        ("T-004", "FAILED", "tests red twice | see log"),
        ("T-005", "NEEDS_HUMAN", "protected path touched"),
        ("T-006", "IN_PROGRESS"),
        ("T-007", "IN_REVIEW"),
        ("T-008", "DRAFT"),
        ("T-009", "BLOCKED", "dependency T-004 failed"),
        ("T-010", "Open"),
        ("T-011", "Open"),
    ]
    briefs = {
        "T-002": brief_text("T-002", priority=5),
        "T-003": brief_text("T-003", deps="[T-006]", priority=1),
        "T-004": brief_text("T-004"),
        "T-005": brief_text("T-005"),
        "T-006": brief_text("T-006"),
        "T-007": brief_text("T-007"),
        "T-009": brief_text("T-009"),
        "T-010": brief_text("T-010", deps="[T-004]"),
        # T-011 has no brief
    }
    return make_repo(tmp_path, rows, briefs)


def groups(report) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in report["items"]:
        out.setdefault(item["group"], []).append(item["key"])
    return out


def test_status_groups_every_item(tmp_path):
    report = status_report(sample(tmp_path))
    assert groups(report) == {
        "attention": ["T-004", "T-005", "T-009"],
        "in_flight": ["T-006", "T-007"],
        "ready": ["T-002"],
        "waiting": ["T-003", "T-010"],
        "draft": ["T-008"],
        "problem": ["T-011"],
        "done": ["T-001"],
    }
    assert report["done_count"] == 1
    assert [r["key"] for r in report["rejections"]] == ["T-011"]
    assert "no brief" in report["rejections"][0]["reason"]


def test_status_items_carry_state_reason_and_dependencies(tmp_path):
    items = {i["key"]: i for i in status_report(sample(tmp_path))["items"]}
    failed = items["T-004"]
    assert failed["state"] == "FAILED" and failed["reason"] == "tests red twice | see log"
    assert failed["ready"] is False and failed["title"] == "Title of T-004"
    assert items["T-002"]["ready"] is True and items["T-002"]["priority"] == 5
    assert items["T-003"]["reason"] == "waiting on T-006 (IN_PROGRESS)"
    assert items["T-003"]["depends_on"] == ["T-006"]
    assert "T-004" in items["T-010"]["reason"]
    assert items["T-008"]["priority"] is None


def test_status_failures_come_first_in_the_text(tmp_path):
    repo = sample(tmp_path)
    log = repo / ".adlc" / "logs" / "T-004" / "run-2"
    log.mkdir(parents=True)
    (log / "summary.md").write_text("it failed")
    text = render_status(status_report(repo), repo=repo, all_items=False)
    assert text.index("T-004") < text.index("T-006") < text.index("T-002") < text.index("T-008")
    assert text.index("NEEDS ATTENTION") < text.index("IN FLIGHT") < text.index("READY")
    assert "tests red twice | see log" in text
    assert ".adlc/logs/T-004/run-2/summary.md" in text.replace(chr(92), "/")
    assert "no log yet" in text  # T-005 has none
    assert "DONE: 1" in text and "T-001" not in text


def test_status_all_lists_the_done_items(tmp_path):
    repo = sample(tmp_path)
    assert "T-001" in render_status(status_report(repo), repo=repo, all_items=True)


def test_empty_groups_are_left_out(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    text = render_status(status_report(repo), repo=repo, all_items=False)
    assert "READY" in text
    for gone in ("NEEDS ATTENTION", "IN FLIGHT", "WAITING", "DRAFT", "BRIEF PROBLEMS"):
        assert gone not in text


def test_status_json_is_stable_and_parseable(tmp_path):
    data = json.loads(json.dumps(status_report(sample(tmp_path))))
    assert set(data) == {"items", "rejections", "done_count"}
    assert set(data["items"][0]) == {
        "key", "title", "state", "priority", "depends_on", "group", "reason", "ready",
    }  # fmt: skip


def test_status_does_not_modify_anything(tmp_path):
    repo = sample(tmp_path)
    before = board_text(repo)
    status_report(repo)
    assert board_text(repo) == before
    assert not list((repo / "docs").glob(".WORK-ITEMS.md.*"))


def test_an_unreadable_status_is_reported_as_a_problem_not_a_crash(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open"), ("T-002", "Sleeping")])
    report = status_report(repo)
    assert groups(report)["problem"] == ["T-002"]
    assert next(i for i in report["items"] if i["key"] == "T-002")["state"] is None


# --- log --------------------------------------------------------------------------------------


def make_runs(repo: Path, key: str, runs: dict[int, str | None]) -> None:
    for n, text in runs.items():
        folder = repo / ".adlc" / "logs" / key / f"run-{n}"
        folder.mkdir(parents=True)
        if text is not None:
            (folder / "summary.md").write_text(text)


def test_log_prints_the_newest_run_by_number_not_by_name(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    make_runs(repo, "T-001", {2: "second", 10: "tenth", 9: "ninth"})
    assert read_log(repo, "T-001") == "tenth"


def test_log_can_pick_a_run(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    make_runs(repo, "T-001", {1: "first", 2: "second"})
    assert read_log(repo, "T-001", run=1) == "first"


def test_log_with_no_runs_says_so(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    assert read_log(repo, "T-001") == "no runs yet for T-001"


def test_log_errors(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    make_runs(repo, "T-001", {1: None})
    with pytest.raises(CommandError, match="T-404"):
        read_log(repo, "T-404")
    with pytest.raises(CommandError, match="run 5"):
        read_log(repo, "T-001", run=5)
    with pytest.raises(CommandError, match="summary"):
        read_log(repo, "T-001")


# --- extra guards found by fault injection -------------------------------------------------------


def test_an_existing_brief_is_not_overwritten_even_if_allocation_races(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    before = (repo / "docs" / "work-items" / "T-001.md").read_text()
    monkeypatch.setattr("adlc.commands.allocate_key", lambda *a: "T-001")
    with pytest.raises(CommandError, match="already exists"):
        add_item(repo, "T", "x")
    assert (repo / "docs" / "work-items" / "T-001.md").read_text() == before


def test_the_key_is_allocated_and_the_row_added_under_one_lock(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    lock = repo / "docs" / ".WORK-ITEMS.md.lock"
    seen = []
    real = __import__("adlc.commands", fromlist=["allocate_key"]).allocate_key

    def spy(*args):
        seen.append(lock.exists())
        return real(*args)

    monkeypatch.setattr("adlc.commands.allocate_key", spy)
    add_item(repo, "T", "x")
    assert seen == [True] and not lock.exists()


def test_depends_must_look_like_keys(tmp_path):
    repo = make_repo(tmp_path, [("T-001", "Open")])
    with pytest.raises(CommandError, match="CODE-123"):
        add_item(repo, "T", "x", depends=["nonsense"])


def test_ready_and_waiting_items_follow_priority_not_row_order(tmp_path):
    rows = [("T-001", "Open"), ("T-002", "Open"), ("T-003", "Open"), ("T-004", "Open")]
    briefs = {
        "T-001": brief_text("T-001", priority=5),
        "T-002": brief_text("T-002", priority=1),
        "T-003": brief_text("T-003", deps="[T-001]", priority=4),
        "T-004": brief_text("T-004", deps="[T-001]", priority=2),
    }
    repo = make_repo(tmp_path, rows, briefs)
    got = groups(status_report(repo))
    assert got["ready"] == ["T-002", "T-001"] and got["waiting"] == ["T-004", "T-003"]
