"""Parser and writer for the board: the Markdown table in WORK-ITEMS.md.

Only the Status and Notes cells can be edited. An edit replaces the characters of one cell and
nothing else, so prose, other tables, unknown columns, padding and line endings come back as they
were. Values are opaque text here; checking them against the state list is the state machine's job.
"""

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

EDITABLE = ("status", "notes")
REQUIRED = ("key", "status", "notes")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_BARE_PIPE = re.compile(r"(?<!\\)\|")


class BoardError(Exception):
    """The board file or a requested edit is not acceptable. Nothing was written."""


class BoardLocked(BoardError):
    """Another writer holds the lock."""


@dataclass(frozen=True)
class Row:
    key: str
    line: int  # 1-based line number in the file
    cells: dict[str, str]  # header name as written -> cell text, escapes left as they are


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[Row, ...]

    def row(self, key: str) -> Row:
        for row in self.rows:
            if row.key == key:
                return row
        raise BoardError(f"no work item {key!r} on the board")


def _pipes(line: str) -> list[int]:
    """Positions of the pipes that separate cells (a pipe after a backslash is text)."""
    found, i = [], 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "|":
            found.append(i)
        i += 1
    return found


def _spans(line: str) -> list[tuple[int, int]] | None:
    """Cell spans of a table row, or None if the line is not one (needs a pipe at both ends)."""
    body = line.rstrip()
    pipes = _pipes(body)
    if len(pipes) < 2 or pipes[0] != 0 or pipes[-1] != len(body) - 1:
        return None
    return [(a + 1, b) for a, b in zip(pipes, pipes[1:], strict=False)]


def _cells(line: str) -> list[str] | None:
    spans = _spans(line)
    return None if spans is None else [line[a:b].strip() for a, b in spans]


def _is_separator(cells: list[str] | None, width: int) -> bool:
    return cells is not None and len(cells) == width and all(map(_SEPARATOR_CELL.match, cells))


def parse(text: str) -> Table:
    """Find the board table (the one with Key, Status and Notes columns) and read its rows."""
    lines = text.split("\n")
    found: list[tuple[int, list[str], int]] = []  # header index, header cells, first body index
    i = 0
    while i + 1 < len(lines):
        header = _cells(lines[i])
        if header is not None and _is_separator(_cells(lines[i + 1]), len(header)):
            found.append((i, header, i + 2))
            i += 2
        else:
            i += 1
    boards = [t for t in found if all(c in [h.lower() for h in t[1]] for c in REQUIRED)]
    if not boards:
        raise BoardError("no board table found (needs Key, Status and Notes columns)")
    if len(boards) > 1:
        raise BoardError("more than one board table found; there must be exactly one")
    head_at, header, body_at = boards[0]
    lowered = [h.lower() for h in header]
    if any(lowered.count(c) > 1 for c in REQUIRED):
        raise BoardError(f"line {head_at + 1}: Key, Status and Notes must each appear once")
    rows: list[Row] = []
    first_seen: dict[str, int] = {}
    for n in range(body_at, len(lines)):
        cells = _cells(lines[n])
        if cells is None:
            break
        if len(cells) != len(header):
            raise BoardError(f"line {n + 1}: expected {len(header)} cells, found {len(cells)}")
        key = cells[lowered.index("key")]
        if not key:
            raise BoardError(f"line {n + 1}: empty key")
        if key in first_seen:
            raise BoardError(
                f"line {n + 1}: duplicate key {key!r} (first on line {first_seen[key]})"
            )
        first_seen[key] = n + 1
        rows.append(Row(key, n + 1, dict(zip(header, cells, strict=True))))
    return Table(tuple(header), tuple(rows))


def with_cell(text: str, key: str, column: str, value: str, expected: str | None = None) -> str:
    """Return `text` with one Status or Notes cell replaced and every other byte unchanged.

    If `expected` is given and the cell no longer holds it, the edit is refused: someone changed
    the cell since the caller read it.
    """
    if column.lower() not in EDITABLE:
        raise BoardError(f"column {column!r} is not editable (only Status and Notes)")
    if "\n" in value or "\r" in value:
        raise BoardError("a cell value cannot contain a newline")
    table = parse(text)
    row = table.row(key)
    name = next(h for h in table.header if h.lower() == column.lower())
    if expected is not None and row.cells[name] != expected:
        raise BoardError(
            f"{key} {name} changed: it is now {row.cells[name]!r}, expected {expected!r}"
        )
    value = _BARE_PIPE.sub(lambda _m: "\\|", value)
    lines = text.split("\n")
    line = lines[row.line - 1]
    spans = _spans(line)
    assert spans is not None  # parse() already accepted this line
    start, end = spans[[h.lower() for h in table.header].index(name.lower())]
    raw = line[start:end]
    if value == raw.strip():
        return text
    if raw.strip():
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()) :]
        new = lead + value + trail
    else:
        new = f" {value} "
    lines[row.line - 1] = line[:start] + new + line[end:]
    return "\n".join(lines)


class Board:
    """A WORK-ITEMS.md file on disk, edited under a lock and replaced atomically."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")

    def _read(self) -> str:
        try:
            return self.path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BoardError(f"cannot read {self.path}: {exc}") from exc

    def rows(self) -> list[Row]:
        return list(parse(self._read()).rows)

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Take the writer lock or fail at once. A leftover lock is reported, never removed."""
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = "unknown"
            with contextlib.suppress(OSError):
                owner = self._lock_path.read_text().strip() or owner
            raise BoardLocked(
                f"{self.path} is locked by process {owner} ({self._lock_path}); "
                "if that process is not running, delete the lock file"
            ) from None
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            self._lock_path.unlink(missing_ok=True)

    def set_cell(self, key: str, column: str, value: str, expected: str | None = None) -> None:
        with self.lock():
            text = self._read()
            new = with_cell(text, key, column, value, expected)
            if new != text:
                self._write(new)

    def _write(self, text: str) -> None:
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            with contextlib.suppress(OSError):
                shutil.copymode(self.path, tmp)
            os.replace(tmp, self.path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise BoardError(
                f"cannot replace {self.path}: it may be open in another program ({exc})"
            ) from exc
