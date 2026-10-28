"""The WORK-ITEMS.md table parser and writer (ADLC-004)."""

import os
from pathlib import Path

import pytest

from adlc import board as board_mod
from adlc.board import Board, BoardError, BoardLocked, parse, with_cell

FIXTURE = Path(__file__).parent / "fixtures" / "work-items.md"


@pytest.fixture
def text() -> str:
    return FIXTURE.read_bytes().decode("utf-8")


@pytest.fixture
def path(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "WORK-ITEMS.md"
    p.write_bytes(text.encode("utf-8"))
    return p


def changed_lines(before: str, after: str) -> list[int]:
    a, b = before.split("\n"), after.split("\n")
    assert len(a) == len(b)
    return [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]


# --- parsing ----


def test_parse_finds_the_board_and_skips_other_tables(text):
    table = parse(text)
    assert table.header == (
        "Key",
        "Context",
        "Title",
        "Status",
        "Owner",
        "Linked PRD Chapter / Day",
        "Notes",
    )
    assert [r.key for r in table.rows] == ["T-001", "T-002", "T-003", "T-004"]


def test_parse_reads_cells_and_escaped_pipes(text):
    row = parse(text).row("T-002")
    assert row.cells["Title"] == r"Second item with a \| pipe"
    assert row.cells["Status"] == "IN_REVIEW"
    assert row.cells["Notes"] == ""
    assert row.line == 12


def test_parse_handles_tight_and_wide_spacing(text):
    table = parse(text)
    assert table.row("T-003").cells["Notes"] == "no padding"
    assert table.row("T-004").cells["Status"] == "Open"


def test_header_names_match_ignoring_case():
    src = "| key | STATUS | notes |\n|---|---|---|\n| A-1 | Open | x |\n"
    assert parse(src).row("A-1").cells["STATUS"] == "Open"


# --- round trip and minimal edits ----


def test_setting_the_same_value_is_a_byte_identical_round_trip(text):
    for row in parse(text).rows:
        for column in ("Status", "Notes"):
            assert with_cell(text, row.key, column, row.cells[column]) == text


def test_editing_status_changes_exactly_one_line_and_one_cell(text):
    after = with_cell(text, "T-001", "Status", "IN_PROGRESS")
    (line,) = changed_lines(text, after)
    assert after.split("\n")[line] == (
        "| T-001 | Platform | First item | IN_PROGRESS | ann | Day 1 | Plain note. |"
    )
    old, new = parse(text), parse(after)
    for a, b in zip(old.rows, new.rows, strict=True):
        for col in old.header:
            if (a.key, col) != ("T-001", "Status"):
                assert a.cells[col] == b.cells[col]


def test_edit_keeps_padding_of_wide_and_tight_cells(text):
    after = with_cell(text, "T-004", "Status", "DONE")
    assert "| DONE   | dee   |" in after
    after = with_cell(text, "T-003", "Notes", "ok")
    assert "|Day 3|ok|" in after


def test_edit_fills_an_empty_cell(text):
    after = with_cell(text, "T-002", "Notes", "Escalated")
    assert after.split("\n")[11].endswith("| Day 2 | Escalated |")


def test_pipes_in_a_new_value_are_escaped_and_read_back(text):
    after = with_cell(text, "T-001", "Notes", "a | b")
    assert parse(after).row("T-001").cells["Notes"] == r"a \| b"
    assert len(parse(after).rows) == 4


def test_rows_outside_the_table_and_unknown_columns_survive(text):
    after = with_cell(text, "T-001", "Status", "DONE")
    assert after.startswith(text.split("| Key |")[0])
    assert after.endswith(
        "Prose after the board. Unknown columns above (Owner) must be preserved.\n"
    )
    assert parse(after).row("T-001").cells["Owner"] == "ann"


def test_crlf_and_missing_final_newline_are_preserved(text):
    crlf = text.replace("\n", "\r\n").rstrip("\r\n")
    after = with_cell(crlf, "T-001", "Status", "DONE")
    assert "\n" not in after.replace("\r\n", "")
    assert not after.endswith("\n")
    assert len(changed_lines(crlf, after)) == 1


# --- refusals ----


@pytest.mark.parametrize("column", ["Title", "Key", "Owner", "Nope"])
def test_only_status_and_notes_are_editable(text, column):
    with pytest.raises(BoardError):
        with_cell(text, "T-001", column, "x")


def test_newline_in_a_value_is_rejected(text):
    with pytest.raises(BoardError, match="newline"):
        with_cell(text, "T-001", "Notes", "a\nb")


def test_unknown_key_is_rejected(text):
    with pytest.raises(BoardError, match="T-999"):
        with_cell(text, "T-999", "Status", "DONE")


def test_key_lookup_is_case_sensitive(text):
    with pytest.raises(BoardError):
        with_cell(text, "t-001", "Status", "DONE")


def test_expected_value_must_match_or_the_edit_is_refused(text):
    with pytest.raises(BoardError, match="changed"):
        with_cell(text, "T-001", "Status", "DONE", expected="IN_PROGRESS")
    assert "DONE" in with_cell(text, "T-001", "Status", "DONE", expected="Open")


@pytest.mark.parametrize(
    ("src", "message"),
    [
        ("no table here\n", "no board table"),
        ("| Key | Status | Notes |\n| A | Open | x |\n", "no board table"),
        ("| Key | Status |\n|---|---|\n| A | Open |\n", "no board table"),
        ("| Key | Status | Notes |\n|---|---|---|\n| A | Open |\n", "line 3"),
        (
            "| Key | Status | Notes |\n|---|---|---|\n| A | Open | x |\n| A | Open | y |\n",
            "duplicate",
        ),
        ("| Key | Status | Notes |\n|---|---|---|\n|  | Open | x |\n", "empty key"),
        (
            "| Key | Status | Notes |\n|---|---|---|\n| A | Open | x |\n\n"
            "| Key | Status | Notes |\n|---|---|---|\n| B | Open | y |\n",
            "more than one",
        ),
    ],
)
def test_malformed_tables_raise_a_clear_error(src, message):
    with pytest.raises(BoardError, match=message):
        parse(src)


# --- files, lock, atomic write ----


def test_board_set_cell_writes_the_file_and_releases_the_lock(path, text):
    Board(path).set_cell("T-001", "Status", "DONE")
    assert changed_lines(text, path.read_bytes().decode()) == [10]
    assert not (path.parent / ".WORK-ITEMS.md.lock").exists()
    assert [p.name for p in path.parent.iterdir()] == ["WORK-ITEMS.md"]


def test_unchanged_value_leaves_the_file_byte_identical(path):
    before = path.read_bytes()
    Board(path).set_cell("T-001", "Status", "Open")
    assert path.read_bytes() == before


def test_second_writer_is_refused_while_the_lock_is_held(path):
    first = Board(path)
    before = path.read_bytes()
    with first.lock():
        with pytest.raises(BoardLocked, match=str(os.getpid())):
            Board(path).set_cell("T-001", "Status", "DONE")
    assert path.read_bytes() == before
    Board(path).set_cell("T-001", "Status", "DONE")  # free again


def test_a_leftover_lock_is_reported_not_removed(path):
    lock = path.parent / ".WORK-ITEMS.md.lock"
    lock.write_text("4242")
    with pytest.raises(BoardLocked, match="4242"):
        Board(path).set_cell("T-001", "Status", "DONE")
    assert lock.exists()


def test_a_failed_replace_leaves_the_original_and_cleans_up(path, monkeypatch):
    before = path.read_bytes()

    def boom(*_a, **_k):
        raise PermissionError("file in use")

    monkeypatch.setattr(board_mod.os, "replace", boom)
    with pytest.raises(BoardError, match="open in another program"):
        Board(path).set_cell("T-001", "Status", "DONE")
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["WORK-ITEMS.md"]


def test_the_stale_value_check_works_on_the_file_under_the_lock(path):
    path.write_bytes(path.read_bytes().replace(b"| Open | ann", b"| BLOCKED | ann"))
    with pytest.raises(BoardError, match="changed"):
        Board(path).set_cell("T-001", "Status", "DONE", expected="Open")


def test_missing_file_is_a_board_error(tmp_path):
    with pytest.raises(BoardError, match="cannot read"):
        Board(tmp_path / "nope.md").rows()


# --- the real BOSS NextGen board (skipped unless ADLC_BOSS_BOARD is set) ----

BOSS = os.environ.get("ADLC_BOSS_BOARD")


@pytest.mark.skipif(not BOSS, reason="set ADLC_BOSS_BOARD to BOSS NextGen's docs/WORK-ITEMS.md")
def test_real_boss_board_round_trips_and_edits_one_line():
    real = Path(BOSS).read_bytes().decode("utf-8")
    table = parse(real)
    assert table.rows
    for row in table.rows:
        for column in ("Status", "Notes"):
            assert with_cell(real, row.key, column, row.cells[column]) == real
    first = table.rows[0]
    after = with_cell(real, first.key, "Status", "IN_PROGRESS")
    assert len(changed_lines(real, after)) == 1


# --- adding a row (ADLC-007) ------------------------------------------------------------------

ADD_BOARD = (
    "# Log\n\nIntro | with a pipe\n\n"
    "| Key | Context | Title | Status | Linked PRD Chapter / Day | Notes |\n"
    "|---|---|---|---|---|---|\n"
    "| T-001 | Ctx (A) | First | Open | Day 1 | n1 |\n"
    "| T-002 | Ctx (B) | Second | DONE |  | n2 |\n"
    "\nAfter the table.\n"
)


def new_row(key="T-003", **over):
    values = {"key": key, "context": "Ctx (B)", "title": "Third", "status": "DRAFT"}
    values.update(over)
    return values


def test_with_row_inserts_one_line_after_the_last_row_and_changes_nothing_else():
    from adlc.board import with_row

    out = with_row(ADD_BOARD, new_row(notes="Brief: x.md"))
    expected = ADD_BOARD.replace(
        "| n2 |\n", "| n2 |\n| T-003 | Ctx (B) | Third | DRAFT |  | Brief: x.md |\n"
    )
    assert out == expected


def test_with_row_matches_columns_by_name_prefix_and_leaves_unknown_ones_blank():
    from adlc.board import with_row

    out = with_row(ADD_BOARD, new_row(**{"linked prd": "FR-5"}))
    assert "| T-003 | Ctx (B) | Third | DRAFT | FR-5 |  |\n" in out


def test_with_row_escapes_pipes_and_refuses_newlines():
    from adlc.board import with_row

    assert "a " + chr(92) + "| b" in with_row(ADD_BOARD, new_row(title="a | b"))
    with pytest.raises(BoardError):
        with_row(ADD_BOARD, new_row(title="a\nb"))


def test_with_row_refuses_a_duplicate_key_and_an_unknown_column():
    from adlc.board import with_row

    with pytest.raises(BoardError, match="already"):
        with_row(ADD_BOARD, new_row(key="T-002"))
    with pytest.raises(BoardError, match="column"):
        with_row(ADD_BOARD, new_row(colour="red"))


def test_with_row_needs_a_key():
    from adlc.board import with_row

    with pytest.raises(BoardError, match="key"):
        with_row(ADD_BOARD, {"title": "x"})


def test_with_row_works_on_an_empty_table_and_without_a_final_newline():
    from adlc.board import with_row

    head = "| Key | Status | Notes |\n|---|---|---|"
    assert with_row(head, {"key": "T-001", "status": "DRAFT"}).endswith(
        "|---|---|---|\n| T-001 | DRAFT |  |"
    )
    full = head + "\n| T-001 | Open | n |"
    assert with_row(full, {"key": "T-002"}) == full + "\n| T-002 |  |  |"


def test_with_row_keeps_crlf_line_endings():
    from adlc.board import with_row

    crlf = ADD_BOARD.replace("\n", "\r\n")
    out = with_row(crlf, new_row())
    assert "| T-003 | Ctx (B) | Third | DRAFT |  |  |\r\n" in out
    assert out.count("\r\n") == crlf.count("\r\n") + 1 and out.count("\n") == out.count("\r\n")


def test_board_add_row_writes_under_the_lock_and_readds_nothing(tmp_path):
    path = tmp_path / "WORK-ITEMS.md"
    path.write_bytes(ADD_BOARD.encode())
    board = Board(path)
    board.add_row(new_row())
    assert [r.key for r in board.rows()] == ["T-001", "T-002", "T-003"]
    with pytest.raises(BoardError):
        board.add_row(new_row())
    assert not list(tmp_path.glob(".WORK-ITEMS.md.*"))


def test_add_row_inside_a_held_lock_does_not_deadlock(tmp_path):
    path = tmp_path / "WORK-ITEMS.md"
    path.write_bytes(ADD_BOARD.encode())
    board = Board(path)
    with board.lock():
        board.add_row(new_row())
        board.set_cell("T-003", "Status", "BACKLOG")
    assert board.rows()[-1].cells["Status"] == "BACKLOG"
    assert not list(tmp_path.glob(".WORK-ITEMS.md.lock"))


def test_add_row_refuses_while_another_writer_holds_the_lock(tmp_path):
    path = tmp_path / "WORK-ITEMS.md"
    path.write_bytes(ADD_BOARD.encode())
    (tmp_path / ".WORK-ITEMS.md.lock").write_text("123")
    with pytest.raises(BoardLocked):
        Board(path).add_row(new_row())
