"""ServiceNow sync survives rate limits, retries and expired tokens, reads in fewer calls, and pages
by X-Total-Count so ACL-hidden rows never end a listing early.

Listings run against ``FakeServiceNow``. The HTTP-level tests send through the real client, datasource
and resilient transport, with only the network replaced.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from servicenow_fakes import (
    INSTANCE,
    T0,
    FakeServiceNow,
    MemorySyncPoint,
    article_row,
    attachment_row,
    file_response,
    migrated_sync_point,
    status_error,
    wire_listing,
    written_ids,
)

from app.connectors.sources.servicenow.servicenow import connector as connector_module
from app.models.permission import PermissionType
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.servicenow.servicenow import (
    ServiceNowRateLimited,
    ServiceNowRateLimitPolicy,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
)
from app.sources.external.servicenow.models import (
    ServiceNowAPIError,
    ServiceNowAuthError,
    TableAPIResponse,
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource

RULE = "dee70c609354df10ff9376718bba104f"
# The 429 a ServiceNow rate limit rule returned on the test instance (3 requests per hour, kb_knowledge).
RATE_LIMIT_429 = {
    "headers": {
        "Retry-After": "1731",
        "X-RateLimit-Rule": RULE,
        "X-RateLimit-Limit": "3",
        "X-RateLimit-Reset": "1793268000",
        "Connection": "close",
    },
    "body": {"error": {"message": "Rate limit exceeded",
                       "detail": "Rate limit of 3 requests per hour for Table API exceeded"}, "status": "failure"},
}


def _ok(rows: list[dict], total: int | None = None) -> httpx.Response:
    return httpx.Response(200, headers={"X-Total-Count": str(len(rows) if total is None else total)},
                          json={"result": rows})


def _rate_limited(**header_changes: str | None) -> httpx.Response:
    headers = {k: v for k, v in {**RATE_LIMIT_429["headers"], **header_changes}.items() if v is not None}
    return httpx.Response(429, headers=headers, json=RATE_LIMIT_429["body"])


def _policy() -> ServiceNowRateLimitPolicy:
    return ServiceNowRateLimitPolicy(rate_limit=1000, max_retries=3, base_delay=0.0, max_delay=60.0)


def _datasource(outcomes: list, monkeypatch, policy: ServiceNowRateLimitPolicy | None) -> tuple[ServiceNowDataSource, list]:
    """The real client and datasource; only the network send is scripted (the last outcome repeats)."""
    client = ServiceNowRESTClientViaOAuthAuthorizationCode(
        INSTANCE, "client-id", "client-secret", "https://pipeshub.example/callback",
        access_token="token", resilience=policy,
    )
    sent: list[httpx.Request] = []

    async def send(_transport: object, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    if policy is None:
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: send(None, r)), headers=client.headers)
    else:
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
        client.client = httpx.AsyncClient(transport=ResilientHTTPTransport(policy), headers=client.headers)
    return ServiceNowDataSource(client), sent


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Backoff waits, recorded and skipped (the gate they arm is opened again)."""
    waits: list[float] = []

    async def instant(delay: float, *_: object) -> None:
        waits.append(delay)

    monkeypatch.setattr("app.sources.client.http.http_resilient_transport.asyncio.sleep", instant)
    monkeypatch.setattr(ServiceNowRateLimitPolicy, "pause", lambda self, seconds: None)
    return waits


async def _list(ds: ServiceNowDataSource, table: str = "kb_knowledge") -> TableAPIResponse:
    return await ds.get_now_table_tableName(tableName=table, sysparm_query="active=true", sysparm_limit="100")


# --- retries of 429, 5xx and timeouts --------------------------------------------------------


def test_connector_declares_resilience(servicenow_connector) -> None:
    policy = servicenow_connector.resilience

    assert isinstance(policy, ServiceNowRateLimitPolicy)
    assert (policy.rate_limit, policy.max_retries, policy.base_delay, policy.max_delay) == (10, 3, 1.0, 60.0)
    assert servicenow_connector.resilience is policy  # built once: a re-init keeps an armed stop


@pytest.mark.asyncio
async def test_429_retry_after_2_retried_once(monkeypatch, slept) -> None:
    ds, sent = _datasource([_rate_limited(**{"Retry-After": "2"}), _ok([{"sys_id": "a1"}])], monkeypatch, _policy())

    response = await _list(ds)

    assert [r.sys_id for r in response.result] == ["a1"]
    assert len(sent) == 2
    assert slept == [pytest.approx(2.0)]


