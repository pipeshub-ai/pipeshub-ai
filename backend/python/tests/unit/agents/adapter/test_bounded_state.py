"""Unit tests for app.agents.agent_loop.bounded_state."""

import pytest

from app.agents.agent_loop.bounded_state import (
    BoundedDict,
    BoundedList,
    bounded_append,
    cap_dict,
    cap_list,
)


class TestBoundedList:
    def test_rejects_non_positive_maxlen(self):
        with pytest.raises(ValueError):
            BoundedList(maxlen=0)

    def test_seeded_iterable_over_maxlen_is_trimmed_on_init(self):
        lst = BoundedList([1, 2, 3, 4, 5], maxlen=3)
        assert list(lst) == [3, 4, 5]

    def test_append_within_maxlen_keeps_everything(self):
        lst = BoundedList(maxlen=3)
        lst.append(1)
        lst.append(2)
        assert list(lst) == [1, 2]

    def test_append_past_maxlen_evicts_oldest_first(self):
        lst = BoundedList(maxlen=3)
        for i in range(5):
            lst.append(i)
        assert list(lst) == [2, 3, 4]

    def test_extend_past_maxlen_evicts_from_front(self):
        lst = BoundedList([1, 2], maxlen=3)
        lst.extend([3, 4, 5])
        assert list(lst) == [3, 4, 5]

    def test_len_and_indexing_behave_like_plain_list(self):
        lst = BoundedList(maxlen=3)
        lst.extend([1, 2, 3])
        assert len(lst) == 3
        assert lst[0] == 1
        assert lst[-1] == 3

    def test_maxlen_of_one_keeps_only_latest(self):
        lst = BoundedList(maxlen=1)
        lst.append(1)
        lst.append(2)
        assert list(lst) == [2]


class TestBoundedDict:
    def test_rejects_non_positive_maxsize(self):
        with pytest.raises(ValueError):
            BoundedDict(maxsize=0)

    def test_setitem_within_maxsize_keeps_everything(self):
        d = BoundedDict(maxsize=3)
        d["a"] = 1
        d["b"] = 2
        assert dict(d) == {"a": 1, "b": 2}

    def test_setitem_past_maxsize_evicts_oldest_inserted_key(self):
        d = BoundedDict(maxsize=2)
        d["a"] = 1
        d["b"] = 2
        d["c"] = 3
        assert dict(d) == {"b": 2, "c": 3}

    def test_update_past_maxsize_evicts_oldest_keys(self):
        d = BoundedDict({"a": 1}, maxsize=2)
        d.update({"b": 2, "c": 3})
        assert dict(d) == {"b": 2, "c": 3}

    def test_overwriting_existing_key_does_not_evict(self):
        """Re-setting an existing key must not count as new growth."""
        d = BoundedDict({"a": 1, "b": 2}, maxsize=2)
        d["a"] = 100
        assert dict(d) == {"a": 100, "b": 2}


class TestBoundedAppend:
    def test_creates_list_on_first_call(self):
        container: dict = {}
        bounded_append(container, "results", "x", maxlen=5)
        assert container["results"] == ["x"]

    def test_appends_to_existing_plain_list(self):
        container = {"results": [1, 2]}
        bounded_append(container, "results", 3, maxlen=5)
        assert container["results"] == [1, 2, 3]

    def test_evicts_front_past_maxlen(self):
        container: dict = {"results": [1, 2, 3]}
        bounded_append(container, "results", 4, maxlen=3)
        assert container["results"] == [2, 3, 4]

    def test_works_on_a_seeded_chat_state_style_plain_list(self):
        """The whole point of `bounded_append` over `BoundedList` -- the
        caller doesn't control how `container[key]` was first constructed."""
        container = {"results": list(range(10))}
        bounded_append(container, "results", 10, maxlen=5)
        assert container["results"] == [6, 7, 8, 9, 10]


class TestCapList:
    def test_returns_same_object_when_under_limit(self):
        items = [1, 2, 3]
        assert cap_list(items, 5) is items

    def test_truncates_to_head_when_over_limit(self):
        items = list(range(10))
        assert cap_list(items, 3) == [0, 1, 2]

    def test_exact_boundary_is_not_truncated(self):
        items = [1, 2, 3]
        assert cap_list(items, 3) == [1, 2, 3]


class TestCapDict:
    def test_returns_same_object_when_under_limit(self):
        items = {"a": 1, "b": 2}
        assert cap_dict(items, 5) is items

    def test_truncates_to_tail_of_insertion_order_when_over_limit(self):
        items = {"a": 1, "b": 2, "c": 3, "d": 4}
        assert cap_dict(items, 2) == {"c": 3, "d": 4}

    def test_exact_boundary_is_not_truncated(self):
        items = {"a": 1, "b": 2}
        assert cap_dict(items, 2) == {"a": 1, "b": 2}
