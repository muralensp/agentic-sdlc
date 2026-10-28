"""Briefs, task loading, selection and key allocation (ADLC-005)."""

from pathlib import Path

import pytest

from adlc.states import State
from adlc.tasks import BriefError, allocate_key, load_tasks, parse_brief, select

ROOT = Path(__file__).resolve().parents[1]

GOOD = """---
key: T-001
type: feature
priority: 3
depends_on: [T-000]
adr: none
requires_approval: false
milestone: M1
---
# T-001: Title

**Description:** Something.

**Acceptance criteria:**
- First thing works
- Second thing works

**Notes:** after.
"""


def brief(**overrides) -> str:
    """GOOD with front-matter fields replaced (value None removes the field)."""
    head, _, body = GOOD.removeprefix("---\n").partition("\n---\n")
    fields = dict(line.split(": ", 1) for line in head.splitlines())
    for name, value in overrides.items():
        if value is None:
            fields.pop(name)
        else:
            fields[name] = value
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n---\n" + body


# --- parse_brief ------------------------------------------------------------------------------


def test_a_good_brief_is_parsed_and_extra_fields_are_kept():
    parsed = parse_brief(GOOD, "T-001.md")
    assert parsed.key == "T-001" and parsed.type == "feature" and parsed.priority == 3
    assert parsed.depends_on == ("T-000",) and parsed.adr == "none"
    assert parsed.requires_approval is False
    assert parsed.criteria == ("First thing works", "Second thing works")
    assert parsed.extra == {"milestone": "M1"}


@pytest.mark.parametrize(
    "field", ["key", "type", "priority", "depends_on", "adr", "requires_approval"]
)
def test_each_missing_field_is_named(field):
    with pytest.raises(BriefError, match=f"missing front-matter field '{field}'"):
        parse_brief(brief(**{field: None}), "T-001.md")


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"type": "chore"}, "type"),
        ({"priority": "0"}, "priority"),
        ({"priority": "high"}, "priority"),
        ({"priority": "true"}, "priority"),
        ({"depends_on": "T-000"}, "depends_on"),
        ({"depends_on": "[t-000]"}, "depends_on"),
        ({"depends_on": "[T-001]"}, "itself"),
        ({"depends_on": "[T-000, T-000]"}, "duplicate"),
        ({"adr": "ADR-12"}, "adr"),
        ({"adr": "maybe"}, "adr"),
        ({"requires_approval": "yes please"}, "requires_approval"),
        ({"key": "t-001"}, "key"),
        ({"key": "T-1"}, "key"),
    ],
)
def test_bad_values_are_rejected_with_the_field_name(override, message):
    with pytest.raises(BriefError, match=message):
        parse_brief(brief(**override), "T-001.md")


@pytest.mark.parametrize("adr", ["none", "needed", "ADR-0050"])
def test_adr_accepts_none_needed_or_a_number(adr):
    assert parse_brief(brief(adr=adr), "x.md").adr == adr


def test_no_front_matter_is_rejected():
    with pytest.raises(BriefError, match="front-matter"):
        parse_brief("# Just a title\n- bullet\n", "x.md")


def test_unclosed_front_matter_is_rejected():
    with pytest.raises(BriefError, match="front-matter"):
        parse_brief("---\nkey: T-001\n", "x.md")


def test_yaml_errors_report_the_line():
    with pytest.raises(BriefError, match="line 3"):
        parse_brief("---\nkey: T-001\ntype: [oops\n---\nbody\n", "x.md")


@pytest.mark.parametrize(
    "body",
    [
        "no criteria section at all\n",
        "**Acceptance criteria:**\n\nNothing listed.\n",
        "**Acceptance criteria:**\n- TBD\n- todo\n- ...\n",
        "## Acceptance criteria\n",
    ],
)
def test_missing_or_placeholder_criteria_are_rejected(body):
    head = GOOD.split("---\n")[1]
    with pytest.raises(BriefError, match="acceptance criteria"):
        parse_brief(f"---\n{head}---\n{body}", "x.md")


def test_criteria_may_sit_under_a_heading_and_stop_at_the_next_section():
    head = GOOD.split("---\n")[1]
    text = (
        f"---\n{head}---\n## Acceptance criteria\n\n- One\n* Two\n\n## Other\n- not a criterion\n"
    )
    assert parse_brief(text, "x.md").criteria == ("One", "Two")


def test_errors_name_the_file():
    with pytest.raises(BriefError, match=r"docs[/\\]work-items[/\\]T-001\.md"):
        parse_brief("nothing", Path("docs/work-items/T-001.md"))


# --- load_tasks -------------------------------------------------------------------------------


