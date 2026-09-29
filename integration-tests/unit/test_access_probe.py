"""The access probes never read a failure as "no access".

The "cannot see it" checks in the permission suites pass when a probe says the
record stayed out of reach. If a server error or an expired login counted as
that, a broken stack would pass every one of them.
"""

from __future__ import annotations

import pytest

from helper.access_probe import ChatAnswer, SearchOutcome, _cited_ids, chat_refused_or_uncited

pytestmark = pytest.mark.unit

RECORD, VIRTUAL = "rec-1", "vrec-1"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (SearchOutcome(200, ("other",)), True),
        (SearchOutcome(200, ()), True),
        (SearchOutcome(404, ()), True),
        (SearchOutcome(403, ()), True),
        (SearchOutcome(200, (VIRTUAL,)), False),
        (SearchOutcome(401, ()), False),
        (SearchOutcome(500, ()), False),
    ],
)
def test_search_counts_only_real_answers_as_out_of_reach(outcome: SearchOutcome, expected: bool) -> None:
    assert outcome.refused_or_missing(VIRTUAL) is expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (ChatAnswer(status=200, finished=True), True),
        (ChatAnswer(status=200, error="no documents"), True),
        (ChatAnswer(status=404), True),
        (ChatAnswer(status=200, finished=True, cited_record_ids={RECORD}), False),
        (ChatAnswer(status=200, finished=True, cited_virtual_ids={VIRTUAL}), False),
        # A stream that stopped without finishing or failing proves nothing.
        (ChatAnswer(status=200), False),
        (ChatAnswer(status=500), False),
        (ChatAnswer(status=401), False),
    ],
)
def test_chat_counts_only_real_answers_as_out_of_reach(answer: ChatAnswer, expected: bool) -> None:
    assert chat_refused_or_uncited(answer, RECORD, VIRTUAL) is expected


def test_citations_are_read_in_either_shape() -> None:
    records, virtuals = _cited_ids([
        {"citationData": {"metadata": {"recordId": "a", "virtualRecordId": "va"}}},
        {"recordId": "b"},
        {"citation": {"metadata": {"virtualRecordId": "vc"}}},
    ])
    assert records == {"a", "b"}
    assert virtuals == {"va", "vc"}
