import re

import pytest

from app.scripts.duplicate_records_report import duplicate_query, summarize

_WRITE_WORDS = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|INSERT|UPDATE|REPLACE|UPSERT)\b", re.I)


@pytest.mark.parametrize("data_store", ["neo4j", "arangodb"])
def test_query_is_read_only_and_takes_the_org(data_store: str) -> None:
    query, bind_vars = duplicate_query(data_store, "org-1")
    assert not _WRITE_WORDS.search(query)
    assert bind_vars == {"org_id": "org-1"}


def test_unknown_backend_is_refused() -> None:
    with pytest.raises(ValueError):
        duplicate_query("postgres", None)


def test_summary_counts_the_records_a_cleanup_would_remove() -> None:
    report = summarize(
        [
            {"connectorId": "c1", "pairs": 3, "records": 7},
            {"connectorId": "c2", "pairs": 1, "records": 2},
        ]
    )
    assert report["connectorsWithDuplicates"] == 2
    assert report["duplicatePairs"] == 4
    assert report["extraRecords"] == 5
    assert report["connectors"][0] == {"connectorId": "c1", "pairs": 3, "records": 7, "extraRecords": 4}


def test_no_duplicates_is_an_empty_report() -> None:
    assert summarize([]) == {
        "connectorsWithDuplicates": 0,
        "duplicatePairs": 0,
        "extraRecords": 0,
        "connectors": [],
    }
