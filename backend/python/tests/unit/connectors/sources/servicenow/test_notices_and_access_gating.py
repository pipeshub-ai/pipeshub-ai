"""ServiceNow tells people what went wrong: notices for stops and skipped items, a connection test
that names what the integration user cannot read, a checked instance URL, logs without content,
the access re-read pass only when access changed, rendered wiki bodies, the
out-of-box "Has a role" criterion and a least-privilege integration user.
"""

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from servicenow_fakes import FakeServiceNow, block_groups, status_error
from test_permissions import Instance, _connector, _people, _sync

from app.connectors.core.base.connector.connector_service import (
    ConnectorInitError,
)
from app.connectors.sources.servicenow.servicenow import connector as connector_module
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.servicenow.servicenow.constants import ServiceNowDefaults
from app.models.permission import PermissionType
from app.services.notification.types import NotificationSeverity, NotificationType
from app.sources.client.servicenow.servicenow import (
    ServiceNowRateLimited,
    check_servicenow_instance_url,
)
from app.sources.external.servicenow.models import (
    ServiceNowAccessError,
    ServiceNowAPIError,
    ServiceNowAuthError,
    TableAPIRecord,
    TableAPIResponse,
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource

T0 = "2024-06-01 00:00:00"
NAME = "IT Knowledge"
ME = "javascript:gs.getUserID()"  # the fake has no script: the current user is the row with this sys_id


def _notices(sc) -> list[dict]:
    if not hasattr(sc, "_published"):
        sc._published = []

        async def publish(**kwargs) -> bool:
            sc._published.append(kwargs)
            return True

        sc._notification_service = MagicMock(publish_notification=AsyncMock(side_effect=publish))
        sc.connector_instance_name = NAME
    return sc._published


def _failing_run(sc, error: BaseException) -> None:
    sc._run_sync = AsyncMock(side_effect=error)


# --- notices -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_error_notifies_once_with_display_name(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    _failing_run(sc, ServiceNowAuthError("User is not authenticated: Required to provide Auth information"))

    for _ in range(2):  # the second, inside the back-off window, is suppressed
        with pytest.raises(ServiceNowAuthError):
            await sc.run_sync()

    assert len(sent) == 1
    assert sent[0]["type"] == NotificationType.CONNECTOR_AUTH_ERROR
    assert sent[0]["severity"] == NotificationSeverity.ERROR
    assert sent[0]["title"] == f"{NAME}: needs to be re-authorized"
    assert "HTTP 401: User is not authenticated" in sent[0]["message"]
    assert sent[0]["recipient_user_ids"] == ["test-user-id"]
    assert [r.value for r in sent[0]["recipient_roles"]] == ["admin"]
    assert [i.code for i in sc.last_action_items] == ["servicenow_reauthorize"]


@pytest.mark.asyncio
async def test_repeat_within_backoff_suppressed_and_other_errors_still_sent(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    _failing_run(sc, RuntimeError("boom"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await sc.run_sync()
    _failing_run(sc, RuntimeError("another"))
    with pytest.raises(RuntimeError):
        await sc.run_sync()

    assert [n["title"] for n in sent] == [f"{NAME}: sync failed"] * 2
    assert "The sync stopped: boom." in sent[0]["message"]


@pytest.mark.asyncio
async def test_access_error_names_the_table(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    _failing_run(sc, ServiceNowAccessError("kb_uc_cannot_read_mtom", "User Not Authorized"))

    with pytest.raises(ServiceNowAccessError):
        await sc.run_sync()

    assert sent[0]["type"] == NotificationType.CONNECTOR_NOT_ACCESSIBLE
    assert sent[0]["title"] == f"{NAME}: cannot read kb_uc_cannot_read_mtom"
    assert "cannot read the table kb_uc_cannot_read_mtom" in sent[0]["message"]


@pytest.mark.asyncio
async def test_unreadable_org_table_names_the_table(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    sc = servicenow_connector
    _connector(sc, inst)
    sent = _notices(sc)
    inst.fake.fail = lambda table, q: status_error(403, "User Not Authorized") if table == "core_company" else None

    with pytest.raises(ServiceNowAccessError):
        await _sync(sc, inst)

    assert sent[0]["title"] == f"{NAME}: cannot read core_company"


@pytest.mark.asyncio
async def test_rate_limit_notice_once_per_reset(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    reset = (int(time.time()) // 3600 + 1) * 3600
    for reset_at in (reset, reset + 5, reset + 3600):  # the second is the same reset minute
        _failing_run(sc, ServiceNowRateLimited(reset_at, "rule-1"))
        with pytest.raises(ServiceNowRateLimited):
            await sc.run_sync()

    assert len(sent) == 2
    assert sent[0]["title"] == f"{NAME}: sync paused, ServiceNow rate limit reached"
    # The rule's sys_id means nothing to a reader; the time is the reset minute.
    until = time.strftime("%H:%M", time.gmtime(reset))
    assert sent[0]["message"].startswith(f"A ServiceNow rate limit rule is used up until {until} UTC")
    assert sent[0]["message"].endswith("the next sync after that time continues where this one stopped.")


def test_rate_limit_reset_shown_as_the_reset_minute() -> None:
    reset = 1793307600  # 21:00:00 UTC; Retry-After lands a second or so before or after it
    assert {connector_module.rate_limit_reset_label(reset + d) for d in (-1.4, 0, 2)} == {"21:00"}


@pytest.mark.asyncio
async def test_rate_limit_notice_of_a_platform_full_sync_says_it_runs_again(servicenow_connector) -> None:
    # The platform clears every sync point before a full sync, so the next one starts over.
    sc = servicenow_connector
    sent = _notices(sc)
    sc.data_entities_processor.full_sync_running = MagicMock(return_value=True)
    _failing_run(sc, ServiceNowRateLimited(int(time.time()) + 7200))
    with pytest.raises(ServiceNowRateLimited):
        await sc.run_sync()

    assert sent[0]["message"].endswith("Nothing it did not read was removed; the next sync after that time "
                                       "runs the full sync again.")


@pytest.mark.asyncio
async def test_failure_notice_carries_no_secret(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    _failing_run(sc, RuntimeError("GET https://x.service-now.com/api?access_token=s3cr3t failed"))
    with pytest.raises(RuntimeError):
        await sc.run_sync()
    assert "s3cr3t" not in sent[0]["message"]


@pytest.mark.asyncio
async def test_summary_lists_two_skipped_kbs_once(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)

    async def run() -> None:
        sc._kbs_unread += ["HR", "Finance", "HR"]

    sc._run_sync = run
    await sc.run_sync()

    assert len(sent) == 1
    assert sent[0]["severity"] == NotificationSeverity.WARNING
    assert sent[0]["title"] == f"{NAME}: sync finished with problems"
    assert sent[0]["message"] == ("The sync finished, but:\n- 2 knowledge bases kept their stored access because "
                                  "their user criteria could not be read: Finance, HR")
    assert sc.last_action_items == []


@pytest.mark.asyncio
async def test_every_count_line_agrees_with_its_count(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)

    async def run() -> None:
        sc._note_criteria("scripted", "c1")
        sc._note_criteria("scripted", "c2")
        for kind in ("hidden", "partial"):
            sc._note_criteria(kind, "c3")
        sc._no_reader = 2

    sc._run_sync = run
    await sc.run_sync()

    message = sent[0]["message"]
    assert "- 2 user criteria use a script, which PipesHub does not run: they grant nobody here, and as Cannot "         "Read or Cannot Contribute they deny everyone they may cover" in message
    assert "- 1 user criterion is hidden from the integration user: it grants nobody here, and as Cannot Read "         "or Cannot Contribute it denies everyone" in message
    assert "- 1 user criterion names groups or users partly hidden from the integration user: as Cannot Read "         "or Cannot Contribute it denies everyone it may cover" in message
    assert "- 2 articles have no reader in PipesHub: their user criteria match nobody" in message


@pytest.mark.asyncio
async def test_clean_run_sends_nothing(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    sc._run_sync = AsyncMock()
    await sc.run_sync()
    assert sent == []


@pytest.mark.asyncio
async def test_kb_whose_criteria_fail_is_named_in_the_summary(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb-ok", can=(inst.crit("c-hr", group="g-hr"),))
    inst.kb("kb-bad", can=(inst.crit("c-ok", user="eng"),), cannot=(inst.crit("c-bad", group="g-eng"),))
    inst.kbs["kb-bad"]["title"] = "Engineering"
    sc = servicenow_connector
    _connector(sc, inst)
    sent = _notices(sc)
    # The knowledge bases' Cannot Read rows cannot be listed at once, nor kb-bad's on their own.
    inst.fake.fail = lambda table, q: status_error(500) if table == "kb_uc_cannot_read_mtom" and (
        q.startswith("ORDERBYsys") or "kb-bad" in q) else None

    await _sync(sc, inst)

    assert "1 knowledge base kept its stored access because its user criteria could not be read: Engineering" \
        in sent[0]["message"]


@pytest.mark.asyncio
async def test_scripted_criteria_counted_in_summary_once(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-script", script="answer = gs.getUser().isMemberOf('x');"),
                       inst.crit("c-hr", group="g-hr")))
    sc = servicenow_connector
    _connector(sc, inst)
    sent = _notices(sc)

    await _sync(sc, inst)
    await _sync(sc, inst)  # a persistent condition is shown when it first appears

    assert len(sent) == 1
    assert "1 user criterion uses a script, which PipesHub does not run: it grants nobody here" in sent[0]["message"]


@pytest.mark.asyncio
async def test_hidden_criteria_and_users_without_email_reported(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.user("noemail", email=False)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB1", "kb", can_read_user_criteria=inst.crit("c-hidden", user="hr"))
    inst.article("KB2", "kb", can_read_user_criteria=inst.crit("c-shown", user="hr"))
    inst.hidden.add("c-hidden")  # read with c-shown: a listing that shows none would be an unreadable table
    sc = servicenow_connector
    _connector(sc, inst)
    sent = _notices(sc)

    await _sync(sc, inst)

    message = sent[0]["message"]
    assert "1 active ServiceNow user has no email address and could not be synced" in message
    assert "1 user criterion is hidden from the integration user: it grants nobody here" in message
    assert "noemail" not in message and "KB1" not in message  # no user or article named


@pytest.mark.asyncio
async def test_article_nobody_can_read_reported(servicenow_connector) -> None:
    inst = Instance()
    inst.user("hr")
    inst.group("g-hr", "hr")
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),), owner="", managers="")
    inst.article("KB1", "kb", cannot_read_user_criteria=inst.crit("c-script", script="answer = true;"))
    sc = servicenow_connector
    graph = _connector(sc, inst)
    sent = _notices(sc)

    await _sync(sc, inst)

    assert graph.readers("KB1") == set()
    assert "- 1 article has no reader in PipesHub: its user criteria match nobody PipesHub can evaluate" \
        in sent[0]["message"]


@pytest.mark.asyncio
async def test_failed_articles_counted_in_summary(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB1", "kb")
    inst.article("KB2", "kb")
    sc = servicenow_connector
    _connector(sc, inst)
    sent = _notices(sc)
    real = sc._process_single_article

    async def flaky(article: TableAPIRecord, *args: object) -> list:
        if article.number == "KB2":
            raise RuntimeError("write failed")
        return await real(article, *args)

    sc._process_single_article = flaky
    await _sync(sc, inst)

    assert "- Articles that could not be synced, retried on the next sync: 1" in sent[0]["message"]
    assert sc.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_background_init_failure_notifies_the_reason(servicenow_connector) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    await sc.on_background_init_failed(ConnectorInitError("The connector is not authorized yet."))
    await sc.on_background_init_failed(RuntimeError("token=s3cr3t"))

    assert [n["title"] for n in sent] == [f"{NAME}: could not start"] * 2
    assert "The connector is not authorized yet." in sent[0]["message"]
    assert "s3cr3t" not in sent[1]["message"]


# --- the connection test -----------------------------------------------------------------------


def _readable_instance(**overrides: list) -> FakeServiceNow:
    tables = {table: [{"sys_id": f"{table}-1", "sys_updated_on": T0}] for table, _ in connector_module._REQUIRED_TABLES}
    tables["sys_user"] = [{"sys_id": ME, "sys_updated_on": T0}]
    tables["sys_user_has_role"] = [{"sys_id": "h1", "user": ME, "role.name": "admin", "state": "active",
                                    "sys_updated_on": T0}]
    tables["sys_attachment"] = [{"sys_id": "f1", "table_name": "kb_knowledge", "sys_updated_on": T0}]
    tables.update(overrides)
    return FakeServiceNow(tables)


def _wire(sc, fake: FakeServiceNow) -> None:
    sc._get_fresh_datasource = AsyncMock(return_value=fake)
    sc.instance_url = "https://acme.service-now.com"


@pytest.mark.asyncio
async def test_connection_test_reads_one_row_of_every_required_table(servicenow_connector) -> None:
    fake = _readable_instance()
    _wire(servicenow_connector, fake)
    assert await servicenow_connector.test_connection_and_access() is True
    read = {t for t, _, limit in fake.calls if limit == 1}
    assert {t for t, _ in connector_module._REQUIRED_TABLES} <= read
    assert servicenow_connector._setup_warnings == []


@pytest.mark.asyncio
async def test_403_on_cannot_read_mtom_names_table(servicenow_connector) -> None:
    fake = _readable_instance()
    fake.fail = lambda table, q: status_error(403, "User Not Authorized") if table in (
        "kb_uc_cannot_read_mtom", "sys_user_grmember") else None
    _wire(servicenow_connector, fake)
    with pytest.raises(ConnectorInitError) as error:
        await servicenow_connector.test_connection_and_access()
    assert str(error.value) == (
        "The ServiceNow integration user cannot read these tables: sys_user_grmember, kb_uc_cannot_read_mtom. "
        "Give it the admin role, or at least the knowledge_admin and user_admin roles, then try again."
    )


@pytest.mark.asyncio
async def test_403_on_optional_tables_does_not_fail(servicenow_connector) -> None:
    fake = _readable_instance()
    fake.fail = lambda table, q: status_error(403) if table in (
        "sys_user_role_contains", "sys_properties", "sp_portal", "sys_audit_delete") else None
    _wire(servicenow_connector, fake)
    assert await servicenow_connector.test_connection_and_access() is True


@pytest.mark.asyncio
async def test_401_message_has_vendor_text(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire(sc, _readable_instance())
    sc._with_token_retry = AsyncMock(side_effect=ServiceNowAuthError("User is not authenticated"))
    with pytest.raises(ConnectorInitError) as error:
        await sc.test_connection_and_access()
    assert str(error.value) == (
        "ServiceNow at acme.service-now.com refused the connector's sign-in (HTTP 401: User is not authenticated). "
        "Re-authorize the connector on its Authorize tab."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "message"), [
    (ServiceNowAPIError(500, "Failed to parse API response"),
     "ServiceNow at acme.service-now.com did not answer with its REST API; the instance may be hibernating. "
     "Wake it up, then try again."),
    (httpx.ConnectError("refused"), "Could not reach ServiceNow at acme.service-now.com. Check the instance URL."),
    (ServiceNowAPIError(302, "ServiceNow answered with a redirect to another address"),
     "ServiceNow at acme.service-now.com redirected the request (HTTP 302), so this URL is not the instance's "
     "REST API. Enter the instance's own address, such as https://<instance>.service-now.com."),
    (ServiceNowAPIError(503, "Service Unavailable"),
     "ServiceNow at acme.service-now.com answered HTTP 503: Service Unavailable. Try again later."),
    (ServiceNowAPIError(404, "Not Found"),
     "ServiceNow at acme.service-now.com answered HTTP 404: Not Found. Check the instance URL."),
])
async def test_connection_failures_say_why(servicenow_connector, error, message) -> None:
    fake = _readable_instance()
    fake.fail = lambda table, q: error
    _wire(servicenow_connector, fake)
    with pytest.raises(ConnectorInitError) as raised:
        await servicenow_connector.test_connection_and_access()
    assert str(raised.value) == message


@pytest.mark.asyncio
async def test_an_address_that_never_answers_fails_the_test_quickly(servicenow_connector, monkeypatch) -> None:
    _wire(servicenow_connector, _readable_instance())
    monkeypatch.setattr(ServiceNowDefaults, "CONNECTION_TEST_TIMEOUT_SECONDS", 0.05)

    async def hang(*_a: object, **_k: object) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(servicenow_connector, "_table_get", hang)
    with pytest.raises(ConnectorInitError, match="Could not reach ServiceNow at acme.service-now.com"):
        await asyncio.wait_for(servicenow_connector.test_connection_and_access(), 5)


@pytest.mark.asyncio
async def test_non_admin_warns(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire(sc, _readable_instance(sys_user_has_role=[
        {"sys_id": "h1", "user": ME, "role.name": "knowledge_admin", "state": "active", "sys_updated_on": T0}]))
    sent = _notices(sc)

    assert await sc.test_connection_and_access() is True
    await sc._notify_setup_warnings()

    assert sent[0]["type"] == NotificationType.CONNECTOR_WARNING
    assert sent[0]["title"] == f"{NAME}: the integration user can read less than an admin"
    assert sent[0]["message"] == (
        "- The integration user is not a ServiceNow admin, so PipesHub syncs only the articles, users and groups "
        "it can read.\nFor a complete sync, give the integration user the admin role."
    )


@pytest.mark.asyncio
async def test_hidden_properties_and_portal_warn_and_grant_less(servicenow_connector) -> None:
    sc = servicenow_connector
    hidden = {"_acl_hidden": True, "sys_updated_on": T0}
    fake = FakeServiceNow({
        "sys_properties": [
            {"sys_id": "p1", "name": ServiceNowDefaults.ARTICLE_READ_CRITERIA_PROPERTY, "value": "false", **hidden},
            {"sys_id": "p2", "name": ServiceNowDefaults.BLOCK_NO_CRITERIA_PROPERTY, "value": "false", **hidden},
        ],
        "sp_portal": [{"sys_id": "sp1", "default": "true", "url_suffix": "esc", **hidden}],
    })
    _wire(sc, fake)

    assert await sc._resolve_article_read_criteria() is True  # hidden is not "unset" (false)
    assert await sc._resolve_block_no_criteria() is True
    assert await sc._resolve_kb_portal_suffix() == "kb"
    assert sc._setup_warnings == [connector_module._PROPERTIES_HIDDEN, connector_module._PORTAL_HIDDEN]


@pytest.mark.asyncio
async def test_role_contains_403_is_read_as_empty(servicenow_connector) -> None:
    sc = servicenow_connector
    _wire(sc, FakeServiceNow({}, fail=lambda table, q: status_error(403) if table == "sys_user_role_contains" else None))
    assert await sc._fetch_role_hierarchy() == []
    assert sc.full_sync_coverage().complete is True


@pytest.mark.asyncio
async def test_least_privileged_user_syncs_without_role_contains(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB1", "kb")
    sc = servicenow_connector
    graph = _connector(sc, inst)
    inst.fake.fail = lambda table, q: status_error(403) if table in ("sys_user_role_contains", "sys_audit_delete") else None

    await _sync(sc, inst)

    assert "hr" in graph.readers("KB1")
    assert sc.full_sync_coverage().complete is True


# --- the instance URL ----------------------------------------------------------------------------


@pytest.mark.parametrize(("url", "message"), [
    ("http://acme.service-now.com", "must use https"),
    ("https://acme.service-now.com/now/nav", "with no path, query or fragment"),
    ("https://acme.service-now.com?x=1", "with no path, query or fragment"),
    ("https://acme.service-now.com#top", "with no path, query or fragment"),
    ("https://admin:pw@acme.service-now.com", "must not contain a user name or password"),
    ("https://169.254.169.254", "is not allowed"),
    ("https://localhost", "is not allowed"),
    ("acme.service-now.com", "must start with https://"),
])
def test_bad_instance_url_rejected(url, message) -> None:
    with pytest.raises(ValueError, match=message):
        check_servicenow_instance_url(url)


def test_custom_domain_accepted_and_trailing_slash_dropped() -> None:
    assert check_servicenow_instance_url("https://support.acme.com/") == "https://support.acme.com"


def test_token_url_other_host_rejected() -> None:
    with pytest.raises(ValueError, match="token URL must be on the instance's address, https://acme.service-now.com"):
        check_servicenow_instance_url("https://acme.service-now.com", same_host={
            "token URL": "https://evil.example.com/oauth_token.do", "authorize URL": None})
    check_servicenow_instance_url("https://acme.service-now.com", same_host={
        "token URL": "https://ACME.service-now.com/oauth_token.do"})
    # The scheme's default port, written or not, is the same address.
    check_servicenow_instance_url("https://acme.service-now.com:443", same_host={
        "token URL": "https://acme.service-now.com/oauth_token.do"})
    check_servicenow_instance_url("https://acme.service-now.com", same_host={
        "token URL": "https://acme.service-now.com:443/oauth_token.do"})
    with pytest.raises(ValueError, match="token URL must be on the instance's address"):
        check_servicenow_instance_url("https://acme.service-now.com", same_host={
            "token URL": "https://acme.service-now.com:8443/oauth_token.do"})


def test_http_allowed_only_with_the_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("SERVICENOW_ALLOW_INSECURE_HTTP", "1")
    assert check_servicenow_instance_url("http://10.0.0.5:8109") == "http://10.0.0.5:8109"


def test_auth_config_problems_refuses_a_bad_url() -> None:
    assert ServiceNowConnector.auth_config_problems({"instanceUrl": "https://acme.service-now.com"}) == []
    assert ServiceNowConnector.auth_config_problems({}) == []
    assert ServiceNowConnector.auth_config_problems({"instanceUrl": "http://acme.service-now.com"}) == [
        "The ServiceNow instance URL must use https: the access token is sent with every request"]


@pytest.mark.asyncio
async def test_init_rejects_a_token_url_on_another_host(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    sc._fetch_oauth_config_by_id = AsyncMock(return_value={
        "config": {"instanceUrl": "https://acme.service-now.com", "clientId": "id", "clientSecret": "secret",
                   "tokenUrl": "https://evil.example.com/oauth_token.do"},
        "redirectUri": "https://pipeshub.example/callback",
    })
    with pytest.raises(ConnectorInitError, match="token URL must be on the instance's address"):
        await sc.init()
    assert sc.servicenow_client is None  # no client, so no request was sent


# --- logs ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_is_a_no_op_that_logs_nothing(servicenow_connector, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    assert await servicenow_connector.handle_webhook_notification({"body": "SECRET-BODY"}) is None
    assert "SECRET-BODY" not in caplog.text


@pytest.mark.asyncio
async def test_a_failed_group_page_logs_one_traceback(servicenow_connector, caplog) -> None:
    sc = servicenow_connector
    inst = Instance()
    _people(inst)
    _connector(sc, inst)
    # Not the 1-row access check (newest first): the group listing itself
    inst.fake.fail = lambda table, q: status_error(503) if table == "sys_user_group" and not (
        q or "").startswith("ORDERBYDESC") else None
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ServiceNowAPIError):
        await _sync(sc, inst)

    assert len([r for r in caplog.records if r.exc_info]) == 1


@pytest.mark.asyncio
async def test_a_rate_limit_stop_logs_no_traceback(servicenow_connector, caplog) -> None:
    sc = servicenow_connector
    inst = Instance()
    _people(inst)
    _connector(sc, inst)
    inst.fake.fail = lambda table, q: ServiceNowRateLimited(time.time() + 600) if table == "sys_user_group" else None
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ServiceNowRateLimited):
        await _sync(sc, inst)

    assert not [r for r in caplog.records if r.exc_info]
    assert any("Sync stopped" in r.getMessage() for r in caplog.records)


def test_transform_failure_does_not_log_body(servicenow_connector, caplog) -> None:
    sc = servicenow_connector
    caplog.set_level(logging.DEBUG)
    # An article transform that fails raises: the sync logs the error, which names no body.
    with pytest.raises(AttributeError) as raised:
        sc._transform_to_article_webpage_record(TableAPIRecord(sys_id="a1", text="<p>SECRET-BODY</p>"))
    sc._transform_to_attachment_file_record(SimpleNamespace(sys_id="f1", file_name="", content_type="SECRET-BODY"))
    assert "SECRET-BODY" not in caplog.text and "SECRET-BODY" not in str(raised.value)
    assert "f1" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_expected_401_and_403_not_logged_at_error(status, caplog) -> None:
    ds = ServiceNowDataSource(MagicMock())
    response = MagicMock(status=status, text=lambda: '{"error": {"message": "User Not Authorized"}}')
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ServiceNowAPIError):
        await ds._handle_table_response(response)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- the access re-read pass runs only when access may have changed ----------------------------------


def _criteria_model(sc) -> tuple[Instance, object]:
    inst = Instance()
    _people(inst)
    for k in range(5):
        inst.kb(f"kb{k}", can=(inst.crit(f"c-hr{k}", group="g-hr"),), contribute=(inst.crit(f"c-c{k}", user="contrib"),))
        inst.article(f"KB{k}a", f"kb{k}")
        inst.article(f"KB{k}b", f"kb{k}", cannot_read_user_criteria=inst.crit(f"c-no{k}", user="hr"))
    graph = _connector(sc, inst)
    return inst, graph


def _later(sc) -> None:
    """The next run reads deltas from a day after every row of the model changed."""
    sc.user_sync_point.data["users"] = {"last_sync_time": "2024-06-02 00:00:00"}
    sc.article_sync_point.data["articles"] = {"last_sync_time": "2024-06-02 00:00:00"}


def _reread(fake: FakeServiceNow) -> list[str]:
    return [q for q in fake.calls_to("kb_knowledge") if q and "ISNOTEMPTY" in q and "^NQ" not in q]


@pytest.mark.asyncio
async def test_the_reread_pass_lists_no_attachments_and_keeps_files_and_revisions(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, graph = _criteria_model(sc)
    image = "e" * 32
    inst.articles["KB0b"]["text"] = f'<p><img src="/sys_attachment.do?sys_id={image}"></p>'
    for sys_id, table, article in ((image, "ZZ_YYkb_knowledge", "s-KB0b"), ("f" * 32, "kb_knowledge", "s-KB1b")):
        inst.attachments.append({"sys_id": sys_id, "file_name": f"{table}.png", "content_type": "image/png",
                                 "size_bytes": "5", "hash": f"h-{sys_id[0]}", "table_name": table,
                                 "table_sys_id": article, "sys_created_on": T0, "sys_updated_on": T0})
    await _sync(sc, inst)
    files = graph.store.keys("FILE")
    revisions = {k: r.external_revision_id for k, r in graph.store.records.items()}
    _later(sc)
    sc.article_sync_point.data["access_fingerprint"]["last_sync_time"] = "2000-01-01 00:00:00"  # the daily pass
    inst.fake.calls.clear()
    sc.data_entities_processor.on_new_records.reset_mock()

    await _sync(sc, inst)

    assert _reread(inst.fake)
    by_article = [q for q in inst.fake.calls_to("sys_attachment") if "table_sys_idIN" in q]
    assert by_article == [f"table_nameINkb_knowledge,ZZ_YYkb_knowledge^table_sys_idIN{inst.articles['KB0b']['sys_id']}"
                          "^ORDERBYsys_updated_on^ORDERBYsys_id"]  # only the body that names an image
    written = [r.record_type for c in sc.data_entities_processor.on_new_records.await_args_list for r, _ in c.args[0]]
    assert written and set(written) == {"WEBPAGE"}
    assert graph.store.keys("FILE") == files == ["KB1b/kb_knowledge.png/h-f"]
    assert {k: r.external_revision_id for k, r in graph.store.records.items()} == revisions


def _listings(fake: FakeServiceNow, table: str) -> list[str]:
    """The calls to ``table`` other than the 1-row access check (newest first)."""
    return [q for q in fake.calls_to(table) if not q.startswith("ORDERBYDESC")]


@pytest.mark.asyncio
async def test_no_change_reads_and_writes_no_access(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, graph = _criteria_model(sc)
    await _sync(sc, inst)  # full: stores what the access tables look like
    readers = {k: graph.readers(k) for k in graph.store.records}
    _later(sc)
    inst.fake.calls.clear()
    p = sc.data_entities_processor
    p.on_new_user_groups.reset_mock()
    p.on_new_record_groups.reset_mock()

    await _sync(sc, inst)

    assert _reread(inst.fake) == []
    for table in ("sys_user_grmember", "sys_user_has_role", "sys_user_role", "user_criteria",
                  "kb_uc_can_read_mtom", "kb_uc_cannot_read_mtom", "kb_uc_can_contribute_mtom",
                  "kb_uc_cannot_contribute_mtom"):
        assert _listings(inst.fake, table) == [], table
    p.on_new_user_groups.assert_not_awaited()
    assert not [kb for c in p.on_new_record_groups.await_args_list for kb, _ in c.args[0] if kb.external_group_id in inst.kbs]
    assert {k: graph.readers(k) for k in graph.store.records} == readers


@pytest.mark.asyncio
async def test_an_access_change_reads_the_kb_criteria_once(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, _ = _criteria_model(sc)
    await _sync(sc, inst)
    _later(sc)
    inst.members.append(("plain", "g-hr"))
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake)
    for table in ("kb_uc_can_read_mtom", "kb_uc_cannot_read_mtom", "kb_uc_can_contribute_mtom",
                  "kb_uc_cannot_contribute_mtom"):
        assert len(_listings(inst.fake, table)) == 1  # one listing for every KB
    # the KBs' criteria in one read (the other is the re-read page's own criteria)
    assert len([q for q in _listings(inst.fake, "user_criteria") if "c-hr0" in q]) == 1
    assert len(_listings(inst.fake, "sys_user_grmember")) == 1  # criteria use the run's membership listing


@pytest.mark.asyncio
async def test_a_quiet_run_reads_the_ownership_group_of_a_changed_article(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, graph = _criteria_model(sc)
    inst.article("KB-og", "kb0", can_read_user_criteria="c-hr0", ownership_group="g-eng")
    await _sync(sc, inst)
    assert "eng" in graph.readers("KB-og")  # through the child group g-eng-platform
    _later(sc)
    inst.articles["KB-og"].update(short_description="renamed", sys_updated_on="2024-06-03 00:00:00")
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake) == []  # quiet: access did not change
    assert _listings(inst.fake, "sys_user_grmember")  # read for this article only
    assert graph.store.records["KB-og"].record_name == "renamed"
    assert {"eng", "eng2", "hr"} <= graph.readers("KB-og")


@pytest.mark.asyncio
async def test_membership_change_during_a_run_rereads_next_run(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, _graph = _criteria_model(sc)

    def join_while_categories_are_read(fake: FakeServiceNow) -> None:
        # After step 1 read the memberships, before the articles: the run that sees this is the next one.
        if fake.calls[-1][0] == "kb_category" and ("plain", "g-hr") not in inst.members:
            inst.members.append(("plain", "g-hr"))
            fake.tables["sys_user_grmember"] = inst.tables()["sys_user_grmember"]

    inst.fake.after_call = join_while_categories_are_read
    await _sync(sc, inst)
    inst.fake.after_call = None
    _later(sc)
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["criterion", "membership_removed", "member_added", "kb_owner", "property"])
async def test_each_access_change_rereads(servicenow_connector, change) -> None:
    sc = servicenow_connector
    inst, graph = _criteria_model(sc)
    await _sync(sc, inst)
    _later(sc)
    later = "2024-06-03 00:00:00"
    if change == "criterion":
        inst.criteria["c-no0"].update(user="eng", sys_updated_on=later)
    elif change == "membership_removed":
        inst.members.remove(("hr", "g-hr"))  # a deleted row: only the row count tells
    elif change == "member_added":
        inst.members.append(("plain", "g-hr"))
    elif change == "kb_owner":
        inst.kbs["kb0"].update(owner="plain", sys_updated_on=later)
    else:
        inst.properties["glide.knowman.apply_article_read_criteria"] = "true"
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake)
    if change == "criterion":
        assert "hr" in graph.readers("KB0b") and "eng" not in graph.readers("KB0b")


@pytest.mark.asyncio
async def test_daily_backstop_rereads(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, _ = _criteria_model(sc)
    await _sync(sc, inst)
    _later(sc)
    stored = sc.article_sync_point.data["access_fingerprint"]
    stored["last_sync_time"] = "2000-01-01 00:00:00"
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake)
    assert sc.article_sync_point.data["access_fingerprint"]["last_sync_time"] != "2000-01-01 00:00:00"


@pytest.mark.asyncio
async def test_access_fingerprint_is_storable_in_a_graph_store(servicenow_connector) -> None:
    # Neo4j rejects a map as a property value (seen live: Neo.ClientError.Statement.TypeError)
    sc = servicenow_connector
    inst, _ = _criteria_model(sc)
    await _sync(sc, inst)
    stored = sc.article_sync_point.data["access_fingerprint"]
    assert all(isinstance(v, (str, int, float, bool)) for v in stored.values())


@pytest.mark.asyncio
async def test_failed_run_keeps_the_pass_due(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, _ = _criteria_model(sc)
    await _sync(sc, inst)
    _later(sc)
    inst.criteria["c-no0"].update(user="eng", sys_updated_on="2024-06-03 00:00:00")
    real = sc._process_single_article

    async def flaky(article: TableAPIRecord, *args: object) -> list:
        if article.number == "KB1b":
            raise RuntimeError("write failed")
        return await real(article, *args)

    sc._process_single_article = flaky
    await _sync(sc, inst)
    sc._process_single_article = real
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake)  # the state was not stored after the failure


@pytest.mark.asyncio
async def test_an_unread_kb_keeps_the_pass_due(servicenow_connector) -> None:
    sc = servicenow_connector
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB1", "kb")  # inherits: no article fails
    _connector(sc, inst)
    await _sync(sc, inst)
    _later(sc)
    inst.members.append(("plain", "g-hr"))  # access changed: the next run reads the KB grants
    inst.fake.fail = lambda table, q: (
        status_error(503) if table == "user_criteria" and not q.startswith("ORDERBYDESC") else None)
    await _sync(sc, inst)
    assert sc._kbs_unread == ["kb"]
    inst.fake.fail = None
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake)  # not quiet: the KB kept its old grants
    assert len(_listings(inst.fake, "kb_uc_can_read_mtom")) == 1


@pytest.mark.asyncio
async def test_an_article_with_a_hidden_criterion_does_not_keep_runs_from_being_quiet(servicenow_connector) -> None:
    sc = servicenow_connector
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.article("KB1", "kb", can_read_user_criteria=inst.crit("c-hidden", user="hr"))
    inst.article("KB2", "kb", can_read_user_criteria=inst.crit("c-shown", user="hr"))
    inst.hidden.add("c-hidden")  # fails every run, kept as stored
    _connector(sc, inst)
    await _sync(sc, inst)
    assert {"access_fingerprint", "attachments", "feedback"} <= set(sc.article_sync_point.data)
    _later(sc)
    inst.fake.calls.clear()

    await _sync(sc, inst)

    assert _reread(inst.fake) == []


@pytest.mark.asyncio
async def test_no_change_incremental_call_count(servicenow_connector) -> None:
    """The model behind the call counts: 5 KBs, 10 articles (5 with Cannot Read)."""
    sc = servicenow_connector
    inst, _ = _criteria_model(sc)
    await _sync(sc, inst)
    _later(sc)
    inst.fake.calls.clear()
    await _sync(sc, inst)
    gated = len(inst.fake.calls)

    sc.article_sync_point.data["access_fingerprint"]["last_sync_time"] = "2000-01-01 00:00:00"
    inst.fake.calls.clear()
    await _sync(sc, inst)
    with_pass = len(inst.fake.calls)

    print(f"no-change incremental: {gated} calls gated, {with_pass} with the re-read pass")
    assert gated < with_pass


# --- wiki articles stream their rendered body -------------------------------------------------------


class _RenderingFake(FakeServiceNow):
    """Answers sysparm_display_value=true on kb_knowledge.wiki with the rendered HTML."""

    async def get_now_table_tableName(self, tableName: str, **params: str | None) -> TableAPIResponse:
        response = await super().get_now_table_tableName(tableName, **params)
        if params.get("sysparm_display_value") == "true" and tableName == "kb_knowledge":
            for row in response.result:
                row.wiki = "<h2>Install</h2><ul><li>Step one</li></ul><table><tr><td>a</td></tr></table>"
        return response


@pytest.mark.asyncio
async def test_wiki_article_streams_rendered_markdown(servicenow_connector) -> None:
    sc = servicenow_connector
    fake = _RenderingFake({"kb_knowledge": [{
        "sys_id": "w1", "number": "KB0000009", "short_description": "Wiki", "article_type": "wiki",
        "wiki": "== Install ==\n* Step one", "text": "", "workflow_state": "published", "active": "true",
        "kb_knowledge_base.active": "true", "valid_to": "", "sys_updated_on": T0,
    }]})
    _wire(sc, fake)

    groups = block_groups(await sc._fetch_article_content("KB0000009"))

    body = groups[0]["data"]
    assert "## Install" in body and "Step one" in body and "==" not in body
    assert any(p.get("sysparm_display_value") == "true" for p in fake.params)


# --- the out-of-box "Has a role" criterion --------------------------------------------------------


def _has_role_model(sc, *, script: str = ServiceNowDefaults.HAS_ROLE_CRITERIA_SCRIPT,
                    plugin: list | None = None) -> tuple[Instance, object]:
    inst = Instance()
    _people(inst)
    inst.role("snc_internal", "plain", "contrib")
    inst.crit(ServiceNowDefaults.HAS_ROLE_CRITERIA, advanced="true", script=script)
    inst.kb("kb", can=(inst.crit("c-any"),), contribute=(ServiceNowDefaults.HAS_ROLE_CRITERIA,))
    inst.article("KB1", "kb", can_read_user_criteria=inst.crit("c-eng", user="eng"),
                 cannot_read_user_criteria=inst.crit("c-no-plain", user="nobody"))
    graph = _connector(sc, inst)
    if plugin is not None:
        real = inst.tables

        def tables() -> dict:
            return {**real(), "v_plugin": plugin}

        inst.tables = tables
    return inst, graph


@pytest.mark.asyncio
async def test_has_role_exact_script_grants_role_holders(servicenow_connector) -> None:
    inst, graph = _has_role_model(servicenow_connector)
    await _sync(servicenow_connector, inst)
    # Contributors read every article of the KB: here everyone holding a role reads KB1.
    assert {"admin", "kadmin", "kmanager", "owner", "kbmgr", "knowledge", "plain", "contrib"} <= graph.readers("KB1")
    assert "hr" not in graph.readers("KB1")  # no role, and not in the article's Can Read


@pytest.mark.asyncio
async def test_has_role_edited_script_grants_nobody(servicenow_connector) -> None:
    edited = ServiceNowDefaults.HAS_ROLE_CRITERIA_SCRIPT.replace(">=1", ">=0")
    inst, graph = _has_role_model(servicenow_connector, script=edited)
    await _sync(servicenow_connector, inst)
    assert "plain" not in graph.readers("KB1") and "knowledge" not in graph.readers("KB1")


@pytest.mark.asyncio
async def test_has_role_whitespace_is_normalised(servicenow_connector) -> None:
    spaced = ServiceNowDefaults.HAS_ROLE_CRITERIA_SCRIPT.replace("\n", "\r\n  ")
    inst, graph = _has_role_model(servicenow_connector, script=spaced)
    await _sync(servicenow_connector, inst)
    assert "knowledge" in graph.readers("KB1")


@pytest.mark.asyncio
async def test_has_role_with_explicit_roles_plugin_needs_snc_internal_and_two_roles(servicenow_connector) -> None:
    inst, graph = _has_role_model(servicenow_connector, plugin=[
        {"sys_id": "p", "id": "com.glide.explicit_roles", "active": "active", "sys_updated_on": T0}])
    inst.role("knowledge", "plain")  # plain: snc_internal + knowledge; contrib: snc_internal only
    await _sync(servicenow_connector, inst)
    readers = graph.readers("KB1")
    assert "plain" in readers
    assert "contrib" not in readers and "knowledge" not in readers


@pytest.mark.asyncio
async def test_has_role_plugin_unreadable_grants_nobody(servicenow_connector) -> None:
    inst, graph = _has_role_model(servicenow_connector, plugin=[
        {"sys_id": "p", "id": "com.glide.explicit_roles", "active": "active", "sys_updated_on": T0, "_acl_hidden": True}])
    await _sync(servicenow_connector, inst)
    assert "knowledge" not in graph.readers("KB1") and "plain" not in graph.readers("KB1")


@pytest.mark.asyncio
async def test_has_role_counts_roles_in_any_state_from_the_runs_role_listing(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, graph = _has_role_model(sc)
    inst.role("itil")
    inst.has_role.append({"user": "hr", "role": "r-itil", "inherited": "false", "state": "requested"})
    await _sync(sc, inst)
    assert "hr" in graph.readers("KB1")  # the script counts the requested role
    assert "hr@x.com" not in graph.group_members["r-itil"]  # the role itself is not held yet
    listings = [q for q in inst.fake.calls_to("sys_user_has_role") if not q.startswith("ORDERBYDESC")]
    assert len(listings) == 2  # the admins and the role listing: "Has a role" reads none of its own


@pytest.mark.asyncio
async def test_has_role_contributors_get_write_on_the_kb(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, graph = _has_role_model(sc)
    await _sync(sc, inst)
    writers = {p.email for p in graph.group_grants["kb"] if p.type == PermissionType.WRITE}
    assert "knowledge@x.com" in writers and "hr@x.com" not in writers


# --- no deletes of groups never stored; inactive groups are not written ----------


@pytest.mark.asyncio
async def test_first_sync_deletes_no_kb_that_was_never_stored(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    inst.kb("kb-off", active=False)
    sc = servicenow_connector
    _connector(sc, inst)
    sc.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)

    await _sync(sc, inst)

    sc.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    sc.data_entities_processor.get_record_group_by_external_id.assert_awaited_once_with(sc.connector_id, "kb-off")


@pytest.mark.asyncio
async def test_inactive_group_not_written_and_stored_one_removed(servicenow_connector) -> None:
    inst = Instance()
    _people(inst)
    inst.group("g-dormant", "hr", active=False)
    inst.kb("kb", can=(inst.crit("c-hr", group="g-hr"),))
    sc = servicenow_connector
    graph = _connector(sc, inst)
    await _sync(sc, inst)
    assert "g-dormant" not in graph.group_nodes

    inst.groups["g-dormant"]["active"] = "true"
    await _sync(sc, inst)
    assert graph.group_members["g-dormant"] == {"hr@x.com"}

    inst.groups["g-dormant"]["active"] = "false"
    inst.hidden.add("other")  # a partial group listing must still remove a group it lists as inactive
    await _sync(sc, inst)
    assert "g-dormant" not in graph.group_nodes
