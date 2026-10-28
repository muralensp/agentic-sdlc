"""Task states and the legal transitions between them (PRD FR-3).

The orchestrator may make the moves marked `orchestrator`; moves marked `human` need a person
(an approve, retry, reset or refine command). A person may also make any move the orchestrator can.
"""

from enum import StrEnum


class State(StrEnum):
    DRAFT = "DRAFT"
    BACKLOG = "BACKLOG"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    DONE = "DONE"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class Actor(StrEnum):
    ORCHESTRATOR = "orchestrator"
    HUMAN = "human"


class StateError(ValueError):
    """A status word that is not a known state."""


class IllegalTransition(Exception):
    """The requested move is not allowed."""


S, BOT, PERSON = State, Actor.ORCHESTRATOR, Actor.HUMAN

# (from, to) -> the least privileged actor allowed to make the move. DONE is terminal.
TRANSITIONS: dict[tuple[State, State], Actor] = {
    (S.DRAFT, S.BACKLOG): PERSON,
    (S.DRAFT, S.FAILED): PERSON,
    (S.BACKLOG, S.IN_PROGRESS): BOT,
    (S.BACKLOG, S.BLOCKED): BOT,
    (S.BACKLOG, S.DRAFT): PERSON,
    (S.IN_PROGRESS, S.IN_REVIEW): BOT,
    (S.IN_PROGRESS, S.NEEDS_HUMAN): BOT,
    (S.IN_PROGRESS, S.FAILED): BOT,
    (S.IN_PROGRESS, S.BLOCKED): BOT,
    (S.IN_REVIEW, S.DONE): BOT,
    (S.IN_REVIEW, S.IN_PROGRESS): BOT,
    (S.IN_REVIEW, S.NEEDS_HUMAN): BOT,
    (S.IN_REVIEW, S.FAILED): BOT,
    (S.NEEDS_HUMAN, S.IN_PROGRESS): PERSON,
    (S.NEEDS_HUMAN, S.BACKLOG): PERSON,
    (S.NEEDS_HUMAN, S.DRAFT): PERSON,
    (S.NEEDS_HUMAN, S.FAILED): PERSON,
    (S.NEEDS_HUMAN, S.DONE): PERSON,
    (S.BLOCKED, S.BACKLOG): BOT,
    (S.BLOCKED, S.FAILED): PERSON,
    (S.FAILED, S.BACKLOG): PERSON,
    (S.FAILED, S.DRAFT): PERSON,
}


def parse_state(word: str) -> State:
    """Read a Status cell. BOSS NextGen's `Open` means BACKLOG; case does not matter."""
    cleaned = word.strip().upper()
    if cleaned == "OPEN":
        return State.BACKLOG
    try:
        return State(cleaned)
    except ValueError:
        known = ", ".join(s.value for s in State)
        raise StateError(f"unknown status {word!r} (expected Open or one of: {known})") from None


def check_transition(current: State, target: State, actor: Actor) -> None:
    """Raise IllegalTransition unless `actor` may move a task from `current` to `target`."""
    needed = TRANSITIONS.get((current, target))
    if needed is None:
        raise IllegalTransition(f"{current.value} -> {target.value} is not a legal transition")
    if needed is Actor.HUMAN and actor is not Actor.HUMAN:
        raise IllegalTransition(
            f"{current.value} -> {target.value} needs a human; the orchestrator cannot do it"
        )