@pytest.mark.asyncio
async def test_503_retried(monkeypatch, slept) -> None:
    ds, sent = _datasource([httpx.Response(503), _ok([{"sys_id": "a1"}])], monkeypatch, _policy())

    assert len((await _list(ds)).result) == 1
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_timeout_retried(monkeypatch, slept) -> None:
    ds, sent = _datasource([httpx.ReadTimeout("slow"), _ok([{"sys_id": "a1"}])], monkeypatch, _policy())

    assert len((await _list(ds)).result) == 1
    assert len(sent) == 2


# --- an hourly rate limit stops the run -------------------------------------------------------


@pytest.mark.asyncio
async def test_no_request_after_rate_limit_stop(monkeypatch, slept) -> None:
    policy = _policy()
    ds, sent = _datasource([_rate_limited(), _ok([])], monkeypatch, policy)

    with pytest.raises(ServiceNowRateLimited) as stop:
        await _list(ds)
    with pytest.raises(ServiceNowRateLimited):
        await _list(ds, "kb_knowledge_base")

    assert len(sent) == 1, "no retry into the spent hour, and nothing sent after it"
    assert slept == []
    assert stop.value.reset_at == pytest.approx(time.time() + 1731, abs=5)
    assert stop.value.rule == RULE
    assert "UTC" in str(stop.value)


@pytest.mark.asyncio
async def test_reset_header_used_when_retry_after_missing(monkeypatch, slept) -> None:
    reset = int(time.time()) + 1800
    ds, sent = _datasource([_rate_limited(**{"Retry-After": None, "X-RateLimit-Reset": str(reset)})],
                           monkeypatch, _policy())

    with pytest.raises(ServiceNowRateLimited) as stop:
        await _list(ds)

    assert stop.value.reset_at == pytest.approx(reset, abs=2)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_retry_after_over_cap_stops_without_advancing_checkpoint(servicenow_connector, monkeypatch, slept) -> None:
    sc = servicenow_connector
    # a page of articles, then the page's attachment listing hits the hourly limit
    ds, sent = _datasource([_ok([article_row(1, "2024-06-01 10:00:00")]), _rate_limited()], monkeypatch, _policy())
    wire_listing(sc, FakeServiceNow(), checkpoint=T0)
    sc._get_fresh_datasource = AsyncMock(return_value=ds)

    with pytest.raises(ServiceNowRateLimited):
        await sc._sync_articles()

    assert sc.article_sync_point.data["articles"]["last_sync_time"] == T0
    sc.data_entities_processor.on_new_records.assert_not_awaited()
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_rate_limit_inside_article_grants_stops_the_run(servicenow_connector) -> None:
    # The criteria read wraps errors in _CriteriaUnreadable; the stop must still end the run, not one article.
    fake = FakeServiceNow(
        {"kb_knowledge": [article_row(1, can_read_user_criteria="uc1"), article_row(2)]},
        fail=lambda table, q: ServiceNowRateLimited(time.time() + 600, RULE) if table == "user_criteria" else None,
    )
    wire_listing(servicenow_connector, fake)

    with pytest.raises(ServiceNowRateLimited):
        await servicenow_connector._sync_articles()

    servicenow_connector.data_entities_processor.on_new_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_in_kb_criteria_stops_the_run(servicenow_connector) -> None:
    fake = FakeServiceNow(
        {"kb_knowledge_base": [{"sys_id": "kb1", "title": "KB", "sys_updated_on": T0}],
         "kb_uc_can_read_mtom": [{"sys_id": "m1", "kb_knowledge_base": "kb1", "user_criteria": "uc1", "sys_updated_on": T0}]},
        fail=lambda table, q: ServiceNowRateLimited(time.time() + 600) if table == "user_criteria" else None,
    )
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)
    servicenow_connector.data_entities_processor.note_kept_record_group = AsyncMock()

    with pytest.raises(ServiceNowRateLimited):
        await servicenow_connector._sync_knowledge_bases([])

    servicenow_connector.data_entities_processor.note_kept_record_group.assert_not_awaited()


# --- a 401 refreshes the token once ----------------------------------------------------------


