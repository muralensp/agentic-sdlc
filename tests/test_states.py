"""The task state machine (ADLC-005)."""

import itertools

import pytest

from adlc.states import (
    TRANSITIONS,
    Actor,
    IllegalTransition,
    State,
    StateError,
    check_transition,
    parse_state,
)

S = State
BOT, PERSON = Actor.ORCHESTRATOR, Actor.HUMAN

# The table from the grilling, written out independently of the implementation.
EXPECTED = {
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


def test_the_table_is_exactly_the_agreed_one():
    assert TRANSITIONS == EXPECTED
    assert len(EXPECTED) == 22


@pytest.mark.parametrize(("edge", "actor"), sorted(EXPECTED.items(), key=str))
def test_every_legal_transition_is_accepted_for_the_right_actor(edge, actor):
    check_transition(*edge, Actor.HUMAN)  # a person may do any legal move
    if actor is BOT:
        check_transition(*edge, Actor.ORCHESTRATOR)


@pytest.mark.parametrize("edge", sorted((e for e, a in EXPECTED.items() if a is PERSON), key=str))
def test_human_only_transitions_are_refused_to_the_orchestrator(edge):
    with pytest.raises(IllegalTransition, match="human"):
        check_transition(*edge, Actor.ORCHESTRATOR)


@pytest.mark.parametrize(
    ("current", "target"),
    [p for p in itertools.product(State, State) if p not in EXPECTED],
)
@pytest.mark.parametrize("actor", list(Actor))
def test_every_other_pair_is_illegal(current, target, actor):
    with pytest.raises(IllegalTransition):
        check_transition(current, target, actor)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.DONE, S.IN_PROGRESS),
        (S.DONE, S.BACKLOG),
        (S.BACKLOG, S.DONE),
        (S.FAILED, S.IN_PROGRESS),
        (S.IN_PROGRESS, S.IN_PROGRESS),
    ],
)
def test_named_examples_from_the_brief_are_illegal(current, target):
    with pytest.raises(IllegalTransition, match=f"{current.value}"):
        check_transition(current, target, Actor.HUMAN)


def test_done_is_terminal():
    assert not [e for e in TRANSITIONS if e[0] is S.DONE]


@pytest.mark.parametrize(
    ("word", "state"),
    [
        ("Open", S.BACKLOG),
        ("open", S.BACKLOG),
        ("BACKLOG", S.BACKLOG),
        (" in_progress ", S.IN_PROGRESS),
        ("Needs_Human", S.NEEDS_HUMAN),
        ("DONE", S.DONE),
        ("failed", S.FAILED),
        ("draft", S.DRAFT),
    ],
)
def test_parse_state_reads_canonical_names_and_boss_open(word, state):
    assert parse_state(word) is state


@pytest.mark.parametrize("word", ["", "Closed", "In Progress", "READY", "PAUSED"])
def test_unknown_status_words_are_rejected(word):
    with pytest.raises(StateError, match="status"):
        parse_state(word)
