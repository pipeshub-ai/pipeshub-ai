"""R31CAPFIX-FU-1: a knowledge check does not start before the seeded pages answer a search.

The journey gate's `knowledge` lines were red on one fresh stack and green on the next (R31-SECKEY's validation at
main-sync 1b05238b3f): the lines read the travel policy's text, and a record can read COMPLETED before its chunks answer
a search. The smoke and the gate now wait on the product's own search returning the page, never on a sleep. Node and
the stack are fakes here: what the signal decides from what it reads, and that the gate stops on it, are under test.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))

smoke = importlib.import_module("smoke_human")
journeys = importlib.import_module("journeys")
from lane import HttpAnswer, StackError  # noqa: E402

if TYPE_CHECKING:
    import pytest

PAGE = "# Travel policy\n\nBook flights at least 14 days ahead."


class _Api:
    def __init__(self, statuses: list[str], hits: list[str], *, search_status: int = 200) -> None:
        self.statuses, self.hits, self.search_status = statuses, hits, search_status
        self.searches: list[Any] = []

    def request(self, method: str, path: str, body: Any = None) -> HttpAnswer:  # noqa: ANN401
        if path.startswith("/api/v1/knowledgeBase/record/"):
            return HttpAnswer(200, {"record": {"indexingStatus": self.statuses[0]}}, {})
        assert (method, path) == ("POST", "/api/v1/search")
        self.searches.append(body)
        return HttpAnswer(self.search_status, {"searchResponse": {"searchResults": [{"content": h} for h in self.hits]}},
                          {})


def _stack(api: _Api) -> SimpleNamespace:
    return SimpleNamespace(api=api, seed={"kbTeamRecords": ["r1", "r2"], "kbAdminRecords": ["r3"]})


def test_kr_1_a_record_still_indexing_is_not_searchable() -> None:
    assert smoke.knowledge_state(_stack(_Api(["IN_PROGRESS"], [PAGE]))) == "record r1 indexing IN_PROGRESS"


def test_kr_2_indexed_records_whose_search_misses_the_page_are_not_searchable() -> None:
    api = _Api(["COMPLETED"], ["# Security\n\nReport incidents within 15 minutes."])
    assert smoke.knowledge_state(_stack(api)) == "search does not return the travel policy yet"
    # The probe asks no model and leaves nothing in the admin's search history.
    assert api.searches == [{"query": "travel policy", "limit": 10, "rewriteQuery": False, "saveHistory": False}]


def test_kr_3_the_page_in_the_search_is_searchable() -> None:
    assert smoke.knowledge_state(_stack(_Api(["COMPLETED"], [PAGE]))) == "searchable"


def test_kr_4_a_refused_search_or_no_answer_says_so() -> None:
    assert smoke.knowledge_state(_stack(_Api(["COMPLETED"], [PAGE], search_status=503))) == "search answered 503"

    class _Down:
        def request(self, *_: Any) -> HttpAnswer:  # noqa: ANN401
            raise StackError("connection refused")

    assert smoke.knowledge_state(SimpleNamespace(api=_Down(), seed={"kbTeamRecords": ["r1"]})).startswith("no answer")


def test_kr_5_the_wait_ends_on_the_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api(["COMPLETED"], [])
    polls: list[str] = []

    def until(predicate: Any, **_: Any) -> None:  # noqa: ANN401
        for _ in range(5):
            if predicate():
                return
            polls.append("miss")
            api.hits = [PAGE]
        raise StackError("timed out")

    monkeypatch.setattr(smoke, "wait_until", until)
    assert smoke.wait_knowledge_searchable(_stack(api)) == "searchable"
    assert polls == ["miss"]


def test_kr_6_the_gate_does_not_start_before_the_knowledge_is_searchable(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    started: list[str] = []
    fake_sh = SimpleNamespace(wait_capabilities_settled=lambda stack: "settled",
                              wait_knowledge_searchable=lambda stack: "record r1 indexing QUEUED")
    fake_stack = SimpleNamespace(attach=lambda run, stack_id: SimpleNamespace())

    def _import(run_dir: Path) -> None:
        journeys.sh, journeys.Stack = fake_sh, fake_stack

    monkeypatch.setattr(journeys, "_import", _import)
    monkeypatch.setattr(journeys, "apply_owner_shape", lambda *a: started.append("shape"))
    monkeypatch.setattr(journeys, "run_journey", lambda *a: started.append("journey") or [])
    assert journeys.main([str(tmp_path / "run")]) == 2
    assert started == []
    assert "not searchable: record r1 indexing QUEUED" in capsys.readouterr().err