def _token_wiring(sc, monkeypatch, *, always_401: bool = False) -> tuple[FakeServiceNow, AsyncMock, dict]:
    """A connector whose stored token is "old" until refresh_now rotates it; the fake answers 401 to it."""
    creds = {"access_token": "old", "refresh_token": "rt-old"}
    sc.config_service.get_config = AsyncMock(side_effect=lambda *a, **k: {"credentials": dict(creds)})
    sc.servicenow_client = ServiceNowRESTClientViaOAuthAuthorizationCode(
        INSTANCE, "client-id", "client-secret", "https://pipeshub.example/callback", access_token="old")
    client = sc.servicenow_client

    async def rotate(connector_id: str, connector_type: str, refresh_token: str) -> SimpleNamespace:
        creds.update(access_token="new", refresh_token="rt-new")
        return SimpleNamespace(access_token="new", refresh_token="rt-new")

    refresh_now = AsyncMock(side_effect=rotate)
    monkeypatch.setattr(connector_module.startup_service, "get_token_refresh_service",
                        lambda: SimpleNamespace(refresh_now=refresh_now))
    fake = FakeServiceNow(
        {"kb_knowledge": [article_row(1)]},
        fail=lambda table, q: status_error(401, "User is not authenticated")
        if always_401 or client.access_token == "old" else None,
    )
    monkeypatch.setattr(connector_module, "ServiceNowDataSource", lambda _client: fake)
    sc.instance_url = INSTANCE
    sc.article_sync_point = migrated_sync_point()
    return fake, refresh_now, creds


