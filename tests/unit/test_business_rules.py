import itertools

import pytest

from app.services.rules import DomainError, operation_key, penalty_points, rating_points, transition

STATES = ("BACKLOG", "TODO", "IN_PROGRESS", "DONE", "CANCELLED")
ALLOWED = {
    "BACKLOG": {"CANCELLED"},
    "TODO": {"IN_PROGRESS", "DONE", "CANCELLED"},
    "IN_PROGRESS": {"TODO", "DONE", "CANCELLED"},
    "DONE": {"TODO", "IN_PROGRESS", "CANCELLED"},
    "CANCELLED": set(),
}


@pytest.mark.parametrize(
    "old,new,confirmed", list(itertools.product(STATES, STATES, (False, True)))
)
def test_complete_transition_matrix(old, new, confirmed):
    valid = new in ALLOWED[old] and (confirmed or (old != "DONE" and new != "CANCELLED"))
    if valid:
        transition(old, new, confirmed)
    else:
        with pytest.raises(DomainError):
            transition(old, new, confirmed)


@pytest.mark.parametrize("value", [0, -1, 10001, True, False, 1.0, "500", None])
def test_rating_strict_validation(value):
    with pytest.raises(DomainError):
        rating_points(value)


@pytest.mark.parametrize("value", [1, 10000])
def test_rating_boundaries(value):
    assert rating_points(value) == value


@pytest.mark.parametrize(
    "method,rate,quantity,manual",
    [
        ("PER_MINUTE", 100, 0, None),
        ("PER_MINUTE", 100, 10001, None),
        ("PER_MINUTE", 0, 1, None),
        ("PER_MINUTE", 1000000, 10000, None),
        ("PER_MINUTE", 100, 5, 1),
        ("PER_MINUTE", True, 5, None),
        ("PER_MINUTE", 100, True, None),
        ("FIXED", 0, None, None),
        ("FIXED", 100, 1, None),
        ("FIXED", 100, None, 1),
        ("MANUAL", 0, None, 0),
        ("MANUAL", 0, None, 1000000001),
        ("MANUAL", 0, None, True),
        ("MANUAL", 0, 1, 10),
        ("UNKNOWN", 1, None, None),
        ("MANUAL", -1, None, 10),
        ("FIXED", 1000001, None, None),
        ("FIXED", 1.0, None, None),
    ],
)
def test_invalid_penalty_inputs(method, rate, quantity, manual):
    with pytest.raises(DomainError):
        penalty_points(method, rate, quantity, manual)


@pytest.mark.parametrize("key", ["", " ", "x" * 81, None, True, 1])
def test_invalid_operation_keys(key):
    with pytest.raises(DomainError):
        operation_key(key)
