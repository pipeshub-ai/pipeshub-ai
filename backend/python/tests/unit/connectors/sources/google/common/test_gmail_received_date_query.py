"""Tests for build_gmail_received_date_threads_query."""

from app.connectors.core.registry.filters import BooleanOperator, Filter, FilterType
from app.connectors.sources.google.common.gmail_received_date_query import (
    build_gmail_received_date_threads_query,
)


class TestBuildGmailReceivedDateThreadsQuery:
    def test_none_filter(self):
        assert build_gmail_received_date_threads_query(None) is None

    def test_empty_datetime_filter(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {},
            "type": "datetime",
            "operator": "is_after",
        })
        assert build_gmail_received_date_threads_query(f) is None

    def test_is_after(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": 1704067200500, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        assert build_gmail_received_date_threads_query(f) == "after:1704067200"

    def test_is_before_ceil_seconds(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": None, "end": 1999},
            "type": "datetime",
            "operator": "is_before",
        })
        assert build_gmail_received_date_threads_query(f) == "before:2"

    def test_is_between(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": 1000, "end": 2500},
            "type": "datetime",
            "operator": "is_between",
        })
        assert build_gmail_received_date_threads_query(f) == "after:1 before:3"

    def test_is_between_start_only(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": 5000, "end": None},
            "type": "datetime",
            "operator": "is_between",
        })
        assert build_gmail_received_date_threads_query(f) == "after:5"

    def test_is_between_end_only(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": None, "end": 3000},
            "type": "datetime",
            "operator": "is_between",
        })
        assert build_gmail_received_date_threads_query(f) == "before:3"

    def test_is_after_missing_start_returns_none(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": None, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        assert build_gmail_received_date_threads_query(f) is None

    def test_unsupported_operator_returns_none(self):
        f = Filter.model_validate({
            "key": "received_date",
            "value": {"start": 1000, "end": None},
            "type": "datetime",
            "operator": "last_90_days",
        })
        assert build_gmail_received_date_threads_query(f) is None

    def test_wrong_filter_type_returns_none(self):
        f = Filter(
            key="received_date",
            value=True,
            type=FilterType.BOOLEAN,
            operator=BooleanOperator.IS,
        )
        assert build_gmail_received_date_threads_query(f) is None
