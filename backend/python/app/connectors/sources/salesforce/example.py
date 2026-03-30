import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from dotenv import load_dotenv

from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.sources.client.salesforce.salesforce import SalesforceResponse

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(records: list, done: bool, next_url: str | None = None) -> SalesforceResponse:
    """Build a SalesforceResponse that looks like a real Salesforce query page."""
    data = {
        "done": done,
        "totalSize": len(records),
        "records": records,
    }
    if next_url:
        data["nextRecordsUrl"] = next_url
    return SalesforceResponse(success=True, data=data)


def _make_error(message: str) -> SalesforceResponse:
    return SalesforceResponse(success=False, data=None, error=message)


def _records(prefix: str, count: int) -> list:
    return [{"Id": f"{prefix}{i:04d}", "Name": f"Record {prefix}{i}"} for i in range(count)]


def _build_connector(logger) -> SalesforceConnector:
    """Build a SalesforceConnector with all dependencies mocked."""
    data_entities_processor = AsyncMock()
    data_entities_processor.org_id = "test-org-123"

    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    data_store_provider.transaction = MagicMock(return_value=mock_transaction)

    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "authType": "OAUTH",
        "apiVersion": "59.0",
        "credentials": {},
    })

    connector = SalesforceConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id="test-salesforce-connector",
    )

    # Inject mock data source directly — bypasses init() and OAuth entirely
    connector.data_source = MagicMock()
    connector.salesforce_instance_url = "https://test.salesforce.com"

    return connector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_single_page(connector: SalesforceConnector, logger) -> None:
    """done=True on first response — soql_query_next must never be called."""
    connector.data_source.soql_query = AsyncMock(return_value=_make_page(
        records=_records("A", 5),
        done=True,
    ))
    connector.data_source.soql_query_next = AsyncMock()

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Contact")

    assert result.success, f"Expected success, got: {result.error}"
    assert len(result.data["records"]) == 5
    connector.data_source.soql_query_next.assert_not_called()
    logger.info(f"✔ test_single_page — {len(result.data['records'])} records, soql_query_next never called")


async def test_two_pages(connector: SalesforceConnector, logger) -> None:
    """done=False on page 1 → follows nextRecordsUrl → done=True on page 2."""
    page1 = _make_page(_records("B", 10), done=False, next_url="/services/data/v59.0/query/01gPAGE2")
    page2 = _make_page(_records("C", 7),  done=True)

    connector.data_source.soql_query = AsyncMock(return_value=page1)
    connector.data_source.soql_query_next = AsyncMock(return_value=page2)

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Contact")

    assert result.success
    assert len(result.data["records"]) == 17
    connector.data_source.soql_query_next.assert_called_once_with(
        next_url="/services/data/v59.0/query/01gPAGE2"
    )
    logger.info(f"✔ test_two_pages — {len(result.data['records'])} records across 2 pages")


async def test_three_pages(connector: SalesforceConnector, logger) -> None:
    """Verifies chaining across 3 pages with the correct nextRecordsUrl each time."""
    page1 = _make_page(_records("D", 10), done=False, next_url="/services/data/v59.0/query/01gPAGE2")
    page2 = _make_page(_records("E", 10), done=False, next_url="/services/data/v59.0/query/01gPAGE3")
    page3 = _make_page(_records("F", 3),  done=True)

    connector.data_source.soql_query = AsyncMock(return_value=page1)
    connector.data_source.soql_query_next = AsyncMock(side_effect=[page2, page3])

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Account")

    assert result.success
    assert len(result.data["records"]) == 23
    assert connector.data_source.soql_query_next.call_count == 2
    logger.info(f"✔ test_three_pages — {len(result.data['records'])} records across 3 pages")


async def test_first_page_failure(connector: SalesforceConnector, logger) -> None:
    """soql_query fails on the first call — error must propagate, soql_query_next never called."""
    connector.data_source.soql_query = AsyncMock(return_value=_make_error("INVALID_SESSION_ID"))
    connector.data_source.soql_query_next = AsyncMock()

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Lead")

    assert not result.success, "Expected failure to propagate"
    assert "INVALID_SESSION_ID" in (result.error or "")
    connector.data_source.soql_query_next.assert_not_called()
    logger.info(f"✔ test_first_page_failure — error propagated correctly: {result.error}")


async def test_mid_pagination_failure(connector: SalesforceConnector, logger) -> None:
    """soql_query_next fails on page 2 — should return partial records collected so far."""
    page1 = _make_page(_records("G", 10), done=False, next_url="/services/data/v59.0/query/01gPAGE2")

    connector.data_source.soql_query = AsyncMock(return_value=page1)
    connector.data_source.soql_query_next = AsyncMock(return_value=_make_error("REQUEST_LIMIT_EXCEEDED"))

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Opportunity")

    assert result.success, "Expected partial success, not hard failure"
    assert len(result.data["records"]) == 10
    logger.info(f"✔ test_mid_pagination_failure — {len(result.data['records'])} partial records returned")


async def test_data_source_not_initialized(connector: SalesforceConnector, logger) -> None:
    """data_source=None — should return a clean error, not raise an exception."""
    connector.data_source = None

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Case")

    assert not result.success
    assert result.error == "Data source not initialized"
    logger.info(f"✔ test_data_source_not_initialized — clean error returned: {result.error}")


async def test_empty_result(connector: SalesforceConnector, logger) -> None:
    """Query returns zero records — should succeed with an empty list."""
    connector.data_source.soql_query = AsyncMock(return_value=_make_page([], done=True))
    connector.data_source.soql_query_next = AsyncMock()

    result = await connector._soql_query_paginated(api_version="59.0", q="SELECT Id FROM Task WHERE Subject = 'NOPE'")

    assert result.success
    assert result.data["records"] == []
    connector.data_source.soql_query_next.assert_not_called()
    logger.info("✔ test_empty_result — empty result handled correctly")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_tests() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    tests = [
        test_single_page,
        test_two_pages,
        test_three_pages,
        test_first_page_failure,
        test_mid_pagination_failure,
        test_data_source_not_initialized,
        test_empty_result,
    ]

    passed = 0
    failed = 0

    print("=" * 60)
    print("Salesforce Pagination Test")
    print("=" * 60)

    for test_fn in tests:
        # Fresh connector + fresh mock data_source for every test
        connector = _build_connector(logger)
        try:
            await test_fn(connector, logger)
            passed += 1
        except AssertionError as e:
            logger.error(f"✘ {test_fn.__name__} — FAIL: {e}")
            failed += 1
        except Exception as e:
            logger.error(f"✘ {test_fn.__name__} — ERROR: {e}", exc_info=True)
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run_tests())