def make_repo(tmp_path: Path, rows: list[tuple[str, str]], briefs: dict[str, str]) -> tuple:
    board = tmp_path / "WORK-ITEMS.md"
    lines = ["# Log", "", "| Key | Context | Title | Status | Notes |", "|---|---|---|---|---|"]
    lines += [f"| {key} | C | Title of {key} | {status} | |" for key, status in rows]
    board.write_text("\n".join(lines) + "\n")
    folder = tmp_path / "work-items"
    folder.mkdir()
    for key, text in briefs.items():
        (folder / f"{key}.md").write_text(text)
    return board, folder


def spec(key, deps="[]", priority=1, **kw):
    return brief(key=key, depends_on=deps, priority=str(priority), **kw)


def test_valid_items_load_with_their_state_and_title(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "Open")], {"T-001": spec("T-001")})
    loaded = load_tasks(board, folder)
    task = loaded.tasks["T-001"]
    assert task.state is State.BACKLOG and task.title == "Title of T-001"
    assert loaded.rejections == []


def test_item_with_no_brief_is_rejected_and_not_selectable(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "Open")], {})
    loaded = load_tasks(board, folder)
    assert [r.key for r in loaded.rejections] == ["T-001"]
    assert "no brief" in loaded.rejections[0].reason
    assert select(loaded).ready == []


def test_brief_key_must_match_the_row(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "Open")], {"T-001": spec("T-002")})
    reason = load_tasks(board, folder).rejections[0].reason
    assert "T-002" in reason and "T-001" in reason


def test_row_key_must_have_the_code_seq_shape(tmp_path):
    board, folder = make_repo(tmp_path, [("bad key", "Open")], {})
    assert "key" in load_tasks(board, folder).rejections[0].reason


def test_unknown_status_word_is_rejected(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "Closed")], {"T-001": spec("T-001")})
    assert "status" in load_tasks(board, folder).rejections[0].reason


def test_one_bad_brief_does_not_hide_the_others(tmp_path):
    rows = [("T-001", "Open"), ("T-002", "Open")]
    board, folder = make_repo(tmp_path, rows, {"T-001": "junk", "T-002": spec("T-002")})
    loaded = load_tasks(board, folder)
    assert list(loaded.tasks) == ["T-002"] and [r.key for r in loaded.rejections] == ["T-001"]


def test_done_items_need_no_brief(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "DONE")], {})
    loaded = load_tasks(board, folder)
    assert loaded.rejections == [] and loaded.states["T-001"] is State.DONE


def test_unknown_dependency_is_rejected(tmp_path):
    board, folder = make_repo(
        tmp_path, [("T-001", "Open")], {"T-001": spec("T-001", deps="[T-999]")}
    )
    assert "T-999" in load_tasks(board, folder).rejections[0].reason


def test_dependency_cycles_are_rejected_for_every_member(tmp_path):
    rows = [("T-001", "Open"), ("T-002", "Open"), ("T-003", "Open")]
    briefs = {
        "T-001": spec("T-001", deps="[T-002]"),
        "T-002": spec("T-002", deps="[T-001]"),
        "T-003": spec("T-003"),
    }
    loaded = load_tasks(*make_repo(tmp_path, rows, briefs))
    assert sorted(r.key for r in loaded.rejections) == ["T-001", "T-002"]
    assert all("cycle" in r.reason for r in loaded.rejections)
    assert list(loaded.tasks) == ["T-003"]


# --- select -----------------------------------------------------------------------------------


def test_only_backlog_items_with_done_dependencies_are_ready(tmp_path):
    rows = [
        ("T-001", "DONE"),
        ("T-002", "Open"),
        ("T-003", "IN_PROGRESS"),
        ("T-004", "DRAFT"),
        ("T-005", "Open"),
        ("T-006", "NEEDS_HUMAN"),
    ]
    briefs = {
        "T-002": spec("T-002", deps="[T-001]"),
        "T-003": spec("T-003"),
        "T-004": spec("T-004"),
        "T-005": spec("T-005", deps="[T-003]"),
        "T-006": spec("T-006"),
    }
    chosen = select(load_tasks(*make_repo(tmp_path, rows, briefs)))
    assert [t.key for t in chosen.ready] == ["T-002"]
    assert chosen.waiting == {"T-005": "waiting on T-003 (IN_PROGRESS)"}


def test_ready_items_are_ordered_by_priority_then_sequence(tmp_path):
    rows = [("T-010", "Open"), ("T-002", "Open"), ("T-003", "Open"), ("T-001", "Open")]
    briefs = {
        "T-010": spec("T-010", priority=1),
        "T-002": spec("T-002", priority=5),
        "T-003": spec("T-003", priority=5),
        "T-001": spec("T-001", priority=9),
    }
    chosen = select(load_tasks(*make_repo(tmp_path, rows, briefs)))
    assert [t.key for t in chosen.ready] == ["T-010", "T-002", "T-003", "T-001"]


