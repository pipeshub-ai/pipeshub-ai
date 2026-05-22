"""
Broad unit tests for jira_data_center.connector: ADF helpers, parsing utilities,
issue extraction, filter option error paths, and smaller public/async paths that
were previously uncovered without standing up Jira DC.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.exceptions import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import IndexingFilterKey, ListOperator, SyncFilterKey
from app.models.blocks import ChildRecord, ChildType, GroupSubType
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
    _normalize_jira_dc_group_row,
    adf_to_text,
    adf_to_text_with_images,
    build_jira_attachment_filename_lookup,
    extract_jira_wiki_attachment_filenames,
    extract_media_from_adf,
    jira_storage_text_to_markdown_with_images,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    ProgressStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test.jira.dc.cov")
    log.setLevel(logging.CRITICAL)
    return log


def _make_connector() -> JiraDataCenterConnector:
    logger = _make_logger()
    dep = MagicMock()
    dep.org_id = "org-dc-cov"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return JiraDataCenterConnector(logger, dep, dsp, cs, "conn-dc-cov", "team", "u1")


def _ticket_record() -> TicketRecord:
    return TicketRecord(
        id=str(uuid4()),
        org_id="org-dc-cov",
        record_name="[PROJ-1] T",
        record_type=RecordType.TICKET,
        external_record_id="10042",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
        mime_type=MimeTypes.BLOCKS.value,
        source_created_at=1700000000000,
        source_updated_at=1700000000000,
        weburl="https://jira.example/browse/PROJ-1",
        external_record_group_id="proj",
        record_group_type=RecordGroupType.PROJECT,
    )


def _file_record() -> FileRecord:
    return FileRecord(
        id=str(uuid4()),
        org_id="org-dc-cov",
        record_name="shot.png",
        record_type=RecordType.FILE,
        external_record_id="attachment_99",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
        mime_type="image/png",
        parent_external_record_id="10042",
        parent_record_type=RecordType.TICKET,
        external_record_group_id="proj",
        record_group_type=RecordGroupType.PROJECT,
        is_file=True,
        source_updated_at=1700000000000,
    )


def _bind_async_transaction(conn: JiraDataCenterConnector, tx_store: MagicMock) -> None:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=tx_store)
    cm.__aexit__ = AsyncMock(return_value=None)
    conn.data_store_provider.transaction = MagicMock(return_value=cm)


def _sample_project_row(**overrides) -> dict:
    row = {
        "id": "pid-1",
        "key": "PROJ",
        "name": "Project",
        "description": None,
        "url": "https://jira.example/browse/PROJ",
    }
    row.update(overrides)
    return row


def _record_group_proj() -> RecordGroup:
    return RecordGroup(
        org_id="org-dc-cov",
        name="Project",
        short_name="PROJ",
        external_group_id="pid-1",
        connector_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
        group_type=RecordGroupType.PROJECT,
    )


# -----------------------------------------------------------------------------
# ADF + media helpers
# -----------------------------------------------------------------------------


def _adf_doc(*nodes: object) -> dict[str, object]:
    """ADF body shape processed by ``adf_to_text`` (top-level ``content`` list)."""
    return {"content": list(nodes)}


@pytest.mark.parametrize(
    "adf, expected_substr",
    [
        pytest.param(None, "", id="adf_none"),
        pytest.param([], "", id="adf_not_dict"),
        (
            _adf_doc(
                {"type": "paragraph", "content": [{"type": "text", "text": "X", "marks": [{"type": "strong"}]}]}
            ),
            "**X**",
        ),
        (
            _adf_doc(
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "bulletList",
                                    "content": [{"type": "listItem", "content": [{"type": "text", "text": "nested"}]}],
                                },
                                {"type": "paragraph", "content": [{"type": "text", "text": "root"}]},
                            ],
                        }
                    ],
                }
            ),
            "- nested",
        ),
        (
            _adf_doc(
                {
                    "type": "numberedList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "One"}]}],
                        }
                    ],
                }
            ),
            "1. One",
        ),
        (
            _adf_doc(
                {
                    "type": "blockquote",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "quoted"}]}],
                }
            ),
            "> quoted",
        ),
        (
            _adf_doc({"type": "paragraph", "content": [{"type": "bulletList", "content": []}]}),
            "",
        ),
        (
            _adf_doc({"type": "paragraph", "content": [{"type": "text", "text": "a"}]}),
            "a",
        ),
        (
            _adf_doc({"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Hi"}]}),
            "## Hi",
        ),
        (
            _adf_doc(
                {"type": "paragraph", "content": [{"type": "text", "text": "c", "marks": [{"type": "code"}]}]}
            ),
            "`c`",
        ),
        (
            _adf_doc({"type": "paragraph", "content": [{"type": "text", "text": "x", "marks": [{"type": "em"}]}]}),
            "*x*",
        ),
        (
            _adf_doc(
                {"type": "paragraph", "content": [{"type": "text", "text": "del", "marks": [{"type": "strike"}]}]}
            ),
            "~~del~~",
        ),
        (
            _adf_doc(
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "lnk", "marks": [{"type": "link", "attrs": {"href": "https://x"}}]}
                    ],
                }
            ),
            "[lnk](https://x)",
        ),
        (_adf_doc({"type": "codeBlock", "attrs": {"language": "py"}, "content": [{"type": "text", "text": "a=1"}]}), "```py"),
        (_adf_doc({"type": "paragraph", "content": [{"type": "hardBreak"}, {"type": "text", "text": "after"}]}), "after"),
        (
            _adf_doc({"type": "paragraph", "content": [{"type": "rule"}, {"type": "text", "text": ""}]}),
            "---",
        ),
        (_adf_doc({"type": "mention", "attrs": {"text": "bob"}}), "@bob"),
        (_adf_doc({"type": "emoji", "attrs": {"shortName": "rocket"}}), ":rocket:"),
        (
            _adf_doc(
                {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {"url": "https://u"}}]},
            ),
            "[https://u](https://u)",
        ),
        (
            _adf_doc({"type": "paragraph", "content": [{"type": "status", "attrs": {"text": "DONE"}}]}),
            "[DONE]",
        ),
        (
            _adf_doc(
                {"type": "paragraph", "content": [{"type": "date", "attrs": {"timestamp": "1700000000000"}}]},
            ),
            "2023-11-14",
        ),
        (
            _adf_doc(
                {"type": "paragraph", "content": [{"type": "date", "attrs": {"timestamp": "not-int"}}]},
            ),
            "not-int",
        ),
        (
            _adf_doc({"type": "paragraph", "content": [{"type": "placeholder", "attrs": {"text": "PH"}}]}),
            "PH",
        ),
        (
            _adf_doc(
                {
                    "type": "paragraph",
                    "content": [{"type": "unknownWithContent", "content": [{"type": "text", "text": "v"}]}],
                },
            ),
            "v",
        ),
        (
            _adf_doc(
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "content": [{"type": "text", "text": "h", "marks": [{"type": "strong"}]}],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [{"type": "text", "text": "cell|broken"}],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [{"type": "tableCell", "content": [{"type": "text", "text": "r"}]}],
                        },
                    ],
                },
            ),
            "| h",
        ),
        pytest.param(
            _adf_doc({"type": "paragraph", "content": [{"type": "media", "attrs": {"id": "m1", "alt": ""}}]}),
            "![attachment]",
            id="media_fallback_no_cache",
        ),
    ],
)
def test_adf_to_text_variants(adf, expected_substr: str):
    md = adf_to_text(adf)
    assert expected_substr in md if expected_substr else md == ""


def test_adf_to_text_media_with_cache():
    md = adf_to_text(
        {"type": "paragraph", "content": [{"type": "media", "attrs": {"id": "mid", "alt": "pic"}}]},
        {"mid": "data:image/png;base64,abcd"},
    )
    assert "data:image/png;base64,abcd" in md


@pytest.mark.parametrize(
    "root",
    [
        {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "", "alt": "", "__fileName": "a.pdf"}}]},
        {"type": "paragraph", "content": [{"type": "media", "attrs": {"id": "", "alt": "", "__fileName": "ignored"}}]},
    ],
)
def test_extract_media_prefers_filename_and_skips_blank_id(root: dict):
    assert extract_media_from_adf({"content": [root]}) == []


def test_extract_media_root_without_content_wrap():
    adf = {"type": "paragraph", "content": [{"type": "text", "text": "n"}]}
    assert extract_media_from_adf(adf) == []
    nested = extract_media_from_adf(
        {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "media", "attrs": {"id": "zz", "__fileName": "f"}}]}
            ],
        }
    )
    assert nested[0]["filename"] == "f"


@pytest.mark.asyncio
async def test_adf_to_text_with_images_success_and_fetch_error():
    adf = {
        "content": [{"type": "paragraph", "content": [{"type": "media", "attrs": {"id": "mid", "alt": "alt"}}]}]
    }
    fetch_ok = AsyncMock(return_value="data:image/png;base64,xx")
    ok_md = await adf_to_text_with_images(adf, fetch_ok)
    assert "data:image/png;base64,xx" in ok_md

    async def boom(_mid, _alt):
        raise RuntimeError("x")

    err_md = await adf_to_text_with_images(adf, boom)
    assert "attachment" in err_md or "[" in err_md


# -----------------------------------------------------------------------------
# init / filters / connectivity
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_rejects_empty_auth_type_whitespace_only():
    conn = _make_connector()
    conn.config_service.get_config = AsyncMock(
        return_value={"auth": {"authType": "   ", "baseUrl": "https://jira.example", "apiToken": "x"}}
    )
    ok = await conn.init()
    assert ok is False


@pytest.mark.parametrize(
    "configure_mock, expect_ok",
    [
        pytest.param(lambda r: setattr(r, "status", HttpStatusCode.FORBIDDEN.value), False),
        pytest.param(lambda r: setattr(r, "status", HttpStatusCode.OK.value), True),
    ],
)
@pytest.mark.asyncio
async def test_connection_and_access_status_branches(configure_mock, expect_ok: bool):
    conn = _make_connector()
    conn.data_source = MagicMock()

    resp = MagicMock()
    configure_mock(resp)
    ds = MagicMock()
    ds.get_current_user_v2 = AsyncMock(return_value=resp)

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn.test_connection_and_access() is expect_ok


@pytest.mark.asyncio
async def test_connection_and_access_handles_exception_without_crash():
    conn = _make_connector()
    conn.data_source = MagicMock()
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=OSError("net")):
        assert await conn.test_connection_and_access() is False


@pytest.mark.asyncio
async def test_get_project_options_bad_http():
    conn = _make_connector()
    bad = MagicMock()
    bad.status = 500

    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=bad)

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with pytest.raises(RuntimeError, match="Failed to fetch project options"):
            await conn._get_project_options(1, 10, None)


@pytest.mark.asyncio
async def test_get_project_options_json_parse_none():
    conn = _make_connector()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value

    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=resp)

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_safe_json_parse", return_value=None):
            with pytest.raises(RuntimeError, match="Failed to fetch project options"):
                await conn._get_project_options(1, 10, None)


@pytest.mark.asyncio
async def test_get_project_options_unexpected_json_shape():
    conn = _make_connector()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value

    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=resp)

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_safe_json_parse", return_value={"not": "a list"}):
            with pytest.raises(RuntimeError, match="Failed to fetch project options"):
                await conn._get_project_options(1, 10, None)


@pytest.mark.asyncio
async def test_get_project_options_pagination_has_more():
    conn = _make_connector()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value

    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=resp)
    plist = [{"key": k, "name": k} for k in "ABCDEFG"]

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_safe_json_parse", return_value=plist):
            first = await conn._get_project_options(page=1, limit=4, search=None)
            assert first.success and len(first.options) == 4 and first.has_more is True

            second = await conn._get_project_options(page=2, limit=4, search=None)
            assert len(second.options) == 3 and second.has_more is False


@pytest.mark.asyncio
async def test_incremental_sync_delegates_to_run_sync():
    conn = _make_connector()
    with patch.object(JiraDataCenterConnector, "run_sync", new_callable=AsyncMock) as rs:
        await conn.run_incremental_sync()
        rs.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_notification_is_safe_noop():
    conn = _make_connector()
    await conn.handle_webhook_notification({"ignored": True})


@pytest.mark.asyncio
async def test_get_signed_url_empty():
    assert await _make_connector().get_signed_url(_ticket_record()) == ""


# -----------------------------------------------------------------------------
# Timestamps / JSON
# -----------------------------------------------------------------------------


def test_parse_jira_timestamp_variants_coverage():
    conn = _make_connector()
    ms = conn._parse_jira_timestamp("2024-06-01T12:34:56.789+0000")
    assert ms > 0
    conn._parse_jira_timestamp("2024-06-02T09:08:07+0200")

    stripped = conn._parse_jira_timestamp("2024-03-03T08:07:06+03:30")
    assert stripped > 0

    z = conn._parse_jira_timestamp("2025-01-01T01:02:03.004Z")
    assert z > 0

    assert conn._parse_jira_timestamp("") == 0
    assert conn._parse_jira_timestamp("not-any-date-format-xyz-unparseable-string") == 0


def test_safe_json_parse_error_branch():
    conn = _make_connector()
    resp = MagicMock()
    resp.json.side_effect = ValueError("boom")
    assert conn._safe_json_parse(resp, "unit") is None


# -----------------------------------------------------------------------------
# Issue parsing helpers
# -----------------------------------------------------------------------------


class TestParseIssueLinks:
    def test_empty_issue(self):
        conn = _make_connector()
        assert conn._parse_issue_links({}) == []
        assert conn._parse_issue_links(None) == []  # type: ignore[arg-type]

    def test_skips_non_outward_links(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [
                    {"type": {"name": "Relates"}, "inwardIssue": {"id": "2"}},
                    {"type": {}, "outwardIssue": {"id": "ignored"}},
                    "not-a-dict",
                ]
            }
        }
        assert conn._parse_issue_links(issue) == []

    def test_outward_link_record(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [
                    {"type": {"outward": "blocks", "name": "Blocks"}, "outwardIssue": {"id": "2001"}},
                ]
            }
        }
        rows = conn._parse_issue_links(issue)
        assert len(rows) == 1 and rows[0].external_record_id == "2001"

    def test_parse_exception_logged_and_continues(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [
                    {"type": {"outward": "blocks"}, "outwardIssue": {"id": "1"}},
                ]
            }
        }
        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.map_relationship_type",
            side_effect=RuntimeError("boom"),
        ):
            assert conn._parse_issue_links(issue) == []


class TestExtractIssueData:
    def test_full_issue_shape(self):
        conn = _make_connector()
        u = AppUser(
            app_name=Connectors.JIRA_DATA_CENTER,
            connector_id="conn-dc-cov",
            source_user_id="acc-77",
            org_id="org-dc-cov",
            email="u@example.com",
            full_name="U One",
            is_active=True,
        )
        mapper = MagicMock()
        mapper.map_type.return_value = "Task"
        mapper.map_status.return_value = "Open"
        mapper.map_priority.return_value = "High"
        conn.value_mapper = mapper

        issue = {
            "id": "10",
            "key": "K-1",
            "fields": {
                "summary": "Title",
                "description": {"type": "paragraph", "content": [{"type": "text", "text": "d"}]},
                "issuetype": {"name": "Bug", "hierarchyLevel": 1},
                "parent": {"id": "p1", "key": "P-99"},
                "status": {"name": "Doing"},
                "priority": {"name": "Highest"},
                "creator": {"accountId": "acc-77", "displayName": "U One"},
                "reporter": {"accountId": "acc-77", "displayName": "U Rep"},
                "assignee": {"accountId": "acc-77", "displayName": "U Asm"},
                "created": "2024-01-15T10:30:45.123+0000",
                "updated": "2024-01-16T10:30:45+0000",
            },
        }

        row = conn._extract_issue_data(issue, {"acc-77": u})
        assert row["issue_key"] == "K-1"
        assert row["is_epic"] is True
        assert row["parent_external_id"] == "p1"
        assert "Issue Type:" in row["description"]
        assert row["creator_email"] == "u@example.com"
        mapper.map_type.assert_called_once_with("Bug")


@pytest.mark.asyncio
async def test_stream_record_ticket_happy():
    conn = _make_connector()
    conn.data_source = MagicMock()

    with patch.object(conn, "init", new_callable=AsyncMock):
        with patch.object(
            conn, "_process_issue_blockgroups_for_streaming", new_callable=AsyncMock, return_value=b'{"blocks": []}'
        ) as sb:
            rec = _ticket_record()
            resp = await conn.stream_record(rec)
            sb.assert_awaited_once()
            assert resp.media_type == MimeTypes.BLOCKS.value


@pytest.mark.asyncio
async def test_stream_record_file_ok():
    conn = _make_connector()
    conn.data_source = MagicMock()

    r = MagicMock()
    r.status = HttpStatusCode.OK.value
    r.bytes = MagicMock(return_value=b"png-bytes")

    ds = MagicMock()
    ds.get_attachment_content_v2 = AsyncMock(return_value=r)

    with patch.object(conn, "init", new_callable=AsyncMock):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            sr = await conn.stream_record(_file_record())
            assert sr is not None


@pytest.mark.asyncio
async def test_stream_record_unsupported_raises():
    conn = _make_connector()
    bad_rec = MagicMock()
    bad_rec.external_record_id = "x"
    bad_rec.record_type = RecordType.MESSAGE
    with patch.object(conn, "init", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(bad_rec)
        assert exc_info.value.status_code == 400
        assert "Unsupported record type" in exc_info.value.detail


@pytest.mark.asyncio
async def test_reindex_early_exit_and_raises_without_ds():
    conn = _make_connector()
    await conn.reindex_records([])
    conn.data_source = None
    with pytest.raises(Exception, match="DataSource not initialized"):
        await conn.reindex_records([_ticket_record()])


@pytest.mark.asyncio
async def test_check_and_fetch_updated_record_unsupported_returns_none():
    conn = _make_connector()
    rec = MagicMock()
    rec.id = "rid"
    rec.record_type = RecordType.MESSAGE

    row = await conn._check_and_fetch_updated_record(rec)
    assert row is None


# -----------------------------------------------------------------------------
# Group row normalizer + filter routing + init / datasource + sync / permissions
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({}, None),
        # On DC, name is the canonical group identifier; fall back to name
        # as groupId when neither groupId nor id is exposed by the server.
        ({"name": "g"}, {"name": "g", "groupId": "g"}),
        ({"groupId": "1"}, None),
        ({"name": "gn", "groupId": "gid"}, {"name": "gn", "groupId": "gid"}),
        ({"name": "gn", "id": "i2"}, {"name": "gn", "groupId": "i2"}),
    ],
)
def test_normalize_jira_dc_group_row(raw, expected):
    assert _normalize_jira_dc_group_row(raw) == expected


@pytest.mark.asyncio
async def test_get_filter_options_unsupported_key_raises():
    conn = _make_connector()
    with pytest.raises(ValueError, match="Unsupported filter key"):
        await conn.get_filter_options("unknown_filter", page=1, limit=10)


@pytest.mark.asyncio
async def test_get_project_options_search_filters_projects():
    conn = _make_connector()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=resp)
    plist = [
        {"key": "ALPHA", "name": "Alpha One"},
        {"key": "BETA", "name": "Other"},
        {"key": "ALP2", "name": "Alpine"},
    ]
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_safe_json_parse", return_value=plist):
            out = await conn._get_project_options(page=1, limit=10, search="alp")
            assert out.success
            keys = {o.id for o in out.options}
            assert keys == {"ALPHA", "ALP2"}
            assert out.has_more is False


@pytest.mark.asyncio
async def test_init_success_sets_clients():
    conn = _make_connector()
    conn.config_service.get_config = AsyncMock(
        return_value={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": "https://jira.dc.example",
                "apiToken": "tok",
            }
        }
    )
    mock_jc = MagicMock()
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.JiraClient.build_from_services",
        new_callable=AsyncMock,
        return_value=mock_jc,
    ):
        ok = await conn.init()
    assert ok is True
    assert conn.external_client is mock_jc
    assert conn.site_url == "https://jira.dc.example"
    assert conn.data_source is not None


@pytest.mark.asyncio
async def test_init_unsupported_auth_returns_false():
    conn = _make_connector()
    conn.config_service.get_config = AsyncMock(
        return_value={"auth": {"authType": "OAUTH", "baseUrl": "https://x"}}
    )
    assert await conn.init() is False


@pytest.mark.asyncio
async def test_init_missing_base_url_returns_false():
    conn = _make_connector()
    conn.config_service.get_config = AsyncMock(
        return_value={"auth": {"authType": "API_TOKEN", "baseUrl": "  ", "apiToken": "t"}}
    )
    assert await conn.init() is False


@pytest.mark.asyncio
async def test_init_build_raises_returns_false():
    conn = _make_connector()
    conn.config_service.get_config = AsyncMock(
        return_value={"auth": {"authType": "API_TOKEN", "baseUrl": "https://x", "apiToken": "t"}}
    )
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.JiraClient.build_from_services",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        assert await conn.init() is False


@pytest.mark.asyncio
async def test_get_fresh_datasource_requires_init():
    conn = _make_connector()
    conn.external_client = None
    with pytest.raises(RuntimeError, match="init"):
        await conn._get_fresh_datasource()


@pytest.mark.asyncio
async def test_run_sync_early_exit_no_active_users():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
        new_callable=AsyncMock,
        return_value=(None, None),
    ):
        await conn.run_sync()


@pytest.mark.asyncio
async def test_run_sync_happy_path_heavy_mock():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="a1",
        org_id="org-dc-cov",
        email="e@example.com",
        full_name="E",
        is_active=True,
    )
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[MagicMock()])
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
        new_callable=AsyncMock,
        return_value=(None, None),
    ):
        with patch.object(conn, "_fetch_users", new_callable=AsyncMock, return_value=[u]):
            with patch.object(conn, "_sync_user_groups", new_callable=AsyncMock, return_value={}):
                with patch.object(
                    conn, "_fetch_projects", new_callable=AsyncMock, return_value=([], []),
                ):
                    with patch.object(conn, "_sync_project_roles", new_callable=AsyncMock):
                        with patch.object(conn, "_sync_project_lead_roles", new_callable=AsyncMock):
                            with patch.object(
                                conn.issues_sync_point,
                                "read_sync_point",
                                new_callable=AsyncMock,
                                return_value=None,
                            ):
                                with patch.object(
                                    conn.issues_sync_point,
                                    "update_sync_point",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        conn, "_sync_all_project_issues", new_callable=AsyncMock,
                                    ) as spi:
                                        spi.return_value = {
                                            "total_synced": 0, "new_count": 0, "updated_count": 0,
                                        }
                                        await conn.run_sync()
    conn.data_entities_processor.on_new_app_users.assert_awaited()
    conn.data_entities_processor.on_new_record_groups.assert_awaited_with([])


@pytest.mark.asyncio
async def test_get_issues_sync_checkpoint_exception_returns_none():
    conn = _make_connector()
    conn.issues_sync_point.read_sync_point = AsyncMock(side_effect=OSError("etcd"))
    assert await conn._get_issues_sync_checkpoint() is None


@pytest.mark.asyncio
async def test_get_issues_sync_checkpoint_returns_time():
    conn = _make_connector()
    conn.issues_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 99})
    assert await conn._get_issues_sync_checkpoint() == 99


@pytest.mark.asyncio
async def test_update_issues_sync_checkpoint_no_op_when_idle():
    conn = _make_connector()
    conn.issues_sync_point.update_sync_point = AsyncMock()
    await conn._update_issues_sync_checkpoint(
        {"total_synced": 0, "new_count": 0, "updated_count": 0}, project_count=0,
    )
    conn.issues_sync_point.update_sync_point.assert_not_called()


@pytest.mark.asyncio
async def test_update_issues_sync_checkpoint_writes_on_activity():
    conn = _make_connector()
    conn.issues_sync_point.update_sync_point = AsyncMock()
    await conn._update_issues_sync_checkpoint(
        {"total_synced": 1, "new_count": 1, "updated_count": 0}, project_count=0,
    )
    conn.issues_sync_point.update_sync_point.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_project_sync_checkpoint_preserves_existing():
    conn = _make_connector()
    conn.issues_sync_point.read_sync_point = AsyncMock(
        return_value={"last_sync_time": 1, "last_issue_updated": 2},
    )
    conn.issues_sync_point.update_sync_point = AsyncMock()
    await conn._update_project_sync_checkpoint("K", last_sync_time=10)
    call_kw = conn.issues_sync_point.update_sync_point.call_args[0][1]
    assert call_kw["last_sync_time"] == 10
    assert call_kw["last_issue_updated"] == 2


@pytest.mark.asyncio
async def test_fetch_users_paginates_and_maps_legacy_key():
    conn = _make_connector()
    conn.data_source = MagicMock()

    r1 = MagicMock()
    r1.status = HttpStatusCode.OK.value
    r1.json = MagicMock(return_value=[{"accountId": "a", "emailAddress": "x@y", "active": True}])
    r2 = MagicMock()
    r2.status = HttpStatusCode.OK.value
    r2.json = MagicMock(return_value=[])

    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(side_effect=[r1, r2])

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    assert len(users) == 1 and users[0].source_user_id == "a"


@pytest.mark.asyncio
async def test_fetch_users_non_ok_raises():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 500
    bad.text = MagicMock(return_value="err")
    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with pytest.raises(Exception, match="Failed to fetch users"):
            await conn._fetch_users()


@pytest.mark.asyncio
async def test_fetch_users_parses_values_wrapper():
    conn = _make_connector()
    conn.data_source = MagicMock()
    r = MagicMock()
    r.status = HttpStatusCode.OK.value
    r.json = MagicMock(return_value={"values": [{"key": "k1", "emailAddress": "a@b", "active": True}]})
    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(side_effect=[r, MagicMock(status=HttpStatusCode.OK.value, json=MagicMock(return_value=[]))])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    assert len(users) == 1 and users[0].source_user_id == "k1"


@pytest.mark.asyncio
async def test_fetch_application_roles_ok():
    conn = _make_connector()
    conn.data_source = MagicMock()

    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(
        return_value=[
            {"key": "k", "groupDetails": [{"groupId": "g2", "name": "G2"}]},
        ]
    )
    ds = MagicMock()
    ds.get_all_application_roles_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        m = await conn._fetch_application_roles_to_groups_mapping()
    assert "k" in m and m["k"][0]["groupId"] == "g2"


@pytest.mark.asyncio
async def test_fetch_application_roles_non_ok_returns_empty():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 403
    bad.text = MagicMock(return_value="nope")
    ds = MagicMock()
    ds.get_all_application_roles_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_application_roles_to_groups_mapping() == {}
    assert conn._app_roles_forbidden is True


def _perm_resp():
    sch = MagicMock()
    sch.status = HttpStatusCode.OK.value
    sch.json = MagicMock(
        return_value={
            "id": 10,
        }
    )
    grants = MagicMock()
    grants.status = HttpStatusCode.OK.value
    grants.json = MagicMock(
        return_value={
            "permissions": [
                {"permission": "OTHER", "holder": {"type": "group", "value": "x"}},
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "gid1"}},
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "applicationRole", "parameter": "jira-software"},
                },
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {
                        "type": "user",
                        "parameter": "acc",
                        "user": {"emailAddress": "u@example.com"},
                    },
                },
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "anyone"}},
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {
                        "type": "projectRole",
                        "parameter": "10400",
                        "projectRole": {"name": "Developers", "id": "10400"},
                    },
                },
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {
                        "type": "projectRole",
                        "parameter": "1",
                        "projectRole": {"name": "atlassian-addons-project-access"},
                    },
                },
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "unknownThing", "parameter": "z"}},
            ],
        }
    )
    return sch, grants


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_dc_branches():
    conn = _make_connector()
    conn.data_source = MagicMock()
    sch, grants = _perm_resp()
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
    ds.get_permission_scheme_grants_v2 = AsyncMock(return_value=grants)
    app_map = {
        "jira-software": [{"groupId": "expanded-g", "name": "E"}],
    }
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        perms = await conn._fetch_project_permission_scheme("PROJ", app_map)
    types = {(p.entity_type, p.email, p.external_id) for p in perms}
    assert (EntityType.GROUP, None, "gid1") in types
    assert (EntityType.GROUP, None, "expanded-g") in types
    assert (EntityType.USER, "u@example.com", None) in types
    assert (EntityType.ORG, None, "anyone_authenticated") in types
    assert (EntityType.ROLE, None, "PROJ_10400") in types
    assert (EntityType.ROLE, None, "PROJ_projectLead") in types


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_http_fail_returns_empty():
    conn = _make_connector()
    bad = MagicMock()
    bad.status = 404
    bad.text = MagicMock(return_value="")
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_project_permission_scheme("P", {}) == []


@pytest.mark.asyncio
async def test_list_all_projects_dc_success():
    conn = _make_connector()
    conn.data_source = MagicMock()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    raw = [{"key": "K", "id": "1", "name": "N"}]
    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_safe_json_parse", return_value=raw):
            out = await conn._list_all_projects_dc()
    assert out == raw


@pytest.mark.asyncio
async def test_list_all_projects_dc_not_initialized():
    conn = _make_connector()
    conn.data_source = None
    with pytest.raises(ValueError, match="not initialized"):
        await conn._list_all_projects_dc()


@pytest.mark.asyncio
async def test_cleanup_closes_client_and_clears_cache():
    conn = _make_connector()
    inner = MagicMock()
    inner.close = AsyncMock()
    jc = MagicMock()
    jc.get_client = MagicMock(return_value=inner)
    conn.external_client = jc
    conn.data_source = MagicMock()
    conn._issue_attachments_cache["k"] = []
    await conn.cleanup()
    assert conn.external_client is None
    assert conn.data_source is None
    assert conn._issue_attachments_cache == {}
    inner.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_record_attachment_fetch_fails_raises():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 500
    bad.text = MagicMock(return_value="fail")
    ds = MagicMock()
    ds.get_attachment_content_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "init", new_callable=AsyncMock):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            with pytest.raises(Exception, match="Failed to fetch attachment"):
                await conn.stream_record(_file_record())


@pytest.mark.asyncio
async def test_reindex_records_updates_and_reindexes_non_updated_typed():
    conn = _make_connector()
    conn.data_source = MagicMock()
    t_unchanged = _ticket_record()
    t_unchanged.id = "id-unchanged"
    t_updated_src = _ticket_record()
    t_updated_src.id = "id-new"
    updated_tuple = (t_updated_src, [])

    with patch.object(
        conn, "_check_and_fetch_updated_record", new_callable=AsyncMock,
    ) as chk:
        chk.side_effect = [None, updated_tuple]
        await conn.reindex_records([t_unchanged, t_updated_src])

    conn.data_entities_processor.on_new_records.assert_awaited_once()
    on_new_arg = conn.data_entities_processor.on_new_records.call_args[0][0]
    assert len(on_new_arg) == 1 and on_new_arg[0][0] is t_updated_src

    conn.data_entities_processor.reindex_existing_records.assert_awaited_once()
    reindex_arg = conn.data_entities_processor.reindex_existing_records.call_args[0][0]
    assert reindex_arg == [t_unchanged]


class TestExtractIssueDataBranches:
    def test_description_only_issue_type_no_desc_text(self):
        conn = _make_connector()
        mapper = MagicMock()
        mapper.map_type.return_value = "Bug"
        mapper.map_status.return_value = "Open"
        mapper.map_priority.return_value = "Low"
        conn.value_mapper = mapper
        issue = {
            "id": "1",
            "key": "K-2",
            "fields": {
                "summary": "S",
                "issuetype": {"name": "Bug", "hierarchyLevel": 0},
            },
        }
        row = conn._extract_issue_data(issue, {})
        assert row["description"] == "Issue Type: Bug"
        assert row["is_epic"] is False
        assert row["is_subtask"] is False

    def test_linked_issue_outward_enum_fallback(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [
                    {
                        "type": {"name": "CustomLink"},
                        "outwardIssue": {"id": "999"},
                    },
                ],
            },
        }
        rows = conn._parse_issue_links(issue)
        assert len(rows) == 1


# -----------------------------------------------------------------------------
# Deeper connector branches: groups, projects, issues batch, reindex, media
# -----------------------------------------------------------------------------


def test_extract_media_skips_non_dict_nodes_in_content():
    adf = {"content": [{"type": "paragraph", "content": ["bad", {"type": "media", "attrs": {"id": "z", "alt": "a"}}]}]}
    out = extract_media_from_adf(adf)
    assert len(out) == 1 and out[0]["id"] == "z"


def test_adf_to_text_underline_and_link_without_href():
    ul = adf_to_text(
        _adf_doc(
            {"type": "paragraph", "content": [{"type": "text", "text": "u", "marks": [{"type": "underline"}]}]},
        )
    )
    assert "*u*" in ul
    plain = adf_to_text(
        _adf_doc(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "x", "marks": [{"type": "link", "attrs": {}}]}],
            },
        )
    )
    assert plain.strip() == "x"


@pytest.mark.asyncio
async def test_sync_user_groups_batches_groups_and_maps_members():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="acc",
        org_id="org-dc-cov",
        email="member@example.com",
        full_name="Member",
        is_active=True,
    )
    with patch.object(
        conn,
        "_fetch_groups",
        new_callable=AsyncMock,
        return_value=[{"groupId": "g1", "name": "G1"}],
    ):
        with patch.object(
            conn,
            "_fetch_group_members",
            new_callable=AsyncMock,
            return_value=["acc", "missing-key"],
        ):
            mmap = await conn._sync_user_groups([u])
    conn.data_entities_processor.on_new_user_groups.assert_awaited()
    assert mmap["g1"][0].email == "member@example.com"
    assert mmap["G1"] == mmap["g1"]


@pytest.mark.asyncio
async def test_sync_user_groups_no_groups_returns_empty():
    conn = _make_connector()
    conn.data_source = MagicMock()
    with patch.object(conn, "_fetch_groups", new_callable=AsyncMock, return_value=[]):
        assert await conn._sync_user_groups([]) == {}
    conn.data_entities_processor.on_new_user_groups.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_groups_paginates_dict_payload_is_last():
    conn = _make_connector()
    conn.data_source = MagicMock()
    r1 = MagicMock()
    r1.status = HttpStatusCode.OK.value
    r1.json = MagicMock(
        return_value={
            "values": [{"name": "a", "groupId": "1"}],
            "isLast": False,
        }
    )
    r2 = MagicMock()
    r2.status = HttpStatusCode.OK.value
    r2.json = MagicMock(
        return_value={
            "values": [{"name": "b", "groupId": "2"}],
            "isLast": True,
        }
    )
    ds = MagicMock()
    ds.bulk_get_groups_v2 = AsyncMock(side_effect=[r1, r2])
    # DC pagination stops after a short page unless maxResults/page size matches: with default
    # GROUP_PAGE_SIZE=50 a single-row ``isLast: false`` batch still breaks (len < max_results).
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.GROUP_PAGE_SIZE",
        1,
    ):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            groups = await conn._fetch_groups()
    assert [g["groupId"] for g in groups] == ["1", "2"]


@pytest.mark.asyncio
async def test_fetch_groups_non_ok_stops():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 500
    bad.text = MagicMock(return_value="err")
    ds = MagicMock()
    ds.bulk_get_groups_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_groups() == []


@pytest.mark.asyncio
async def test_fetch_group_members_missing_group_id():
    conn = _make_connector()
    conn.data_source = MagicMock()
    assert await conn._fetch_group_members("", "name") == []


@pytest.mark.asyncio
async def test_fetch_group_members_non_ok():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 403
    bad.text = MagicMock(return_value="nope")
    ds = MagicMock()
    ds.get_users_from_group_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_group_members("gid", "G") == []


@pytest.mark.asyncio
async def test_sync_project_roles_user_and_group_actors():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="u1",
        org_id="org-dc-cov",
        email="actor@example.com",
        full_name="Actor",
        is_active=True,
    )
    list_resp = MagicMock()
    list_resp.status = HttpStatusCode.OK.value
    list_resp.json = MagicMock(return_value={"Dev": "https://jira/rest/api/2/project/P/role/10400"})

    role_resp = MagicMock()
    role_resp.status = HttpStatusCode.OK.value
    role_resp.json = MagicMock(
        return_value={
            "name": "Dev",
            "actors": [
                {
                    "type": "atlassian-user-role-actor",
                    "actorUser": {"accountId": "u1", "emailAddress": "actor@example.com"},
                },
                {"type": "atlassian-group-role-actor", "groupId": "gx", "name": "GX"},
            ],
        }
    )

    ds = MagicMock()
    ds.get_project_roles_v2 = AsyncMock(return_value=list_resp)
    ds.get_project_role_v2 = AsyncMock(return_value=role_resp)
    gmember = [u]

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        await conn._sync_project_roles(
            ["P"],
            [u],
            {"gx": gmember},
        )
    conn.data_entities_processor.on_new_app_roles.assert_awaited()
    args = conn.data_entities_processor.on_new_app_roles.call_args[0][0]
    assert len(args) == 1
    assert args[0][1] == [u, u]


@pytest.mark.asyncio
async def test_sync_project_lead_roles_with_and_without_lead_user():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="lead-acc",
        org_id="org-dc-cov",
        email="lead@example.com",
        full_name="Lead",
        is_active=True,
    )
    raw = [
        {"key": "K1", "lead": {"accountId": "lead-acc", "displayName": "Lead"}},
        {"key": "K2", "lead": {"displayName": "NoAcc"}},
        {"key": "K3"},
    ]
    await conn._sync_project_lead_roles(raw, [u])
    conn.data_entities_processor.on_new_app_roles.assert_awaited()
    batch = conn.data_entities_processor.on_new_app_roles.call_args[0][0]
    assert len(batch) == 3
    assert batch[0][1] == [u]
    assert batch[1][1] == []
    assert batch[2][1] == []


@pytest.mark.asyncio
async def test_fetch_projects_include_exclude_and_empty_keys():
    conn = _make_connector()
    conn.data_source = MagicMock()
    all_rows = [_sample_project_row(key="A", id="1"), _sample_project_row(key="B", id="2")]
    with patch.object(conn, "_list_all_projects_dc", new_callable=AsyncMock, return_value=all_rows):
        with patch.object(conn, "_fetch_application_roles_to_groups_mapping", new_callable=AsyncMock, return_value={}):
            with patch.object(conn, "_fetch_project_permission_scheme", new_callable=AsyncMock, return_value=[]):
                _, inc = await conn._fetch_projects(["B"], ListOperator.IN)
                _, exc = await conn._fetch_projects(["B"], ListOperator.NOT_IN)
                empty_gr, empty_raw = await conn._fetch_projects([], ListOperator.IN)
    assert [r["key"] for r in inc] == ["B"]
    assert [r["key"] for r in exc] == ["A"]
    assert [r["key"] for r in empty_raw] == ["A", "B"]
    assert len(empty_gr) == 2


@pytest.mark.asyncio
async def test_fetch_projects_raises_without_data_source():
    conn = _make_connector()
    conn.data_source = None
    with pytest.raises(ValueError, match="not initialized"):
        await conn._fetch_projects()


@pytest.mark.asyncio
async def test_list_all_projects_dc_bad_http_and_parse_shape():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 500
    bad.text = MagicMock(return_value="x")
    ds = MagicMock()
    ds.list_projects_get_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with pytest.raises(Exception, match="Failed to fetch projects"):
            await conn._list_all_projects_dc()

    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ds2 = MagicMock()
    ds2.list_projects_get_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
        with patch.object(conn, "_safe_json_parse", return_value=None):
            with pytest.raises(Exception, match="Failed to parse project list"):
                await conn._list_all_projects_dc()

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
        with patch.object(conn, "_safe_json_parse", return_value={"a": 1}):
            with pytest.raises(Exception, match="Unexpected project list shape"):
                await conn._list_all_projects_dc()


@pytest.mark.asyncio
async def test_sync_all_project_issues_continues_on_project_error():
    conn = _make_connector()
    conn.data_source = MagicMock()
    g1 = _record_group_proj()
    g2 = RecordGroup(
        org_id="org-dc-cov",
        name="P2",
        short_name="P2",
        external_group_id="p2",
        connector_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
        group_type=RecordGroupType.PROJECT,
    )

    async def boom(project, users, ts):
        if project.short_name == "PROJ":
            raise RuntimeError("boom")
        return {"total_synced": 2, "new_count": 1, "updated_count": 1}

    with patch.object(conn, "_sync_project_issues", new_callable=AsyncMock, side_effect=boom):
        stats = await conn._sync_all_project_issues([(g1, []), (g2, [])], [], None)
    assert stats["total_synced"] == 2


@pytest.mark.asyncio
async def test_sync_project_issues_updates_checkpoint_on_empty_batch_with_ts():
    conn = _make_connector()
    conn.data_source = MagicMock()
    proj = _record_group_proj()

    async def mock_batched(*_a, **_kw):
        yield [], False, 999_000

    with patch.object(conn, "_get_project_sync_checkpoint", new_callable=AsyncMock, return_value={"last_sync_time": 1}):
        with patch.object(conn, "_fetch_issues_batched", side_effect=mock_batched):
            with patch.object(conn, "_update_project_sync_checkpoint", new_callable=AsyncMock) as up:
                await conn._sync_project_issues(proj, [], None)
    up.assert_awaited()


@pytest.mark.asyncio
async def test_process_new_records_sorts_epic_before_child_and_stats():
    conn = _make_connector()
    child = _ticket_record()
    child.parent_external_record_id = "parent-ext"
    child.version = 0
    root = _ticket_record()
    root.parent_external_record_id = None
    root.version = 1
    stats = {"new_count": 0, "updated_count": 0}
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.BATCH_PROCESSING_SIZE",
        10,
    ):
        await conn._process_new_records([(child, []), (root, [])], "PROJ", stats)
    batch = conn.data_entities_processor.on_new_records.call_args[0][0]
    assert batch[0][0] is root and batch[1][0] is child
    assert stats["new_count"] == 1 and stats["updated_count"] == 1


@pytest.mark.asyncio
async def test_fetch_issues_batched_empty_page():
    conn = _make_connector()
    conn.data_source = MagicMock()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    resp.json = MagicMock(return_value={"issues": [], "total": 0})
    ds = MagicMock()
    ds.search_issues_post_v2 = AsyncMock(return_value=resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        out = []
        async for row in conn._fetch_issues_batched("PROJ", "pid", [], None, None, False):
            out.append(row)
    assert out == [([], False, None)]


@pytest.mark.asyncio
async def test_build_issue_records_skips_unchanged_when_not_full_sync():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tx = MagicMock()
    ex = _ticket_record()
    ex.source_updated_at = 1700000000000
    ex.version = 2
    tx.get_record_by_external_id = AsyncMock(return_value=ex)
    _bind_async_transaction(conn, tx)
    issue = {
        "id": ex.external_record_id,
        "key": "K-9",
        "fields": {
            "summary": "S",
            "updated": "2024-11-15T01:23:45.000+0000",
            "created": "2024-11-15T01:23:45.000+0000",
            "issuetype": {"name": "Task"},
        },
    }
    conn._parse_jira_timestamp = MagicMock(return_value=1700000000000)  # type: ignore[method-assign]
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    rows = await conn._build_issue_records([issue], "pid", [], tx, is_new_project=False)
    assert rows == []


@pytest.mark.asyncio
async def test_build_issue_records_full_sync_keeps_unchanged():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tx = MagicMock()
    ex = _ticket_record()
    ex.source_updated_at = 1700000000000
    ex.version = 2
    tx.get_record_by_external_id = AsyncMock(return_value=ex)
    _bind_async_transaction(conn, tx)
    issue = {
        "id": ex.external_record_id,
        "key": "K-9",
        "fields": {
            "summary": "S",
            "updated": "2024-11-15T01:23:45.000+0000",
            "created": "2024-11-15T01:23:45.000+0000",
            "issuetype": {"name": "Task"},
        },
    }
    conn._parse_jira_timestamp = MagicMock(return_value=1700000000000)  # type: ignore[method-assign]
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    with patch.object(conn, "_fetch_issue_attachments", new_callable=AsyncMock, return_value=[]):
        rows = await conn._build_issue_records([issue], "pid", [], tx, is_new_project=True)
    assert len(rows) == 1
    assert isinstance(rows[0][0], TicketRecord)


@pytest.mark.asyncio
async def test_fetch_issue_attachments_builds_file_records():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    fields = {
        "attachment": [
            {"id": "77", "filename": "a.png", "mimeType": "image/png", "size": 10, "created": "2024-01-01T00:00:00.000+0000"},
        ]
    }
    out = await conn._fetch_issue_attachments(
        "10",
        "K-1",
        fields,
        [],
        "pid",
        RecordGroupType.PROJECT,
        tx,
        parent_node_id="node-1",
    )
    assert len(out) == 1
    assert out[0][0].external_record_id == "attachment_77"


@pytest.mark.asyncio
async def test_fetch_issue_attachments_swallows_inner_failure():
    conn = _make_connector()
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("db"))
    fields = {"attachment": [{"id": "1", "filename": "f"}]}
    out = await conn._fetch_issue_attachments("10", "K-1", fields, [], "pid", RecordGroupType.PROJECT, tx)
    assert out == []


def test_organize_issue_comments_to_threads_sorts():
    conn = _make_connector()
    threads = conn._organize_issue_comments_to_threads(
        [
            {"id": "2", "parent": {"id": "1"}, "created": "2024-01-02T00:00:00.000+0000"},
            {"id": "1", "parent": {}, "created": "2024-01-01T00:00:00.000+0000"},
        ]
    )
    assert len(threads) == 1
    assert [c["id"] for c in threads[0]] == ["1", "2"]


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_minimal_description():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    with patch.object(conn, "_create_media_fetcher", return_value=AsyncMock(return_value=None)):
        container = await conn._parse_issue_to_blocks(
            {"id": "99", "key": "K-1", "fields": {"summary": "Title"}},
            issue_key="K-1",
            weburl="https://jira.example/browse/K-1",
        )
    assert container.block_groups[0].data.startswith("# [K-1] Title")
    assert container.block_groups[0].name == "[K-1] Title"


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_plain_string_description():
    """Legacy DC returns description as wiki/plain string, not ADF."""
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    body = "h1. Overview\n\nConnector syncs issues from Jira Data Center."
    with patch.object(conn, "_create_media_fetcher", return_value=AsyncMock(return_value=None)):
        container = await conn._parse_issue_to_blocks(
            {
                "id": "99",
                "key": "PA-1203",
                "fields": {"summary": "Jira Data Center Connector", "description": body},
            },
            issue_key="PA-1203",
            weburl="https://jira.example/browse/PA-1203",
        )
    assert container.block_groups[0].data.startswith("# [PA-1203] Jira Data Center Connector\n\n")
    assert body in container.block_groups[0].data
    assert container.block_groups[0].name == "[PA-1203] Jira Data Center Connector"


def test_extract_jira_wiki_attachment_filenames():
    text = 'Guide\n!image-20260516-133226.png|width=983,alt="image-20260516-133226.png"!'
    assert extract_jira_wiki_attachment_filenames(text) == ["image-20260516-133226.png"]


@pytest.mark.asyncio
async def test_jira_storage_text_to_markdown_with_images_inlines_png():
    mime = {"13352": "image/png"}
    lookup = build_jira_attachment_filename_lookup(
        mime,
        None,
        [{"id": "13352", "filename": "image-20260516-133226.png", "mimeType": "image/png"}],
    )

    async def fetcher(att_id: str, filename: str) -> str:
        assert att_id == "13352"
        return "data:image/png;base64,QUJD"

    wiki = 'Title\n!image-20260516-133226.png|width=983,alt="x"!'
    md, embedded = await jira_storage_text_to_markdown_with_images(
        wiki, fetcher, lookup, mime
    )
    assert "data:image/png;base64,QUJD" in md
    assert "!image-20260516" not in md
    assert embedded == {"13352"}


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_wiki_image_in_description():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    wiki_body = (
        "Jira data center connector email guide\n\n\n"
        '!image-20260516-133226.png|width=983,alt="image-20260516-133226.png"!'
    )

    async def fetcher(att_id: str, filename: str) -> str:
        return "data:image/png;base64,EMBED"

    with patch.object(conn, "_create_media_fetcher", return_value=fetcher):
        container = await conn._parse_issue_to_blocks(
            {
                "id": "99",
                "key": "PST-15",
                "fields": {
                    "summary": "jira email visibilty guide",
                    "description": wiki_body,
                    "attachment": [
                        {
                            "id": "13352",
                            "filename": "image-20260516-133226.png",
                            "mimeType": "image/png",
                        }
                    ],
                },
            },
            issue_key="PST-15",
            weburl="https://jira.example/browse/PST-15",
        )
    data = container.block_groups[0].data
    assert "data:image/png;base64,EMBED" in data
    assert "!image-20260516-133226.png" not in data
    assert not container.block_groups[0].children_records


@pytest.mark.asyncio
async def test_create_media_fetcher_delegates_to_fetch_media():
    conn = _make_connector()

    async def fake_fetch(issue_id: str, media_id: str, alt: str):
        assert issue_id == "iss"
        return f"data:image/png;base64,{media_id}"

    with patch.object(conn, "_fetch_media_as_base64", new_callable=AsyncMock, side_effect=fake_fetch):
        fetcher = conn._create_media_fetcher("iss")
        out = await fetcher("mid", "a")
    assert "base64" in out


@pytest.mark.asyncio
async def test_get_issue_attachments_cached_hit_and_miss():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn._issue_attachments_cache["1"] = [{"id": "9"}]
    assert await conn._get_issue_attachments_cached("1") == [{"id": "9"}]

    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value={"fields": {"attachment": [{"id": "10", "filename": "f"}]}})
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        att = await conn._get_issue_attachments_cached("2")
    assert att[0]["id"] == "10"
    assert conn._issue_attachments_cache["2"] == att


@pytest.mark.asyncio
async def test_fetch_media_as_base64_happy_path_and_bad_content_status():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn._issue_attachments_cache["iss"] = [{"id": "5", "filename": "x.png", "mimeType": "image/png"}]

    ok_content = MagicMock()
    ok_content.status = HttpStatusCode.OK.value
    ok_content.bytes = MagicMock(return_value=b"\xff")
    bad_content = MagicMock()
    bad_content.status = 500
    ds = MagicMock()
    ds.get_attachment_content_v2 = AsyncMock(side_effect=[ok_content, bad_content])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        uri = await conn._fetch_media_as_base64("iss", "5", "alt")
        assert uri and uri.startswith("data:image/png;base64,")
        assert await conn._fetch_media_as_base64("iss", "999", "does-not-match-anything") is None


@pytest.mark.asyncio
async def test_fetch_media_as_base64_matches_partial_filename():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn._issue_attachments_cache["iss"] = [{"id": "1", "filename": "long-name.pdf", "mimeType": "application/pdf"}]
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    resp.bytes = MagicMock(return_value=b"abc")
    ds = MagicMock()
    ds.get_attachment_content_v2 = AsyncMock(return_value=resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        uri = await conn._fetch_media_as_base64("iss", "nope", "name.pdf")
    assert uri.startswith("data:application/pdf;base64,")


def test_create_attachment_file_record_sets_auto_index_off_for_attachments_filter():
    conn = _make_connector()
    m = MagicMock()
    m.is_enabled = MagicMock(return_value=False)
    conn.indexing_filters = m
    fr = conn._create_attachment_file_record(
        attachment_id="1",
        filename="f.txt",
        mime_type="text/plain",
        file_size=3,
        created_at=1000,
        parent_issue_id="p",
        parent_node_id=None,
        project_id="proj",
        weburl=None,
    )
    assert fr.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_extra_holder_branches():
    conn = _make_connector()
    conn.data_source = MagicMock()
    sch = MagicMock()
    sch.status = HttpStatusCode.OK.value
    sch.json = MagicMock(return_value={"id": 1})
    grants = MagicMock()
    grants.status = HttpStatusCode.OK.value
    grants.json = MagicMock(
        return_value={
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {
                        "type": "user",
                        "parameter": "acc",
                        "user": {},
                    },
                },
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "sd.customer.portal.only"}},
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "groupCustomField", "parameter": "x"}},
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "applicationRole", "parameter": "no-map-key"},
                },
            ],
        }
    )
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
    ds.get_permission_scheme_grants_v2 = AsyncMock(return_value=grants)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        perms = await conn._fetch_project_permission_scheme("P", {})
    # "no-map-key" is unresolvable and _app_roles_forbidden is False, so it's skipped (no ORG over-grant)
    assert not any(p.external_id == "no-map-key" for p in perms)
    assert not any(p.entity_type == EntityType.USER for p in perms)


@pytest.mark.asyncio
async def test_reindex_records_skips_base_record_class_for_reindex():
    conn = _make_connector()
    conn.data_source = MagicMock()
    base = Record(
        org_id="org-dc-cov",
        record_name="base",
        record_type=RecordType.TICKET,
        external_record_id="1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
    )
    t = _ticket_record()
    with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=None):
        await conn.reindex_records([t, base])
    conn.data_entities_processor.reindex_existing_records.assert_awaited_once()
    assert conn.data_entities_processor.reindex_existing_records.call_args[0][0] == [t]


@pytest.mark.asyncio
async def test_reindex_records_check_error_skips_that_record():
    conn = _make_connector()
    conn.data_source = MagicMock()
    t1 = _ticket_record()
    t2 = _ticket_record()
    with patch.object(
        conn,
        "_check_and_fetch_updated_record",
        new_callable=AsyncMock,
        side_effect=[RuntimeError("x"), None],
    ):
        await conn.reindex_records([t1, t2])
    conn.data_entities_processor.reindex_existing_records.assert_awaited_once()
    assert conn.data_entities_processor.reindex_existing_records.call_args[0][0] == [t2]


@pytest.mark.asyncio
async def test_reindex_records_not_implemented_on_reindex_is_swallowed():
    conn = _make_connector()
    conn.data_source = MagicMock()
    t = _ticket_record()
    conn.data_entities_processor.reindex_existing_records = AsyncMock(side_effect=NotImplementedError("kafka"))
    with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=None):
        await conn.reindex_records([t])
    conn.data_entities_processor.reindex_existing_records.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_and_fetch_updated_record_outer_exception_returns_none():
    conn = _make_connector()
    conn.data_source = MagicMock()
    with patch.object(conn, "_check_and_fetch_updated_issue", new_callable=AsyncMock, side_effect=OSError("x")):
        assert await conn._check_and_fetch_updated_record(_ticket_record()) is None


@pytest.mark.asyncio
async def test_check_and_fetch_updated_issue_gone_and_http_error():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    rec = _ticket_record()
    rec.external_record_id = "42"
    gone = MagicMock()
    gone.status = HttpStatusCode.GONE.value
    bad = MagicMock()
    bad.status = 500
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(side_effect=[gone, bad])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._check_and_fetch_updated_issue(rec) is None
        assert await conn._check_and_fetch_updated_issue(rec) is None


@pytest.mark.asyncio
async def test_check_and_fetch_updated_issue_unchanged_timestamp():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    rec = _ticket_record()
    rec.external_record_id = "42"
    rec.source_updated_at = 1700000000000
    rec.version = 1
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(
        return_value={"fields": {"updated": "2024-11-15T01:23:45.000+0000", "project": {"id": "p"}}}
    )
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_parse_jira_timestamp", return_value=1700000000000):
            assert await conn._check_and_fetch_updated_issue(rec) is None


@pytest.mark.asyncio
async def test_check_and_fetch_updated_issue_returns_when_timestamp_changes():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    rec = _ticket_record()
    rec.external_record_id = "42"
    rec.source_updated_at = 100
    rec.version = 1
    fresh = MagicMock()
    fresh.status = HttpStatusCode.OK.value
    fresh.json = MagicMock(
        return_value={
            "id": "42",
            "key": "K-1",
            "fields": {
                "summary": "S",
                "updated": "2025-01-01T00:00:00.000+0000",
                "created": "2025-01-01T00:00:00.000+0000",
                "issuetype": {"name": "Task"},
                "creator": {"accountId": "c1", "emailAddress": "c@e", "displayName": "C"},
            },
        }
    )
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=fresh)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_parse_jira_timestamp", return_value=200):
            row = await conn._check_and_fetch_updated_issue(rec)
    assert row is not None and row[0].external_record_id == "42" and row[0].version == 2


@pytest.mark.asyncio
async def test_check_and_fetch_updated_attachment_missing_parent_and_unchanged():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    f = _file_record()
    f.parent_external_record_id = None
    assert await conn._check_and_fetch_updated_attachment(f) is None

    f2 = _file_record()
    f2.source_updated_at = 50
    issue_resp = MagicMock()
    issue_resp.status = HttpStatusCode.OK.value
    issue_resp.json = MagicMock(
        return_value={
            "key": "K-1",
            "fields": {"attachment": [{"id": "99", "created": "2024-06-01T00:00:00.000+0000", "filename": "z"}]},
        }
    )
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    _bind_async_transaction(conn, tx)
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=issue_resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_parse_jira_timestamp", return_value=50):
            assert await conn._check_and_fetch_updated_attachment(f2) is None


# -----------------------------------------------------------------------------
# Coverage push: ADF branches, sync filters, groups/members list payloads, streaming
# -----------------------------------------------------------------------------


def test_extract_media_from_adf_rejects_non_dict_doc():
    assert extract_media_from_adf(None) == []  # type: ignore[arg-type]
    assert extract_media_from_adf([]) == []  # type: ignore[arg-type]


def test_adf_to_text_single_root_node_without_top_level_content():
    md = adf_to_text({"type": "text", "text": "solo"})
    assert md == "solo"


@pytest.mark.parametrize(
    "fragment, needle",
    [
        (
            {
                "type": "bulletList",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x"}]}],
            },
            "- x",
        ),
        (
            {
                "type": "unorderedList",
                "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "u"}]}]},
                ],
            },
            "- u",
        ),
        (
            {
                "type": "orderedList",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "orphan"}]}],
            },
            "1. orphan",
        ),
        (
            {
                "type": "taskList",
                "content": [
                    {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [{"type": "text", "text": "done"}]},
                ],
            },
            "[x]",
        ),
        (
            {
                "type": "taskList",
                "content": [
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "todo"}]},
                ],
            },
            "[ ]",
        ),
        (
            {
                "type": "decisionList",
                "content": [{"type": "decisionItem", "attrs": {"state": "DECIDED"}, "content": [{"type": "text", "text": "d"}]}],
            },
            "✓",
        ),
        (
            {
                "type": "decisionList",
                "content": [{"type": "decisionItem", "attrs": {"state": "OPEN"}, "content": [{"type": "text", "text": "d"}]}],
            },
            "◇",
        ),
        (
            {"type": "panel", "attrs": {"panelType": "note"}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "p"}]}]},
            "NOTE",
        ),
        (
            {"type": "inlineCard", "attrs": {"url": "https://card.example"}},
            "https://card.example",
        ),
        (
            {"type": "inlineCode", "text": "ic"},
            "`ic`",
        ),
        (
            {"type": "listItem", "content": [{"type": "text", "text": "li"}]},
            "li",
        ),
        (
            {
                "type": "blockquote",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "a"}, {"type": "hardBreak"}, {"type": "text", "text": "b"}]}],
            },
            "> a",
        ),
        (
            {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "h"}]},
            "### h",
        ),
        (
            {"type": "expand", "attrs": {"title": "More"}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "in"}]}]},
            "**More**",
        ),
        (
            {
                "type": "layoutSection",
                "content": [
                    {"type": "layoutColumn", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "L"}]}]},
                    {"type": "layoutColumn", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "R"}]}]},
                ],
            },
            "L",
        ),
        (
            {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "m1", "alt": "pic"}}]},
            "![pic]",
        ),
        (
            {"type": "unknownWithContent", "content": [{"type": "text", "text": "fall"}]},
            "fall",
        ),
        (
            {"type": "paragraph", "content": [{"type": "date", "attrs": {"timestamp": "not-int"}}]},
            "not-int",
        ),
        (
            {"type": "paragraph", "content": [{"type": "emoji", "attrs": {"shortName": "", "text": "E"}}]},
            "E",
        ),
        (
            {"type": "paragraph", "content": [{"type": "mention", "attrs": {"id": "mid"}}]},
            "@mid",
        ),
    ],
)
def test_adf_to_text_more_node_types(fragment: dict, needle: str):
    assert needle in adf_to_text(_adf_doc(fragment))


def test_adf_to_text_media_in_list_depth_uses_cache_shape():
    adf = {
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{"type": "media", "attrs": {"id": "z9", "alt": "a"}}],
                    },
                ],
            },
        ]
    }
    md = adf_to_text(adf, {"z9": "data:image/png;base64,xx"})
    assert "data:image/png;base64,xx" in md


@pytest.mark.asyncio
async def test_adf_to_text_with_images_non_dict_and_fetch_exception():
    assert await adf_to_text_with_images(None, AsyncMock()) == ""  # type: ignore[arg-type]

    async def boom(_a, _b):
        raise RuntimeError("fetch")

    adf = {"content": [{"type": "paragraph", "content": [{"type": "media", "attrs": {"id": "x", "alt": ""}}]}]}
    md = await adf_to_text_with_images(adf, boom)
    assert "attachment" in md or "!" in md


@pytest.mark.asyncio
async def test_fetch_users_parse_failure_stops_fetch():
    conn = _make_connector()
    conn.data_source = MagicMock()
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value=None)
    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    assert users == []


@pytest.mark.asyncio
async def test_fetch_users_skips_inactive_and_missing_email():
    conn = _make_connector()
    conn.data_source = MagicMock()
    batch = [
        {"accountId": "a1", "emailAddress": "ok@example.com", "active": True},
        {"accountId": "a2", "emailAddress": "no@example.com", "active": False},
        {"accountId": "a3", "active": True},
        {"emailAddress": "orphan@example.com", "active": True},
    ]
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value=batch)
    empty = MagicMock()
    empty.status = HttpStatusCode.OK.value
    empty.json = MagicMock(return_value=[])
    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(side_effect=[ok, empty])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    assert len(users) == 1 and users[0].email == "ok@example.com"


@pytest.mark.asyncio
async def test_fetch_application_roles_json_raises_returns_empty():
    conn = _make_connector()
    conn.data_source = MagicMock()
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(side_effect=ValueError("bad json"))
    ds = MagicMock()
    ds.get_all_application_roles_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_application_roles_to_groups_mapping() == {}


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_grants_not_ok():
    conn = _make_connector()
    conn.data_source = MagicMock()
    sch = MagicMock()
    sch.status = HttpStatusCode.OK.value
    sch.json = MagicMock(return_value={"id": 9})
    bad_grants = MagicMock()
    bad_grants.status = 500
    bad_grants.text = MagicMock(return_value="nope")
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
    ds.get_permission_scheme_grants_v2 = AsyncMock(return_value=bad_grants)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_project_permission_scheme("P", {}) == []


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_dedupes_duplicate_holders():
    conn = _make_connector()
    conn.data_source = MagicMock()
    sch = MagicMock()
    sch.status = HttpStatusCode.OK.value
    sch.json = MagicMock(return_value={"id": 1})
    grants = MagicMock()
    grants.status = HttpStatusCode.OK.value
    grants.json = MagicMock(
        return_value={
            "permissions": [
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "g1"}},
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "g1"}},
            ],
        },
    )
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
    ds.get_permission_scheme_grants_v2 = AsyncMock(return_value=grants)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        perms = await conn._fetch_project_permission_scheme("P", {})
    assert len(perms) == 1


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_grants_json_raises():
    conn = _make_connector()
    conn.data_source = MagicMock()
    sch = MagicMock()
    sch.status = HttpStatusCode.OK.value
    sch.json = MagicMock(return_value={"id": 1})
    grants = MagicMock()
    grants.status = HttpStatusCode.OK.value
    grants.json = MagicMock(side_effect=RuntimeError("x"))
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
    ds.get_permission_scheme_grants_v2 = AsyncMock(return_value=grants)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_project_permission_scheme("P", {}) == []


@pytest.mark.asyncio
async def test_fetch_project_duplicate_app_role_group_skipped_when_seen():
    conn = _make_connector()
    conn.data_source = MagicMock()
    sch = MagicMock()
    sch.status = HttpStatusCode.OK.value
    sch.json = MagicMock(return_value={"id": 1})
    grants = MagicMock()
    grants.status = HttpStatusCode.OK.value
    grants.json = MagicMock(
        return_value={
            "permissions": [
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "same-g"}},
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "applicationRole", "parameter": "jira"},
                },
            ],
        }
    )
    ds = MagicMock()
    ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
    ds.get_permission_scheme_grants_v2 = AsyncMock(return_value=grants)
    app_map = {"jira": [{"groupId": "same-g", "name": "Dup"}]}
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        perms = await conn._fetch_project_permission_scheme("P", app_map)
    assert sum(1 for p in perms if p.external_id == "same-g") == 1


@pytest.mark.asyncio
async def test_sync_user_groups_skips_invalid_group_row():
    conn = _make_connector()
    conn.data_source = MagicMock()
    with patch.object(
        conn,
        "_fetch_groups",
        new_callable=AsyncMock,
        return_value=[{"name": "only-name"}, {"groupId": "g1", "name": "G1"}],
    ):
        with patch.object(conn, "_fetch_group_members", new_callable=AsyncMock, return_value=[]):
            await conn._sync_user_groups([])
    conn.data_entities_processor.on_new_user_groups.assert_awaited()


@pytest.mark.asyncio
async def test_sync_user_groups_single_group_failure_continues():
    conn = _make_connector()
    conn.data_source = MagicMock()

    async def flaky(_gid, _name):
        if _gid == "bad":
            raise RuntimeError("members")
        return []

    with patch.object(
        conn,
        "_fetch_groups",
        new_callable=AsyncMock,
        return_value=[
            {"groupId": "bad", "name": "B"},
            {"groupId": "ok", "name": "O"},
        ],
    ):
        with patch.object(conn, "_fetch_group_members", new_callable=AsyncMock, side_effect=flaky):
            await conn._sync_user_groups([])
    assert conn.data_entities_processor.on_new_user_groups.await_count >= 1


@pytest.mark.asyncio
async def test_fetch_groups_list_payload_two_calls():
    conn = _make_connector()
    conn.data_source = MagicMock()
    r1 = MagicMock()
    r1.status = HttpStatusCode.OK.value
    r1.json = MagicMock(return_value=[{"name": "a", "groupId": "1"}])
    r2 = MagicMock()
    r2.status = HttpStatusCode.OK.value
    r2.json = MagicMock(return_value=[{"name": "b", "groupId": "2"}])
    ds = MagicMock()
    ds.bulk_get_groups_v2 = AsyncMock(side_effect=[r1, r2])
    with patch("app.connectors.sources.atlassian.jira_data_center.connector.GROUP_PAGE_SIZE", 1):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            groups = await conn._fetch_groups()
    assert [g["groupId"] for g in groups] == ["1", "2"]


@pytest.mark.asyncio
async def test_fetch_groups_malformed_payload_and_exception():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = HttpStatusCode.OK.value
    bad.json = MagicMock(return_value=12345)
    ds = MagicMock()
    ds.bulk_get_groups_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_groups() == []

    boom = MagicMock()
    boom.status = HttpStatusCode.OK.value
    boom.json = MagicMock(side_effect=OSError("net"))
    ds2 = MagicMock()
    ds2.bulk_get_groups_v2 = AsyncMock(return_value=boom)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
        assert await conn._fetch_groups() == []


@pytest.mark.asyncio
async def test_fetch_group_members_list_payload_pages():
    conn = _make_connector()
    conn.data_source = MagicMock()
    r1 = MagicMock()
    r1.status = HttpStatusCode.OK.value
    r1.json = MagicMock(return_value=[{"key": "k1", "emailAddress": "a@a"}])
    r2 = MagicMock()
    r2.status = HttpStatusCode.OK.value
    r2.json = MagicMock(return_value=[{"key": "k2", "emailAddress": "b@b"}])
    ds = MagicMock()
    ds.get_users_from_group_v2 = AsyncMock(side_effect=[r1, r2])
    with patch("app.connectors.sources.atlassian.jira_data_center.connector.GROUP_MEMBER_PAGE_SIZE", 1):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            keys = await conn._fetch_group_members("g1", "G")
    assert keys == ["k1", "k2"]


@pytest.mark.asyncio
async def test_fetch_group_members_exception_breaks():
    conn = _make_connector()
    conn.data_source = MagicMock()
    boom = MagicMock()
    boom.status = HttpStatusCode.OK.value
    boom.json = MagicMock(side_effect=RuntimeError("x"))
    ds = MagicMock()
    ds.get_users_from_group_v2 = AsyncMock(return_value=boom)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._fetch_group_members("g", "G") == []


@pytest.mark.asyncio
async def test_sync_project_roles_project_list_fails_and_empty_dict():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
        source_user_id="u1",
        org_id="org-dc-cov",
        email="u@e",
        full_name="U",
        is_active=True,
    )

    bad = MagicMock()
    bad.status = 403
    ds = MagicMock()
    ds.get_project_roles_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        await conn._sync_project_roles(["P"], [u], {})

    ok_empty = MagicMock()
    ok_empty.status = HttpStatusCode.OK.value
    ok_empty.json = MagicMock(return_value={})
    ds2 = MagicMock()
    ds2.get_project_roles_v2 = AsyncMock(return_value=ok_empty)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds2):
        await conn._sync_project_roles(["P2"], [u], {})
    conn.data_entities_processor.on_new_app_roles.assert_not_called()


@pytest.mark.asyncio
async def test_sync_project_roles_role_detail_non_ok():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id="conn-dc-cov",
        source_user_id="u1",
        org_id="org-dc-cov",
        email="u@e",
        full_name="U",
        is_active=True,
    )
    list_resp = MagicMock()
    list_resp.status = HttpStatusCode.OK.value
    list_resp.json = MagicMock(return_value={"R": "https://jira/rest/api/2/project/P/role/9"})

    role_bad = MagicMock()
    role_bad.status = 404
    ds = MagicMock()
    ds.get_project_roles_v2 = AsyncMock(return_value=list_resp)
    ds.get_project_role_v2 = AsyncMock(return_value=role_bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        await conn._sync_project_roles(["P"], [u], {})
    conn.data_entities_processor.on_new_app_roles.assert_not_called()


@pytest.mark.asyncio
async def test_sync_project_lead_roles_warns_when_lead_has_no_account_id():
    conn = _make_connector()
    conn.data_source = MagicMock()
    await conn._sync_project_lead_roles([{"key": "K", "lead": {"displayName": "Who"}}], [])
    conn.data_entities_processor.on_new_app_roles.assert_awaited()


@pytest.mark.asyncio
async def test_fetch_projects_adf_description_converted():
    conn = _make_connector()
    conn.data_source = MagicMock()
    row = _sample_project_row(
        key="KF",
        description={"type": "paragraph", "content": [{"type": "text", "text": "adf"}]},
    )
    with patch.object(conn, "_list_all_projects_dc", new_callable=AsyncMock, return_value=[row]):
        with patch.object(conn, "_fetch_application_roles_to_groups_mapping", new_callable=AsyncMock, return_value={}):
            with patch.object(
                conn,
                "_fetch_project_permission_scheme",
                new_callable=AsyncMock,
                return_value=[Permission(entity_type=EntityType.GROUP, external_id="g", type=PermissionType.READ)],
            ) as fp:
                groups, raw = await conn._fetch_projects()
    assert raw[0]["description"] == "adf" or "adf" in str(raw[0]["description"])
    fp.assert_awaited()
    assert groups and groups[0][1]


@pytest.mark.asyncio
async def test_run_sync_with_project_keys_filter_logs(monkeypatch):
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[MagicMock()])

    pfilter = MagicMock()
    pfilter.get_value = MagicMock(return_value=["ABC"])
    pfilter.get_operator = MagicMock(return_value=ListOperator.IN)
    sync_f = MagicMock()
    sync_f.get = MagicMock(side_effect=lambda k: pfilter if k == SyncFilterKey.PROJECT_KEYS else None)

    async def fake_load(*_a, **_kw):
        return sync_f, None

    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="s",
        org_id="org-dc-cov",
        email="e@e",
        full_name="E",
        is_active=True,
    )

    monkeypatch.setattr(
        "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
        fake_load,
    )
    with patch.object(conn, "_fetch_users", new_callable=AsyncMock, return_value=[u]):
        with patch.object(conn, "_sync_user_groups", new_callable=AsyncMock, return_value={}):
            with patch.object(
                conn,
                "_fetch_projects",
                new_callable=AsyncMock,
                return_value=([], []),
            ):
                with patch.object(conn, "_sync_project_roles", new_callable=AsyncMock):
                    with patch.object(conn, "_sync_project_lead_roles", new_callable=AsyncMock):
                        with patch.object(conn.issues_sync_point, "read_sync_point", new_callable=AsyncMock, return_value=None):
                            with patch.object(conn.issues_sync_point, "update_sync_point", new_callable=AsyncMock):
                                with patch.object(
                                    conn,
                                    "_sync_all_project_issues",
                                    new_callable=AsyncMock,
                                    return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
                                ):
                                    await conn.run_sync()


@pytest.mark.asyncio
async def test_fetch_issues_batched_with_modified_sync_filters():
    conn = _make_connector()
    conn.data_source = MagicMock()
    mf = MagicMock()
    mf.get_value = MagicMock(return_value=(1000, 2000))
    cf = MagicMock()
    cf.get_value = MagicMock(return_value=(500, None))
    conn.sync_filters = MagicMock()
    conn.sync_filters.get = MagicMock(
        side_effect=lambda k: mf if k == SyncFilterKey.MODIFIED else (cf if k == SyncFilterKey.CREATED else None)
    )

    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    resp.json = MagicMock(return_value={"issues": [], "total": 0})
    ds = MagicMock()
    ds.search_issues_post_v2 = AsyncMock(return_value=resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        out = []
        async for row in conn._fetch_issues_batched("P", "pid", [], last_sync_time=1500, resume_from_timestamp=None, is_new_project=False):
            out.append(row)
    assert len(out) == 1
    ds.search_issues_post_v2.assert_awaited()
    call_kw = ds.search_issues_post_v2.await_args
    assert "jql" in call_kw.kwargs
    body = str(call_kw.kwargs.get("jql") or "")
    assert "updated" in body and "created" in body


@pytest.mark.asyncio
async def test_fetch_issues_batched_search_error_raises():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 500
    bad.text = MagicMock(return_value="err")
    ds = MagicMock()
    ds.search_issues_post_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with pytest.raises(Exception, match="Failed to fetch issues"):
            async for _ in conn._fetch_issues_batched("P", "pid", [], None, None, False):
                pass


@pytest.mark.asyncio
async def test_fetch_issues_batched_one_page_calls_build_records():
    conn = _make_connector()
    conn.data_source = MagicMock()
    issue = {
        "id": "1",
        "key": "K-1",
        "fields": {
            "summary": "S",
            "updated": "2024-01-01T00:00:00.000+0000",
            "created": "2024-01-01T00:00:00.000+0000",
            "issuetype": {"name": "Task"},
        },
    }
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    resp.json = MagicMock(return_value={"issues": [issue], "total": 1})
    ds = MagicMock()
    ds.search_issues_post_v2 = AsyncMock(return_value=resp)
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    _bind_async_transaction(conn, tx)
    mapper = MagicMock()
    mapper.map_type.return_value = "T"
    mapper.map_status.return_value = "O"
    mapper.map_priority.return_value = "L"
    conn.value_mapper = mapper
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_fetch_issue_attachments", new_callable=AsyncMock, return_value=[]):
            batches = []
            async for batch in conn._fetch_issues_batched("P", "pid", [], None, None, False):
                batches.append(batch)
    assert len(batches) == 1 and batches[0][0]


def test_parse_issue_links_empty_fields_and_bad_issuelinks():
    conn = _make_connector()
    assert conn._parse_issue_links({"fields": {}}) == []
    assert conn._parse_issue_links({"fields": {"issuelinks": {}}}) == []


def test_extract_issue_data_without_status_priority_objects():
    conn = _make_connector()
    mapper = MagicMock()
    mapper.map_type.return_value = "T"
    mapper.map_status.return_value = "S"
    mapper.map_priority.return_value = "P"
    conn.value_mapper = mapper
    row = conn._extract_issue_data(
        {
            "id": "1",
            "key": "K-1",
            "fields": {"summary": "S", "issuetype": {"name": "Bug"}},
        },
        {},
    )
    assert row["status"] == "S" and row["priority"] == "P"


@pytest.mark.asyncio
async def test_build_issue_records_increments_version_when_updated():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tx = MagicMock()
    ex = _ticket_record()
    ex.source_updated_at = 1
    ex.version = 3
    tx.get_record_by_external_id = AsyncMock(return_value=ex)
    _bind_async_transaction(conn, tx)
    issue = {
        "id": ex.external_record_id,
        "key": "K-1",
        "fields": {
            "summary": "S",
            "updated": "2025-01-02T00:00:00.000+0000",
            "created": "2024-01-01T00:00:00.000+0000",
            "issuetype": {"name": "Task"},
        },
    }
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    with patch.object(conn, "_parse_jira_timestamp", side_effect=[999, 999, 999]):
        with patch.object(conn, "_fetch_issue_attachments", new_callable=AsyncMock, return_value=[]):
            rows = await conn._build_issue_records([issue], "pid", [], tx, is_new_project=False)
    assert rows[0][0].version == 4


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_requires_weburl():
    conn = _make_connector()
    with pytest.raises(ValueError, match="weburl is required"):
        await conn._parse_issue_to_blocks({"id": "1", "fields": {}}, issue_key="K-1", weburl=None)


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_plain_string_comment_body():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    issue_data = {
        "id": "10",
        "key": "K-1",
        "fields": {"summary": "T"},
        "comments": {
            "comments": [
                {"id": "c1", "author": {"displayName": "A"}, "body": "Plain text", "created": "2024-01-01T00:00:00.000+0000"},
            ],
        },
    }
    with patch.object(conn, "_create_media_fetcher", return_value=AsyncMock(return_value=None)):
        box = await conn._parse_issue_to_blocks(issue_data, issue_key="K-1", weburl="https://jira.example/browse/K-1")
    assert len(box.block_groups) >= 2


@pytest.mark.asyncio
async def test_process_issue_attachments_for_children_creates_file_and_maps():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    att = [{"id": "55", "filename": "f.bin", "mimeType": "application/octet-stream", "size": 4, "created": "2024-01-01T00:00:00.000+0000"}]
    cmap = await conn._process_issue_attachments_for_children(att, "iss", "node", "prj", "https://w", tx)
    assert "55" in cmap
    conn.data_entities_processor.on_new_records.assert_awaited()


@pytest.mark.asyncio
async def test_process_issue_attachments_for_children_per_file_exception():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(side_effect=[RuntimeError("db"), None])
    att = [{"id": "1", "filename": "a"}, {"id": "2", "filename": "b", "mimeType": "text/plain", "created": "2024-01-01T00:00:00.000+0000"}]
    cmap = await conn._process_issue_attachments_for_children(att, "i", "n", "p", None, tx)
    assert "2" in cmap


@pytest.mark.asyncio
async def test_process_issue_blockgroups_for_streaming_end_to_end():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.site_url = "https://jira.example"
    rec = _ticket_record()
    rec.external_record_group_id = "prj"
    fields = {
        "summary": "Hi",
        "description": {"type": "paragraph", "content": [{"type": "text", "text": "d"}]},
        "attachment": [],
        "comment": {"comments": []},
    }
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    resp.json = MagicMock(return_value={"id": rec.external_record_id, "key": "KF", "fields": fields})
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=resp)
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    _bind_async_transaction(conn, tx)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        raw = await conn._process_issue_blockgroups_for_streaming(rec)
    assert raw.startswith(b"{") and b"block_groups" in raw


@pytest.mark.asyncio
async def test_stream_record_file_yields_bytes():
    conn = _make_connector()
    conn.data_source = MagicMock()
    fr = _file_record()
    r = MagicMock()
    r.status = HttpStatusCode.OK.value
    r.bytes = MagicMock(return_value=b"xyz")
    ds = MagicMock()
    ds.get_attachment_content_v2 = AsyncMock(return_value=r)
    with patch.object(conn, "init", new_callable=AsyncMock):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            out = await conn.stream_record(fr)
    chunks = []
    async for part in out.body_iterator:
        chunks.append(part)
    assert b"".join(chunks) == b"xyz"


@pytest.mark.asyncio
async def test_cleanup_close_client_handles_errors():
    conn = _make_connector()
    inner = MagicMock()
    inner.close = AsyncMock(side_effect=RuntimeError("closed"))
    jc = MagicMock()
    jc.get_client = MagicMock(return_value=inner)
    conn.external_client = jc
    conn.data_source = MagicMock()
    await conn.cleanup()
    assert conn.external_client is None


@pytest.mark.asyncio
async def test_reindex_all_updated_skips_reindex_existing():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tup = (_ticket_record(), [])
    with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=tup):
        await conn.reindex_records([_ticket_record()])
    conn.data_entities_processor.on_new_records.assert_awaited()
    conn.data_entities_processor.reindex_existing_records.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_fetch_updated_issue_loads_indexing_filters():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = None
    rec = _ticket_record()
    rec.source_updated_at = 1
    fresh = MagicMock()
    fresh.status = HttpStatusCode.OK.value
    fresh.json = MagicMock(
        return_value={
            "id": rec.external_record_id,
            "key": "K",
            "fields": {
                "summary": "S",
                "updated": "2025-01-01T00:00:00.000+0000",
                "created": "2025-01-01T00:00:00.000+0000",
                "issuetype": {"name": "Task"},
            },
        }
    )
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=fresh)

    async def fake_load(*_a, **_kw):
        return None, MagicMock()

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            new_callable=AsyncMock,
            side_effect=fake_load,
        ):
            with patch.object(conn, "_parse_jira_timestamp", return_value=2):
                row = await conn._check_and_fetch_updated_issue(rec)
    assert row is not None


@pytest.mark.asyncio
async def test_check_and_fetch_updated_attachment_returns_updated_record():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    conn.site_url = "https://jira.example"
    f = _file_record()
    f.parent_external_record_id = "100"
    f.source_updated_at = 10
    parent = MagicMock()
    parent.id = "pid-internal"
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=parent)
    _bind_async_transaction(conn, tx)
    issue_resp = MagicMock()
    issue_resp.status = HttpStatusCode.OK.value
    issue_resp.json = MagicMock(
        return_value={
            "key": "KF",
            "fields": {
                "attachment": [
                    {"id": "99", "filename": "z.png", "size": 1, "mimeType": "image/png", "created": "2025-06-01T00:00:00.000+0000"},
                ],
            },
        }
    )
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=issue_resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_parse_jira_timestamp", return_value=9999):
            row = await conn._check_and_fetch_updated_attachment(f)
    assert row is not None and row[0].external_record_id == f.external_record_id


@pytest.mark.asyncio
async def test_check_and_fetch_updated_attachment_outer_exception():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    f = _file_record()
    f.parent_external_record_id = "100"
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=OSError("x")):
        assert await conn._check_and_fetch_updated_attachment(f) is None


@pytest.mark.asyncio
async def test_create_connector_classmethod():
    log = _make_logger()
    dsp = MagicMock()
    cs = MagicMock()
    dep_inst = MagicMock()
    dep_inst.initialize = AsyncMock()
    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.DataSourceEntitiesProcessor",
        return_value=dep_inst,
    ):
        c = await JiraDataCenterConnector.create_connector(log, dsp, cs, "cid", "team", "user-1")
    assert isinstance(c, JiraDataCenterConnector)
    dep_inst.initialize.assert_awaited_once()


def test_parse_jira_timestamp_strptime_fallback_and_warn():
    conn = _make_connector()
    weird = "2024-01-15T10:30:45.123+0000"
    ts = conn._parse_jira_timestamp(weird)
    assert ts > 0
    with patch.object(conn.logger, "warning") as w:
        assert conn._parse_jira_timestamp("not-a-date-at-all-xyz") == 0
        w.assert_called()


@pytest.mark.asyncio
async def test_get_filter_options_project_keys_delegates():
    conn = _make_connector()
    expected = MagicMock(success=True)
    with patch.object(conn, "_get_project_options", new_callable=AsyncMock, return_value=expected) as gp:
        out = await conn.get_filter_options("project_keys", page=2, limit=5, search="x")
    gp.assert_awaited_once_with(2, 5, "x")
    assert out is expected


def test_adf_to_text_table_heading_strip_marks_and_list_in_paragraph():
    md = adf_to_text(
        _adf_doc(
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {
                                "type": "tableHeader",
                                "content": [{"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "H"}]}],
                            },
                        ],
                    },
                ],
            },
        )
    )
    assert "| H" in md
    md2 = adf_to_text(
        _adf_doc(
            {
                "type": "paragraph",
                "content": [
                    {"type": "bulletList", "content": [{"type": "listItem", "content": [{"type": "text", "text": "x"}]}]},
                ],
            },
        )
    )
    assert "- x" in md2


def test_adf_to_text_media_in_nested_list_without_cache():
    adf = {
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [{"type": "media", "attrs": {"id": "m", "alt": "pic"}}],
                    },
                ],
            },
        ]
    }
    assert "![pic]" in adf_to_text(adf)


@pytest.mark.asyncio
async def test_fetch_users_paginates_two_pages():
    conn = _make_connector()
    conn.data_source = MagicMock()
    page1 = [{"accountId": f"a{i}", "emailAddress": f"a{i}@a", "active": True} for i in range(50)]
    page2 = [{"accountId": "b", "emailAddress": "b@b", "active": True}]
    r1 = MagicMock()
    r1.status = HttpStatusCode.OK.value
    r1.json = MagicMock(return_value=page1)
    r2 = MagicMock()
    r2.status = HttpStatusCode.OK.value
    r2.json = MagicMock(return_value=page2)
    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(side_effect=[r1, r2])
    with patch("app.connectors.sources.atlassian.jira_data_center.connector.USER_PAGE_SIZE", 50):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            users = await conn._fetch_users()
    assert len(users) == 51


@pytest.mark.asyncio
async def test_fetch_users_db_cache_resolves_hidden_email_user():
    """Phase 3B: cached AppUser from prior sync resolves user without visible email."""
    conn = _make_connector()
    conn.data_source = MagicMock()
    cached_user = MagicMock()
    cached_user.source_user_id = "k1"
    cached_user.email = "cached@example.com"
    conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[cached_user])

    batch = [
        {"key": "k1", "active": True},
        {"key": "k2", "emailAddress": "visible@example.com", "active": True},
    ]
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value=batch)
    empty = MagicMock()
    empty.status = HttpStatusCode.OK.value
    empty.json = MagicMock(return_value=[])
    ds = MagicMock()
    ds.get_user_search_v2 = AsyncMock(side_effect=[ok, empty])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    emails = {u.email for u in users}
    assert "cached@example.com" in emails
    assert "visible@example.com" in emails
    assert len(users) == 2


@pytest.mark.asyncio
async def test_fetch_users_reverse_lookup_resolves_hidden_email():
    """Phase 5: reverse lookup resolves a user with hidden email via PipesHub directory."""
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
    pipeshub_user = MagicMock()
    pipeshub_user.email = "hidden@example.com"
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[pipeshub_user])

    bulk_batch = [
        {"key": "k1", "emailAddress": "visible@example.com", "active": True},
        {"key": "k2", "active": True},
    ]
    ok_bulk = MagicMock()
    ok_bulk.status = HttpStatusCode.OK.value
    ok_bulk.json = MagicMock(return_value=bulk_batch)

    reverse_resp = MagicMock()
    reverse_resp.status = HttpStatusCode.OK.value
    reverse_resp.json = MagicMock(return_value=[{"key": "k2", "displayName": "Hidden User", "active": True}])

    ds = MagicMock()
    # Bulk returns 2 items < USER_PAGE_SIZE so it breaks after 1st call; then reverse lookup
    ds.get_user_search_v2 = AsyncMock(side_effect=[ok_bulk, reverse_resp])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    emails = {u.email for u in users}
    assert "hidden@example.com" in emails
    assert "visible@example.com" in emails
    assert len(users) == 2


@pytest.mark.asyncio
async def test_fetch_users_reverse_lookup_skipped_when_no_gaps():
    """Phase 5 not triggered when all users have visible email."""
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
    pipeshub_user = MagicMock()
    pipeshub_user.email = "visible@example.com"
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[pipeshub_user])

    batch = [{"key": "k1", "emailAddress": "visible@example.com", "active": True}]
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value=batch)
    ds = MagicMock()
    # Bulk returns 1 item < USER_PAGE_SIZE, breaks after 1 call; no reverse lookup needed
    ds.get_user_search_v2 = AsyncMock(side_effect=[ok])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    assert len(users) == 1
    assert ds.get_user_search_v2.await_count == 1


@pytest.mark.asyncio
async def test_resolve_private_email_users_api_failure_graceful():
    """_resolve_private_email_users handles API failure without crashing."""
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
    pipeshub_user = MagicMock()
    pipeshub_user.email = "user@example.com"
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[pipeshub_user])

    batch = [{"key": "k1", "active": True}]
    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value=batch)

    fail_resp = MagicMock()
    fail_resp.status = 500
    fail_resp.text = MagicMock(return_value="error")

    ds = MagicMock()
    # Bulk returns 1 item < USER_PAGE_SIZE, breaks after 1 call; then reverse lookup fails
    ds.get_user_search_v2 = AsyncMock(side_effect=[ok, fail_resp])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        users = await conn._fetch_users()
    assert len(users) == 0


@pytest.mark.asyncio
async def test_sync_user_groups_top_level_exception_returns_empty():
    conn = _make_connector()
    conn.data_source = MagicMock()
    with patch.object(conn, "_fetch_groups", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        assert await conn._sync_user_groups([]) == {}


@pytest.mark.asyncio
async def test_run_sync_raises_on_inner_failure(monkeypatch):
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[MagicMock()])

    async def fake_load(*_a, **_kw):
        return None, None

    monkeypatch.setattr(
        "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
        fake_load,
    )
    with patch.object(conn, "_fetch_users", new_callable=AsyncMock, side_effect=OSError("sync fail")):
        with pytest.raises(OSError, match="sync fail"):
            await conn.run_sync()


@pytest.mark.asyncio
async def test_run_sync_empty_project_keys_filter(monkeypatch):
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[MagicMock()])
    pfilter = MagicMock()
    pfilter.get_value = MagicMock(return_value=[])
    pfilter.get_operator = MagicMock(return_value=ListOperator.IN)
    sync_f = MagicMock()
    sync_f.get = MagicMock(side_effect=lambda k: pfilter if k == SyncFilterKey.PROJECT_KEYS else None)

    async def fake_load(*_a, **_kw):
        return sync_f, None

    monkeypatch.setattr(
        "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
        fake_load,
    )
    with patch.object(conn, "_fetch_users", new_callable=AsyncMock, return_value=[]):
        with patch.object(conn, "_sync_user_groups", new_callable=AsyncMock, return_value={}):
            with patch.object(conn, "_fetch_projects", new_callable=AsyncMock, return_value=([], [])) as fp:
                with patch.object(conn, "_sync_project_roles", new_callable=AsyncMock):
                    with patch.object(conn, "_sync_project_lead_roles", new_callable=AsyncMock):
                        with patch.object(conn.issues_sync_point, "read_sync_point", new_callable=AsyncMock, return_value=None):
                            with patch.object(conn.issues_sync_point, "update_sync_point", new_callable=AsyncMock):
                                with patch.object(
                                    conn,
                                    "_sync_all_project_issues",
                                    new_callable=AsyncMock,
                                    return_value={"total_synced": 0, "new_count": 0, "updated_count": 0},
                                ):
                                    await conn.run_sync()
    fp.assert_awaited_once()
    assert fp.call_args[0][0] is None


@pytest.mark.asyncio
async def test_update_issues_sync_checkpoint_on_project_count_only():
    conn = _make_connector()
    conn.issues_sync_point.update_sync_point = AsyncMock()
    await conn._update_issues_sync_checkpoint(
        {"total_synced": 0, "new_count": 0, "updated_count": 0},
        project_count=3,
    )
    conn.issues_sync_point.update_sync_point.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_project_issues_new_project_processes_batch():
    conn = _make_connector()
    conn.data_source = MagicMock()
    proj = _record_group_proj()
    ticket = _ticket_record()

    async def batched(*_a, **_kw):
        yield [(ticket, [])], False, 12345

    with patch.object(conn, "_get_project_sync_checkpoint", new_callable=AsyncMock, return_value=None):
        with patch.object(conn, "_fetch_issues_batched", side_effect=batched):
            with patch.object(conn, "_process_new_records", new_callable=AsyncMock) as proc:
                with patch.object(conn, "_update_project_sync_checkpoint", new_callable=AsyncMock) as up:
                    stats = await conn._sync_project_issues(proj, [], None)
    proc.assert_awaited_once()
    up.assert_awaited()
    assert stats["total_synced"] == 1


@pytest.mark.asyncio
async def test_sync_project_issues_resume_existing_project():
    conn = _make_connector()
    conn.data_source = MagicMock()
    proj = _record_group_proj()

    async def empty_gen(*_a, **_kw):
        return
        yield  # pragma: no cover - makes async generator

    with patch.object(
        conn,
        "_get_project_sync_checkpoint",
        new_callable=AsyncMock,
        return_value={"last_sync_time": 100, "last_issue_updated": 200},
    ):
        with patch.object(conn, "_fetch_issues_batched", side_effect=empty_gen):
            stats = await conn._sync_project_issues(proj, [], 50)
    assert stats["total_synced"] == 0


@pytest.mark.asyncio
async def test_fetch_issues_batched_two_pages():
    conn = _make_connector()
    conn.data_source = MagicMock()
    issue = {
        "id": "1",
        "key": "K-1",
        "fields": {"summary": "S", "updated": "2024-06-01T00:00:00.000+0000", "created": "2024-06-01T00:00:00.000+0000", "issuetype": {"name": "Task"}},
    }
    r1 = MagicMock()
    r1.status = HttpStatusCode.OK.value
    r1.json = MagicMock(return_value={"issues": [issue], "total": 2})
    issue2 = dict(issue)
    issue2["id"] = "2"
    issue2["key"] = "K-2"
    r2 = MagicMock()
    r2.status = HttpStatusCode.OK.value
    r2.json = MagicMock(return_value={"issues": [issue2], "total": 2})
    ds = MagicMock()
    ds.search_issues_post_v2 = AsyncMock(side_effect=[r1, r2])
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    _bind_async_transaction(conn, tx)
    mapper = MagicMock()
    mapper.map_type.return_value = "T"
    mapper.map_status.return_value = "O"
    mapper.map_priority.return_value = "L"
    conn.value_mapper = mapper
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_fetch_issue_attachments", new_callable=AsyncMock, return_value=[]):
            pages = []
            async for batch in conn._fetch_issues_batched("P", "pid", [], None, 1000, False):
                pages.append(batch)
    assert len(pages) >= 1
    assert ds.search_issues_post_v2.await_count >= 2


@pytest.mark.asyncio
async def test_fetch_issues_batched_parse_failure_raises():
    conn = _make_connector()
    conn.data_source = MagicMock()
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    ds = MagicMock()
    ds.search_issues_post_v2 = AsyncMock(return_value=resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch.object(conn, "_safe_json_parse", return_value=None):
            with pytest.raises(Exception, match="Failed to parse issues"):
                async for _ in conn._fetch_issues_batched("P", "pid", [], None, None, False):
                    pass


@pytest.mark.asyncio
async def test_build_issue_records_epic_subtask_links_and_indexing_off():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.site_url = "https://jira.example"
    idx = MagicMock()
    idx.is_enabled = MagicMock(return_value=False)
    conn.indexing_filters = idx
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    epic = {
        "id": "e1",
        "key": "E-1",
        "fields": {
            "summary": "Epic",
            "issuetype": {"name": "Epic", "hierarchyLevel": 1},
            "updated": "2025-01-01T00:00:00.000+0000",
            "created": "2025-01-01T00:00:00.000+0000",
        },
    }
    sub = {
        "id": "s1",
        "key": "S-1",
        "fields": {
            "summary": "Sub",
            "issuetype": {"name": "Sub-task", "hierarchyLevel": -1},
            "parent": {"id": "e1", "key": "E-1"},
            "updated": "2025-01-02T00:00:00.000+0000",
            "created": "2025-01-01T00:00:00.000+0000",
            "issuelinks": [
                {"type": {"outward": "blocks", "name": "Blocks"}, "outwardIssue": {"id": "x9"}},
            ],
        },
    }
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    with patch.object(conn, "_fetch_issue_attachments", new_callable=AsyncMock, return_value=[]):
        rows = await conn._build_issue_records([epic, sub], "pid", [], tx, is_new_project=False)
    by_id = {r[0].external_record_id: r[0] for r in rows}
    assert by_id["e1"].parent_external_record_id is None
    assert by_id["s1"].parent_external_record_id == "e1"
    assert by_id["s1"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
    assert by_id["s1"].related_external_records


@pytest.mark.asyncio
async def test_build_issue_records_attachment_fetch_error_still_returns_issue():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    issue = {
        "id": "10",
        "key": "K-10",
        "fields": {
            "summary": "S",
            "updated": "2025-01-01T00:00:00.000+0000",
            "created": "2025-01-01T00:00:00.000+0000",
            "issuetype": {"name": "Task"},
        },
    }
    mapper = MagicMock()
    mapper.map_type.return_value = "Task"
    mapper.map_status.return_value = "Open"
    mapper.map_priority.return_value = "Low"
    conn.value_mapper = mapper
    with patch.object(conn, "_fetch_issue_attachments", new_callable=AsyncMock, side_effect=RuntimeError("att")):
        rows = await conn._build_issue_records([issue], "pid", [], tx, is_new_project=False)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_fetch_issue_attachments_no_attachments_and_version_bump():
    conn = _make_connector()
    tx = MagicMock()
    ex = _file_record()
    ex.source_updated_at = 1
    ex.version = 2
    tx.get_record_by_external_id = AsyncMock(return_value=ex)
    assert await conn._fetch_issue_attachments("i", "K", {}, [], "p", RecordGroupType.PROJECT, tx) == []
    fields = {
        "attachment": [
            {"id": "5", "filename": "f", "mimeType": "text/plain", "size": 1, "created": "2025-06-01T00:00:00.000+0000"},
        ]
    }
    out = await conn._fetch_issue_attachments("i", "K", fields, [], "p", RecordGroupType.PROJECT, tx)
    assert out[0][0].version == 3


@pytest.mark.asyncio
async def test_sync_project_roles_skips_addon_and_resolves_group_by_name():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="u1",
        org_id="org-dc-cov",
        email="u@e",
        full_name="U",
        is_active=True,
    )
    list_resp = MagicMock()
    list_resp.status = HttpStatusCode.OK.value
    list_resp.json = MagicMock(
        return_value={
            "Dev": "https://jira/rest/api/2/project/P/role/1",
            "atlassian-addons-project-access": "https://jira/rest/api/2/project/P/role/2",
        }
    )
    role_resp = MagicMock()
    role_resp.status = HttpStatusCode.OK.value
    role_resp.json = MagicMock(
        return_value={
            "name": "Dev",
            "actors": [
                {"type": "atlassian-group-role-actor", "name": "GX"},
                {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "missing"}},
            ],
        }
    )
    ds = MagicMock()
    ds.get_project_roles_v2 = AsyncMock(return_value=list_resp)
    ds.get_project_role_v2 = AsyncMock(return_value=role_resp)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        await conn._sync_project_roles(["P"], [u], {"GX": [u]})
    conn.data_entities_processor.on_new_app_roles.assert_awaited()
    assert len(conn.data_entities_processor.on_new_app_roles.call_args[0][0]) == 1


@pytest.mark.asyncio
async def test_sync_project_roles_per_role_exception_continues():
    conn = _make_connector()
    conn.data_source = MagicMock()
    u = AppUser(
        app_name=Connectors.JIRA_DATA_CENTER,
        connector_id=conn.connector_id,
        source_user_id="u1",
        org_id="org-dc-cov",
        email="u@e",
        full_name="U",
        is_active=True,
    )
    list_resp = MagicMock()
    list_resp.status = HttpStatusCode.OK.value
    list_resp.json = MagicMock(
        return_value={"R1": "https://jira/rest/api/2/project/P/role/1", "R2": "https://jira/rest/api/2/project/P/role/2"}
    )
    good = MagicMock()
    good.status = HttpStatusCode.OK.value
    good.json = MagicMock(return_value={"name": "R2", "actors": []})
    ds = MagicMock()
    ds.get_project_roles_v2 = AsyncMock(return_value=list_resp)
    ds.get_project_role_v2 = AsyncMock(side_effect=[RuntimeError("role"), good])
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        await conn._sync_project_roles(["P"], [u], {})
    conn.data_entities_processor.on_new_app_roles.assert_awaited()


@pytest.mark.asyncio
async def test_sync_project_lead_roles_no_roles_to_sync_logs():
    conn = _make_connector()
    conn.data_source = MagicMock()
    await conn._sync_project_lead_roles([], [])
    conn.data_entities_processor.on_new_app_roles.assert_not_called()


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_rich_comments_and_attachments():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    child_pdf = ChildRecord(child_type=ChildType.RECORD, child_id="fid", child_name="doc.pdf")
    child_img = ChildRecord(child_type=ChildType.RECORD, child_id="iid", child_name="shot.png")
    att_map = {"10": child_pdf, "20": child_img}
    att_mimes = {"10": "application/pdf", "20": "image/png"}
    issue_data = {
        "id": "99",
        "key": "KF",
        "fields": {
            "summary": "Title",
            "description": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "media", "attrs": {"id": "tok", "alt": "shot.png", "__fileName": "shot.png"}}],
                    },
                ],
            },
        },
        "comments": {
            "comments": [
                {"id": "c0", "body": None},
                {
                    "id": "c1",
                    "author": {"displayName": "Ann"},
                    "created": "2024-01-01T00:00:00.000+0000",
                    "body": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "media", "attrs": {"id": "tok2", "alt": "doc.pdf", "__fileName": "doc.pdf"}}],
                            },
                        ],
                    },
                },
            ],
        },
    }

    async def fake_adf(adf, fetcher):
        return "comment-md"

    with patch(
        "app.connectors.sources.atlassian.jira_data_center.connector.adf_to_text_with_images",
        new=fake_adf,
    ):
        with patch.object(conn, "_create_media_fetcher", return_value=AsyncMock(return_value=None)):
            box = await conn._parse_issue_to_blocks(
                issue_data,
                issue_key="KF",
                weburl="https://jira.example/browse/KF",
                attachment_children_map=att_map,
                attachment_mime_types=att_mimes,
            )
    subtypes = {bg.sub_type for bg in box.block_groups}
    assert GroupSubType.COMMENT_THREAD in subtypes
    comment_bgs = [bg for bg in box.block_groups if bg.sub_type == GroupSubType.COMMENT]
    assert len(comment_bgs) == 1
    assert comment_bgs[0].children_records
    assert comment_bgs[0].children_records[0].child_name == "doc.pdf"
    assert box.block_groups[0].children is not None


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_description_child_for_standalone_pdf():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    child_pdf = ChildRecord(child_type=ChildType.RECORD, child_id="fid", child_name="readme.pdf")
    att_map = {"30": child_pdf}
    att_mimes = {"30": "application/pdf"}
    issue_data = {
        "id": "1",
        "key": "K",
        "fields": {
            "summary": "T",
            "description": {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "see file"}]}],
            },
        },
        "comments": [],
    }
    with patch.object(conn, "_create_media_fetcher", return_value=AsyncMock(return_value=None)):
        box = await conn._parse_issue_to_blocks(
            issue_data,
            issue_key="K",
            weburl="https://jira.example/browse/K",
            attachment_children_map=att_map,
            attachment_mime_types=att_mimes,
        )
    assert box.block_groups[0].children_records
    assert box.block_groups[0].children_records[0].child_name == "readme.pdf"


@pytest.mark.asyncio
async def test_parse_issue_to_blocks_empty_thread_skipped():
    conn = _make_connector()
    conn.site_url = "https://jira.example"
    with patch.object(conn, "_organize_issue_comments_to_threads", return_value=[[]]):
        box = await conn._parse_issue_to_blocks(
            {"id": "1", "key": "K", "fields": {"summary": "T"}, "comments": [{"id": "c1", "body": {"type": "text"}}]},
            issue_key="K",
            weburl="https://jira.example/browse/K",
        )
    assert len(box.block_groups) == 1


@pytest.mark.asyncio
async def test_process_issue_blockgroups_fetch_issue_fails():
    conn = _make_connector()
    conn.data_source = MagicMock()
    bad = MagicMock()
    bad.status = 500
    bad.text = MagicMock(return_value="err")
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=bad)
    with patch.object(conn, "init", new_callable=AsyncMock):
        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            with pytest.raises(Exception, match="Failed to fetch issue content"):
                await conn._process_issue_blockgroups_for_streaming(_ticket_record())


@pytest.mark.asyncio
async def test_stream_record_ticket_calls_init_when_no_datasource():
    conn = _make_connector()
    conn.data_source = None
    with patch.object(conn, "init", new_callable=AsyncMock) as ini:
        with patch.object(
            conn,
            "_process_issue_blockgroups_for_streaming",
            new_callable=AsyncMock,
            return_value=b"{}",
        ):
            out = await conn.stream_record(_ticket_record())
    ini.assert_awaited_once()
    assert out.media_type == MimeTypes.BLOCKS.value


@pytest.mark.asyncio
async def test_reindex_records_outer_exception_reraises():
    conn = _make_connector()
    conn.data_source = MagicMock()
    tup = (_ticket_record(), [])
    conn.data_entities_processor.on_new_records = AsyncMock(side_effect=RuntimeError("kafka down"))
    with patch.object(conn, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=tup):
        with pytest.raises(RuntimeError, match="kafka down"):
            await conn.reindex_records([_ticket_record()])


@pytest.mark.asyncio
async def test_check_and_fetch_updated_attachment_deleted_and_parent_gone():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = None
    f = _file_record()
    f.parent_external_record_id = "100"
    tx = MagicMock()
    tx.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="p"))
    _bind_async_transaction(conn, tx)
    gone = MagicMock()
    gone.status = HttpStatusCode.GONE.value
    ds = MagicMock()
    ds.get_issue_v2 = AsyncMock(return_value=gone)

    async def fake_load(*_a, **_kw):
        return None, MagicMock()

    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            new_callable=AsyncMock,
            side_effect=fake_load,
        ):
            assert await conn._check_and_fetch_updated_attachment(f) is None

    ok = MagicMock()
    ok.status = HttpStatusCode.OK.value
    ok.json = MagicMock(return_value={"key": "K", "fields": {"attachment": []}})
    ds.get_issue_v2 = AsyncMock(return_value=ok)
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
        assert await conn._check_and_fetch_updated_attachment(f) is None


@pytest.mark.asyncio
async def test_check_and_fetch_updated_issue_inner_exception():
    conn = _make_connector()
    conn.data_source = MagicMock()
    conn.indexing_filters = MagicMock()
    rec = _ticket_record()
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=ValueError("x")):
        assert await conn._check_and_fetch_updated_issue(rec) is None


@pytest.mark.asyncio
async def test_cleanup_outer_exception_logged():
    conn = _make_connector()
    with patch.object(conn.logger, "info", side_effect=[None, RuntimeError("cleanup")]):
        await conn.cleanup()


@pytest.mark.asyncio
async def test_fetch_project_permission_scheme_outer_exception():
    conn = _make_connector()
    conn.data_source = MagicMock()
    with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=OSError("net")):
        assert await conn._fetch_project_permission_scheme("P", {}) == []
