"""Unit tests for FilterPredicate evaluator."""
from __future__ import annotations

import pytest
from app.services.events.domain.matching import evaluate_filter, evaluate_predicate, get_nested


def test_get_nested_simple():
    assert get_nested({"a": 1}, "a") == 1

def test_get_nested_dot_path():
    assert get_nested({"channel": {"id": "C123"}}, "channel.id") == "C123"

def test_get_nested_missing():
    assert get_nested({"a": 1}, "b.c") is None

def test_eq_match():
    assert evaluate_predicate("channel.id", "eq", "C123", {"channel": {"id": "C123"}})

def test_eq_no_match():
    assert not evaluate_predicate("channel.id", "eq", "C999", {"channel": {"id": "C123"}})

def test_in_match():
    assert evaluate_predicate("channel.id", "in", ["C123", "C456"], {"channel": {"id": "C456"}})

def test_not_in_match():
    assert evaluate_predicate("user.id", "not_in", ["UBOT"], {"user": {"id": "UHUMAN"}})

def test_contains_match():
    assert evaluate_predicate("text", "contains", "urgent", {"text": "This is URGENT"})

def test_prefix_match():
    assert evaluate_predicate("ref", "prefix", "refs/heads/", {"ref": "refs/heads/main"})

def test_exists_present():
    assert evaluate_predicate("assignee", "exists", None, {"assignee": "alice"})

def test_exists_absent():
    assert not evaluate_predicate("assignee", "exists", None, {"other": "x"})

def test_empty_filter_matches_all():
    assert evaluate_filter([], {"any": "payload"})

def test_conjunction_all_must_match():
    preds = [
        {"field": "channel.id", "op": "eq", "value": "C123"},
        {"field": "user.id", "op": "neq", "value": "UBOT"},
    ]
    assert evaluate_filter(preds, {"channel": {"id": "C123"}, "user": {"id": "UALICE"}})
    assert not evaluate_filter(preds, {"channel": {"id": "C123"}, "user": {"id": "UBOT"}})