def test_a_failed_dependency_is_reported_as_a_blocker(tmp_path):
    rows = [("T-001", "FAILED"), ("T-002", "Open")]
    briefs = {"T-001": spec("T-001"), "T-002": spec("T-002", deps="[T-001]")}
    chosen = select(load_tasks(*make_repo(tmp_path, rows, briefs)))
    assert chosen.ready == []
    assert chosen.blocked == {"T-002": ["T-001"]}


def test_a_blocked_item_is_unblocked_once_dependencies_are_done(tmp_path):
    rows = [("T-001", "DONE"), ("T-002", "BLOCKED"), ("T-003", "BLOCKED"), ("T-004", "FAILED")]
    briefs = {
        "T-002": spec("T-002", deps="[T-001]"),
        "T-003": spec("T-003", deps="[T-004]"),
        "T-004": spec("T-004"),
    }
    chosen = select(load_tasks(*make_repo(tmp_path, rows, briefs)))
    assert chosen.unblocked == ["T-002"]
    assert chosen.ready == []


def test_requires_approval_is_exposed_not_enforced(tmp_path):
    board, folder = make_repo(
        tmp_path, [("T-001", "Open")], {"T-001": spec("T-001", requires_approval="true")}
    )
    (task,) = select(load_tasks(board, folder)).ready
    assert task.brief.requires_approval is True


# --- allocate_key -----------------------------------------------------------------------------


def test_next_key_follows_the_highest_seen_in_rows_and_briefs(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "DONE"), ("T-004", "Open")], {})
    assert allocate_key("T", board, folder) == "T-005"


def test_a_removed_row_does_not_free_its_key_while_the_brief_remains(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "DONE")], {"T-009": spec("T-009")})
    assert allocate_key("T", board, folder) == "T-010"


def test_a_new_code_starts_at_001(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "DONE")], {})
    assert allocate_key("NEW", board, folder) == "NEW-001"


def test_codes_do_not_share_a_sequence(tmp_path):
    board, folder = make_repo(tmp_path, [("A-007", "DONE"), ("B-002", "DONE")], {})
    assert allocate_key("B", board, folder) == "B-003"


def test_a_bad_code_is_refused(tmp_path):
    board, folder = make_repo(tmp_path, [], {})
    with pytest.raises(ValueError, match="code"):
        allocate_key("lower", board, folder)


# --- this repository's own board --------------------------------------------------------------


def test_this_repos_board_and_briefs_all_validate():
    loaded = load_tasks(ROOT / "docs" / "WORK-ITEMS.md", ROOT / "docs" / "work-items")
    assert loaded.rejections == []
    assert len(loaded.states) >= 33


# --- DRAFT items and next_priority (ADLC-007) --------------------------------------------------


def test_a_draft_item_is_not_validated_and_is_never_selectable(tmp_path):
    rows = [("T-001", "DRAFT"), ("T-002", "DRAFT"), ("T-003", "DRAFT")]
    board, folder = make_repo(tmp_path, rows, {"T-002": "not a brief at all"})
    (folder / "T-003.md").write_text("---\nkey: T-003\n---\n")
    loaded = load_tasks(board, folder)
    assert loaded.rejections == []
    assert loaded.tasks == {}
    assert loaded.states == dict.fromkeys(["T-001", "T-002", "T-003"], State.DRAFT)
    assert select(loaded).ready == []


def test_depending_on_a_draft_item_waits(tmp_path):
    rows = [("T-001", "DRAFT"), ("T-002", "Open")]
    board, folder = make_repo(tmp_path, rows, {"T-002": spec("T-002", deps="[T-001]")})
    assert select(load_tasks(board, folder)).waiting == {"T-002": "waiting on T-001 (DRAFT)"}


def test_an_item_promoted_to_backlog_is_checked_again(tmp_path):
    board, folder = make_repo(tmp_path, [("T-001", "Open")], {"T-001": "---\nkey: T-001\n---\n"})
    assert [r.key for r in load_tasks(board, folder).rejections] == ["T-001"]


def test_next_priority_is_one_above_the_highest_in_any_brief(tmp_path):
    from adlc.tasks import next_priority

    folder = tmp_path / "b"
    folder.mkdir()
    assert next_priority(folder) == 1  # nothing in the folder yet
    assert next_priority(tmp_path / "missing") == 1
    (folder / "T-001.md").write_text(spec("T-001", priority=4))
    (folder / "T-002.md").write_text(spec("T-002", priority=9))
    (folder / "T-003.md").write_text("garbage, no front-matter")
    (folder / "T-004.md").write_text("---\npriority: many\n---\n")
    (folder / "notes.txt").write_text("---\npriority: 50\n---\n")
    assert next_priority(folder) == 10