@pytest.mark.asyncio
async def test_401_refreshes_once_and_continues(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    fake, refresh_now, _ = _token_wiring(sc, monkeypatch)

    await sc._sync_articles()

    refresh_now.assert_awaited_once_with("sn-conn-1", "SERVICENOW", "rt-old")  # the refresh token held before the call
    assert sc.servicenow_client.access_token == "new"
    assert sc.servicenow_client.headers["Authorization"] == "Bearer new"
    assert written_ids(sc) == ["KB0000001"]


@pytest.mark.asyncio
async def test_401_adopts_a_token_stored_meanwhile(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    fake, refresh_now, creds = _token_wiring(sc, monkeypatch)
    real_fail = fake.fail

    def background_refresh_lands(table: str, query: str | None) -> Exception | None:
        error = real_fail(table, query)
        if error:
            creds.update(access_token="rotated", refresh_token="rt-rotated")  # written by the background refresher
        return error

    fake.fail = background_refresh_lands

    await sc._sync_articles()

    refresh_now.assert_not_awaited()
    assert sc.servicenow_client.access_token == "rotated"
    assert written_ids(sc) == ["KB0000001"]


@pytest.mark.asyncio
async def test_second_401_raises_auth_error_and_unlinks_nothing(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    fake, refresh_now, _ = _token_wiring(sc, monkeypatch, always_401=True)

    with pytest.raises(ServiceNowAuthError, match="User is not authenticated"):
        await sc._sync_articles()

    refresh_now.assert_awaited_once()
    sc.data_entities_processor.on_new_records.assert_not_awaited()
    assert "articles" not in sc.article_sync_point.data


@pytest.mark.asyncio
async def test_401_in_the_attachment_listing_is_not_a_failed_page(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    fake, _, _ = _token_wiring(sc, monkeypatch)
    fake.fail = lambda table, q: status_error(401) if table == "sys_attachment" else None

    with pytest.raises(ServiceNowAuthError):
        await sc._sync_articles()


@pytest.mark.asyncio
async def test_stream_401_refreshes_once(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    _, refresh_now, _ = _token_wiring(sc, monkeypatch)
    client = sc.servicenow_client

    async def answer(_attachment_id: str) -> httpx.Response:
        if client.access_token == "old":
            raise status_error(401)
        return file_response(b"file bytes")

    download = AsyncMock(side_effect=answer)
    monkeypatch.setattr(connector_module, "ServiceNowDataSource", lambda _client: SimpleNamespace(stream_attachment=download))

    assert b"".join([c async for c in await sc._open_attachment("att1")]) == b"file bytes"
    refresh_now.assert_awaited_once()
    assert download.await_count == 2


@pytest.mark.asyncio
async def test_401_without_a_refresh_token_raises_auth_error(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    _, refresh_now, creds = _token_wiring(sc, monkeypatch)
    creds.pop("refresh_token")

    with pytest.raises(ServiceNowAuthError):
        await sc._table_get("kb_knowledge")

    refresh_now.assert_not_awaited()


# --- the vendor's reason is kept, the body is redacted in the log ----------------------------


@pytest.mark.asyncio
async def test_api_error_carries_vendor_message(monkeypatch, caplog) -> None:
    body = {"error": {"message": "User is not authenticated",
                      "detail": "Required to provide Auth information, see https://x.example/a?access_token=s3cr3t"},
            "status": "failure"}
    ds, _ = _datasource([httpx.Response(401, json=body)], monkeypatch, None)

    with pytest.raises(ServiceNowAPIError) as error:
        await _list(ds)

    assert error.value.status_code == 401
    assert error.value.message.startswith("User is not authenticated: Required to provide Auth information")
    assert "s3cr3t" not in caplog.text


@pytest.mark.asyncio
async def test_api_error_without_json_body_keeps_the_status(monkeypatch) -> None:
    ds, _ = _datasource([httpx.Response(502, text="<html>Bad gateway</html>")], monkeypatch, None)

    with pytest.raises(ServiceNowAPIError, match="HTTP 502"):
        await _list(ds)


# --- the HTTP pool is closed ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_twice_then_cleanup_closes_both_clients(servicenow_connector) -> None:
    sc = servicenow_connector
    sc._fetch_oauth_config_by_id = AsyncMock(return_value={
        "config": {"instanceUrl": INSTANCE, "clientId": "id", "clientSecret": "secret"},
        "redirectUri": "https://pipeshub.example/callback",
    })
    clients = []
    with patch.object(sc, "test_connection_and_access", AsyncMock(return_value=True)), \
         patch.object(sc, "_resolve_kb_portal_suffix", AsyncMock(return_value="kb")), \
         patch.object(sc, "_resolve_article_read_criteria", AsyncMock(return_value=False)):
        for _ in range(2):
            assert await sc.init() is True
            clients.append(sc.servicenow_client)
            sc.servicenow_client.close = AsyncMock()

    clients[0].close.assert_awaited_once()  # by the second init
    clients[1].close.assert_not_awaited()
    assert clients[1].resilience is sc.resilience

    await sc.cleanup()

    clients[1].close.assert_awaited_once()
    assert sc.servicenow_client is None and sc.servicenow_datasource is None


# --- fewer calls per page and per run ---------------------------------------------------------


def _hundred_articles(updated: str = T0) -> tuple[list[dict], FakeServiceNow]:
    # 32-character sys_ids, as on an instance: they decide how many fit in one IN list
    articles = [article_row(i, updated, sys_id=f"{i:032x}", can_read_user_criteria="ucA" if i % 2 else "ucB")
                for i in range(100)]
    attachments = [attachment_row(3 * i + k, a["sys_id"]) for i, a in enumerate(articles) for k in range(3)]
    criteria = [{"sys_id": c, "user": "u1", "sys_updated_on": T0} for c in ("ucA", "ucB")]
    return articles, FakeServiceNow({"kb_knowledge": articles, "sys_attachment": attachments, "user_criteria": criteria})


@pytest.mark.asyncio
async def test_a_full_read_lists_attachments_and_feedback_once(servicenow_connector) -> None:
    sc = servicenow_connector
    _, fake = _hundred_articles()
    wire_listing(sc, fake)

    await sc._sync_articles()

    assert len(fake.calls_to("kb_knowledge")) == 1
    # one listing for the whole run, not one per IN chunk of each page
    assert fake.calls_to("sys_attachment") == ["table_nameINkb_knowledge,ZZ_YYkb_knowledge^ORDERBYsys_updated_on^ORDERBYsys_id"]
    assert len(fake.calls_to("kb_feedback")) == 1
    assert len(fake.calls_to("user_criteria")) == 2  # one per distinct set, then the run's cache
    assert len(written_ids(sc)) == 400


@pytest.mark.asyncio
async def test_an_incremental_page_lists_attachments_per_in_chunk(servicenow_connector) -> None:
    sc = servicenow_connector
    articles, fake = _hundred_articles("2024-06-02 00:00:00")
    wire_listing(sc, fake, checkpoint=T0)

    await sc._sync_articles()

    chunks = sc._chunk_ids([a["sys_id"] for a in articles])
    # one listing per IN chunk the URL limit allows (it was one listing per article)
    assert len(fake.calls_to("sys_attachment")) == len(chunks)
    assert len(chunks) < 100
    assert len(written_ids(sc)) == 400


@pytest.mark.asyncio
async def test_failed_attachment_page_fails_its_articles(servicenow_connector) -> None:
    fake = FakeServiceNow(
        {"kb_knowledge": [article_row(1, "2024-06-01 01:00:00"), article_row(2, "2024-06-01 02:00:00")]},
        fail=lambda table, q: status_error(503) if table == "sys_attachment" else None,
    )
    wire_listing(servicenow_connector, fake, checkpoint=T0)

    await servicenow_connector._sync_articles()

    servicenow_connector.data_entities_processor.on_new_records.assert_not_awaited()
    assert servicenow_connector.full_sync_coverage().complete is False
    assert servicenow_connector.article_sync_point.data["articles"]["last_sync_time"] == "2024-06-01 00:59:59"


@pytest.mark.asyncio
async def test_admins_read_once_per_run(servicenow_connector) -> None:
    sc = servicenow_connector
    fake = FakeServiceNow({
        "kb_knowledge": [article_row(1, cannot_read_user_criteria="d1")],
        "sys_user_has_role": [{"sys_id": "r1", "user": "u1", "role.name": "admin", "user.active": "true",
                               "sys_updated_on": T0}],
        "kb_knowledge_base": [{"sys_id": "kb1", "owner": "", "kb_managers": "", "sys_updated_on": T0}],
        "user_criteria": [{"sys_id": "d1", "user": "u2", "sys_updated_on": T0}],
    })
    wire_listing(sc, fake)
    sc.servicenow_client = MagicMock()
    sc.data_entities_processor.get_user_by_source_id = AsyncMock(return_value=MagicMock(source_user_id="u1", email="u1@x.com"))
    with patch.object(sc, "_register_authenticated_identity", new_callable=AsyncMock), \
         patch.object(sc, "_sync_users_and_groups", new_callable=AsyncMock), \
         patch.object(sc, "_sync_knowledge_bases", new_callable=AsyncMock), \
         patch.object(sc, "_sync_categories", new_callable=AsyncMock):
        await sc.run_sync()

    assert len([q for q in fake.calls_to("sys_user_has_role") if not q.startswith("ORDERBYDESC")]) == 1
    assert written_ids(sc) == ["KB0000001"]


@pytest.mark.asyncio
async def test_app_users_read_once_per_run(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.data_entities_processor.get_all_app_users = AsyncMock(return_value=[MagicMock(source_user_id="u1", email="u1@x.com")])

    await sc._flatten_and_create_user_groups([], [])
    permissions = await sc._users_to_permissions({"u1"}, PermissionType.READ)

    assert [p.email for p in permissions] == ["u1@x.com"]
    sc.data_entities_processor.get_all_app_users.assert_awaited_once()


# --- X-Total-Count ends a listing, never a short or empty page ---------------------------------


def _visible_at(total: int, page: int, rows_per_page: list[int]) -> list[dict]:
    """``total`` rows in keyset order; page n (of ``page`` rows) shows its first rows_per_page[n] rows."""
    rows = [{"sys_id": f"k{i:03d}", "sys_updated_on": T0, "_acl_hidden": True} for i in range(total)]
    for n, visible in enumerate(rows_per_page):
        for row in rows[n * page: n * page + visible]:
            row.pop("_acl_hidden")
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize(("total", "page", "rows_per_page"), [
    (90, 10, [0, 0, 3, 2, 1, 2, 2, 1, 0]),  # non-admin persona, limit 10: pages empty in the middle
    (82, 5, [0] * 10 + [1, 0, 1, 0, 0, 0, 1]),  # persona on kb_knowledge, limit 5: rows only at offsets 50, 60, 80
])
async def test_empty_pages_mid_list_continue(servicenow_connector, total, page, rows_per_page) -> None:
    rows = _visible_at(total, page, rows_per_page)
    fake = FakeServiceNow({"kb_knowledge": rows})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    read = [r.sys_id async for p in servicenow_connector._iter_table("kb_knowledge", None, ["sys_id"], page=page)
            for r in p]

    assert read == [r["sys_id"] for r in rows if not r.get("_acl_hidden")]
    assert len(read) == sum(rows_per_page)


@pytest.mark.asyncio
async def test_stops_when_offset_reaches_total_count(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": _visible_at(25, 10, [])})  # every row hidden
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    read = [p async for p in servicenow_connector._iter_table("kb_knowledge", None, ["sys_id"], page=10)]

    assert read == []
    assert [p["sysparm_offset"] for p in fake.params] == ["0", "10", "20"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 100, 150])
async def test_a_listing_reads_no_extra_page(servicenow_connector, count) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(i) for i in range(count)]})
    wire_listing(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert len(fake.calls_to("kb_knowledge")) == max(1, -(-count // 100))
    assert len(set(written_ids(servicenow_connector))) == count


@pytest.mark.asyncio
async def test_short_admin_page_continues(servicenow_connector) -> None:
    # Rows an ACL hides even from admin (seen live: 618 roles list as 607), over pages of 1000
    roles = [{"sys_id": f"r{i:04d}", "name": f"role{i}", "sys_updated_on": T0} for i in range(2618)]
    for i in range(0, 2618, 57):
        roles[i]["_acl_hidden"] = True
    fake = FakeServiceNow({"sys_user_role": roles})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_roles()

    assert len(result) == 2618 - 46
    assert [limit for _, _, limit in fake.calls] == [1000] * 3  # short pages, and no extra one


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 1000, 1001])
async def test_an_id_listing_reads_pages_of_1000_and_no_extra_page(servicenow_connector, count) -> None:
    rows = [{"sys_id": f"m{i:05d}", "user": f"u{i}", "group": "g1", "user.active": "true",
             "sys_updated_on": T0} for i in range(count)]
    fake = FakeServiceNow({"sys_user_grmember": rows})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    memberships = await servicenow_connector._fetch_all_memberships()

    assert len(memberships) == count
    assert [limit for _, _, limit in fake.calls] == [1000] * max(1, -(-count // 1000))


@pytest.mark.asyncio
async def test_missing_total_count_raises(servicenow_connector) -> None:
    response = TableAPIResponse(result=[{"sys_id": "g1", "sys_updated_on": T0}])
    servicenow_connector._get_fresh_datasource = AsyncMock(
        return_value=MagicMock(get_now_table_tableName=AsyncMock(return_value=response)))

    with pytest.raises(ServiceNowAPIError, match="X-Total-Count"):
        await servicenow_connector._fetch_all_groups()


@pytest.mark.asyncio
async def test_no_count_param_not_sent(servicenow_connector, monkeypatch) -> None:
    rows = [{"sys_id": "g1", "name": "G", "sys_updated_on": T0}]
    ds, sent = _datasource([_ok(rows, total=1)], monkeypatch, None)
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=ds)

    result = await servicenow_connector._fetch_all_groups()

    assert [g.sys_id for g in result] == ["g1"]
    [request] = sent
    assert "sysparm_no_count" not in request.url.params
    assert request.url.params["sysparm_offset"] == "0"


@pytest.mark.asyncio
async def test_datasource_reads_x_total_count(monkeypatch) -> None:
    ds, _ = _datasource([_ok([{"sys_id": "a1"}], total=82)], monkeypatch, None)

    response = await _list(ds)

    assert (response.total_count, len(response.result)) == (82, 1)


@pytest.mark.asyncio
async def test_admins_granted_are_stored_for_a_run_whose_roles_did_not_change(servicenow_connector) -> None:
    sc = servicenow_connector
    row = {"role.name": "admin", "state": "active", "user.active": "true", "sys_updated_on": T0}
    fake = FakeServiceNow({"sys_user_has_role": [{**row, "sys_id": "r1", "user": "u1"},
                                                 {**row, "sys_id": "r2", "user": "u2", "_acl_hidden": True}]})
    sc._get_fresh_datasource = AsyncMock(return_value=fake)
    sc.user_sync_point = MemorySyncPoint({"admins": {"ids": ["u3"]}})  # the last listing shown in full

    assert await sc._get_admin_users() == {"u1", "u3"}  # partly hidden: what it shows and the last full set

    assert sc.user_sync_point.data["admins"] == {"ids": ["u3"], "granted": ["u1", "u3"]}
