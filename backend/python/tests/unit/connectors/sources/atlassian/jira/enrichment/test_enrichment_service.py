"""Tests for Jira ticket live enrichment."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.atlassian.jira.enrichment.service import (
    _discover_custom_fields,
    _get_data_source,
    _sync_oauth_token_if_needed,
    enrich_ticket_llm_context,
    is_jira_connector,
)
from app.models.entities import OriginTypes, RecordType, TicketRecord


def _ticket_record(**overrides):
    defaults = {
        "record_name": "PROJ-1",
        "record_type": RecordType.TICKET,
        "external_record_id": "10324",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.JIRA,
        "connector_id": "conn-jira-1",
    }
    defaults.update(overrides)
    return TicketRecord(**defaults)


class TestJiraEnrichmentHelpers:
    def test_is_jira_connector_accepts_enum_and_string(self):
        assert is_jira_connector(Connectors.JIRA) is True
        assert is_jira_connector(Connectors.JIRA.value) is True
        assert is_jira_connector(Connectors.JIRA_DATA_CENTER) is True
        assert is_jira_connector(Connectors.GOOGLE_DRIVE) is False
        assert is_jira_connector("not-a-connector") is False


_BASE = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"
_ISSUE = {
    "id": "10324",
    "fields": {
        "parent": {"id": "1", "key": "SEC-1", "fields": {"summary": "Acquire Foo Corp"}},
        "issuelinks": [
            {"type": {"outward": "blocks"}, "outwardIssue": {"id": "2", "key": "SEC-2"}},
            {"type": {"outward": "relates to"}, "outwardIssue": {"id": "3", "key": "PUB-3"}},
        ],
        "subtasks": [{"id": "4", "key": "SEC-4"}, {"id": "5", "key": "PUB-5"}],
    },
}


async def _enrich(readable, base: str = _BASE) -> str:
    with patch(
        "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
        new=AsyncMock(return_value=MagicMock()),
    ), patch(
        "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
        new=AsyncMock(return_value={"10324": _ISSUE}),
    ):
        return await enrich_ticket_llm_context(base, _ticket_record(), AsyncMock(), readable)


class TestCrossIssueReferences:
    """Another issue is named only if the viewer may read it."""

    @pytest.mark.asyncio
    async def test_unreadable_issues_are_not_named(self) -> None:
        check = AsyncMock(return_value={"3", "5"})
        merged = await _enrich(check)
        check.assert_awaited_once_with("conn-jira-1", {"1", "2", "3", "4", "5"})
        assert "SEC-1" not in merged and "Foo Corp" not in merged  # hidden parent
        assert "SEC-2" not in merged  # hidden linked issue
        assert "SEC-4" not in merged  # hidden subtask
        assert "PUB-3" in merged and "PUB-5" in merged  # readable ones stay

    @pytest.mark.asyncio
    async def test_readable_parent_keeps_its_title(self) -> None:
        merged = await _enrich(AsyncMock(return_value={"1", "2", "3", "4", "5"}))
        assert "SEC-1 — Acquire Foo Corp" in merged
        assert "SEC-2" in merged and "SEC-4" in merged

    @pytest.mark.asyncio
    async def test_hidden_parent_key_sharing_a_prefix_with_own_key_is_omitted(self) -> None:
        # Own key PROJ-12 is in the base context; hidden parent PROJ-1 must not match inside it.
        issue = {"id": "10324", "fields": {"parent": {"id": "1", "key": "PROJ-1", "fields": {"summary": "Secret"}}}}
        base = "Record: PROJ-12\nTicket Information:\n* Status: Open\n"
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"10324": issue}),
        ):
            merged = await enrich_ticket_llm_context(base, _ticket_record(), AsyncMock(), AsyncMock(return_value=set()))
        assert "Parent" not in merged and "Secret" not in merged
        assert merged.count("PROJ-1") == merged.count("PROJ-12")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("readable", ["raises", "absent"])
    async def test_failed_or_missing_check_names_no_other_issue(self, readable: str) -> None:
        check = AsyncMock(side_effect=RuntimeError("down")) if readable == "raises" else None
        merged = await _enrich(check)
        for hidden in ("SEC-1", "Foo Corp", "SEC-2", "PUB-3", "SEC-4", "PUB-5"):
            assert hidden not in merged

    async def _epic(self, lookup, readable, value: object = "PROJ-1") -> tuple[str, AsyncMock]:
        """Enrich a ticket whose epic link is the key PROJ-1 (own key PROJ-12)."""
        issue = {"id": "10324", "fields": {"customfield_9": value}}
        base = "Record: PROJ-12\nTicket Information:\n* Status: Open\n"

        async def fetch(*_a: object, by_key: bool = False, **_k: object) -> dict:
            if by_key:
                if isinstance(lookup, Exception):
                    raise lookup
                return lookup
            return {"10324": issue}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={"epic_link": "customfield_9"}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(side_effect=fetch),
        ) as batch:
            merged = await enrich_ticket_llm_context(base, _ticket_record(), AsyncMock(), readable)
        return merged, batch

    @pytest.mark.asyncio
    async def test_readable_epic_link_is_shown_after_one_key_lookup_and_one_check(self) -> None:
        check = AsyncMock(return_value={"501"})
        merged, batch = await self._epic({"501": {"id": "501", "key": "PROJ-1"}}, check)
        assert "* Epic Link: PROJ-1" in merged
        check.assert_awaited_once_with("conn-jira-1", {"501"})
        key_calls = [c for c in batch.await_args_list if c.kwargs.get("by_key")]
        assert len(key_calls) == 1 and key_calls[0].kwargs["issue_ids"] == ["PROJ-1"]

    @pytest.mark.asyncio
    async def test_unreadable_epic_link_is_dropped(self) -> None:
        merged, _ = await self._epic({"501": {"id": "501", "key": "PROJ-1"}}, AsyncMock(return_value=set()))
        assert "Epic Link" not in merged and "PROJ-1" not in merged.replace("PROJ-12", "")

    @pytest.mark.asyncio
    async def test_unresolved_epic_key_is_dropped(self) -> None:
        check = AsyncMock(return_value={"501"})
        merged, _ = await self._epic({}, check)
        assert "Epic Link" not in merged

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", ["lookup_raises", "check_raises"])
    async def test_failed_lookup_or_check_drops_epic_link(self, failure: str) -> None:
        lookup = RuntimeError("down") if failure == "lookup_raises" else {"501": {"id": "501", "key": "PROJ-1"}}
        check = AsyncMock(side_effect=RuntimeError("down")) if failure == "check_raises" else AsyncMock(return_value={"501"})
        merged, _ = await self._epic(lookup, check)
        assert "Epic Link" not in merged

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [
        {"hasEpicLinkFieldDependency": False, "data": {"id": 7, "key": "PROJ-1", "summary": "Secret plan"}},
        ["PROJ-1"],
        {"value": "PROJ-1"},
        {"name": "PROJ-1 Secret plan"},
        {"key": 1},
    ])
    async def test_epic_link_of_any_other_shape_is_dropped(self, value: object) -> None:
        check = AsyncMock(return_value={"501"})
        merged, _ = await self._epic({"501": {"id": "501", "key": "PROJ-1"}}, check, value)
        assert "Epic Link" not in merged and "Secret" not in merged
        assert "PROJ-1" not in merged.replace("PROJ-12", "")

    @pytest.mark.asyncio
    async def test_no_check_drops_epic_link(self) -> None:
        merged, _ = await self._epic({"501": {"id": "501", "key": "PROJ-1"}}, None)
        assert "Epic Link" not in merged


class TestBaseContextParentId:
    """The base context's ``External Parent ID`` line follows the same access rule."""

    async def _run(self, readable, issue: dict | None = None) -> str:
        ticket = _ticket_record(parent_external_record_id="777")
        base = ticket.to_llm_context()
        assert "External Parent ID: 777" in base
        found = {"10324": {"id": "10324", "fields": {}}} if issue is None else {"10324": issue}
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value=found),
        ):
            return await enrich_ticket_llm_context(base, ticket, AsyncMock(), readable)

    @pytest.mark.asyncio
    async def test_readable_parent_id_stays(self) -> None:
        check = AsyncMock(return_value={"777"})
        assert "External Parent ID: 777" in await self._run(check)
        check.assert_awaited_once_with("conn-jira-1", {"777"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["unreadable", "raises", "absent", "no_issue"])
    async def test_unreadable_or_unchecked_parent_id_is_dropped(self, mode: str) -> None:
        check = {
            "unreadable": AsyncMock(return_value=set()),
            "raises": AsyncMock(side_effect=RuntimeError("down")),
            "absent": None,
            "no_issue": AsyncMock(return_value={"777"}),
        }[mode]
        issue = {} if mode == "no_issue" else None  # empty issue: nothing fetched
        merged = await self._run(check, issue)
        assert "External Parent ID: 777" not in merged
        assert "External Parent ID: N/A" in merged


    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["_get_data_source", "_discover_custom_fields", "batch_fetch_issues"])
    async def test_a_failed_live_fetch_keeps_the_parent_id_hidden(self, failing: str) -> None:
        ticket = _ticket_record(parent_external_record_id="777")
        with patch(
            f"app.connectors.sources.atlassian.jira.enrichment.service.{failing}",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ):
            merged = await ticket.to_llm_context_with_live_fields(
                config_service=AsyncMock(), readable_issue_ids=AsyncMock(return_value={"777"}),
            )
        assert "External Parent ID: 777" not in merged
        assert "External Parent ID: N/A" in merged

class TestEnrichTicketLlmContext:
    @pytest.mark.asyncio
    async def test_fetches_issue_and_merges(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"
        issue = {"id": "10324", "fields": {"status": {"name": "In Progress"}, "labels": ["live"]}}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"10324": issue}),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert "* Status: Open" in merged
        assert "* Status: In Progress" not in merged
        assert "* Labels: live" in merged

    @pytest.mark.asyncio
    async def test_returns_base_when_fetch_fails(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=None),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base

    @pytest.mark.asyncio
    async def test_cloud_search_uses_is_cloud_after_client_build(self):
        """First request must read is_cloud after _get_data_source populates the cache."""
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        config_service = AsyncMock()
        ticket = _ticket_record(
            connector_name=Connectors.JIRA,
            external_record_id="20086",
            record_name="PST-11",
        )
        base = "Record: PST-11\nTicket Information:\n* Status: Open\n"
        issue = {"id": "20086", "fields": {"labels": ["live"]}}
        mock_ds = MagicMock()
        enrichment_service._is_cloud.clear()

        async def _build_ds(_config, connector_id, _connector_name):
            enrichment_service._is_cloud[connector_id] = True
            return mock_ds

        with patch.object(enrichment_service, "_get_data_source", side_effect=_build_ds), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"20086": issue}),
        ) as mock_batch, patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.enrich_from_issue",
            return_value=base + "\n* Labels: live",
        ):
            await enrich_ticket_llm_context(base, ticket, config_service)

        mock_batch.assert_awaited_once()
        assert mock_batch.await_args.kwargs["is_cloud"] is True

    @pytest.mark.asyncio
    async def test_skips_non_jira_connector(self):
        config_service = AsyncMock()
        ticket = _ticket_record(connector_name=Connectors.LINEAR)
        base = "Record: LIN-1\nTicket Information:\n* Status: Open\n"

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(),
        ) as mock_batch:
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base
        mock_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_base_when_missing_ids(self):
        config_service = AsyncMock()
        ticket = _ticket_record(connector_id="", external_record_id="")
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"

        merged = await enrich_ticket_llm_context(base, ticket, config_service)
        assert merged == base

    @pytest.mark.asyncio
    async def test_returns_base_when_issue_not_found(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={}),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base

    @pytest.mark.asyncio
    async def test_returns_base_when_format_raises(self):
        config_service = AsyncMock()
        ticket = _ticket_record()
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"
        issue = {"id": "10324", "fields": {"labels": ["live"]}}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._discover_custom_fields",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.batch_fetch_issues",
            new=AsyncMock(return_value={"10324": issue}),
        ), patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.enrich_from_issue",
            side_effect=RuntimeError("format failed"),
        ):
            merged = await enrich_ticket_llm_context(base, ticket, config_service)

        assert merged == base


@pytest.fixture(autouse=True)
def clear_enrichment_caches():
    from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

    enrichment_service._data_sources.clear()
    enrichment_service._auth_types.clear()
    enrichment_service._is_cloud.clear()
    enrichment_service._unavailable_until.clear()
    yield
    enrichment_service._data_sources.clear()
    enrichment_service._auth_types.clear()
    enrichment_service._is_cloud.clear()
    enrichment_service._unavailable_until.clear()


class TestOAuthTokenSync:
    @pytest.mark.asyncio
    async def test_oauth_cache_hit_syncs_token(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-oauth-1"
        inner_client = MagicMock()
        inner_client.get_token.return_value = "old-token"
        mock_ds = MagicMock()
        mock_ds._client = inner_client

        enrichment_service._data_sources[connector_id] = mock_ds
        enrichment_service._auth_types[connector_id] = "OAUTH"

        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "credentials": {"access_token": "new-token"},
        }

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(),
        ) as mock_build:
            ds = await _get_data_source(config_service, connector_id, Connectors.JIRA)

        assert ds is mock_ds
        mock_build.assert_not_awaited()
        config_service.get_config.assert_awaited_once()
        inner_client.set_token.assert_called_once_with("new-token")

    @pytest.mark.asyncio
    async def test_non_oauth_cache_hit_skips_sync(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-pat-1"
        inner_client = MagicMock()
        mock_ds = MagicMock()
        mock_ds._client = inner_client

        enrichment_service._data_sources[connector_id] = mock_ds
        enrichment_service._auth_types[connector_id] = "API_TOKEN"

        config_service = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(),
        ) as mock_build:
            ds = await _get_data_source(config_service, connector_id, Connectors.JIRA_DATA_CENTER)

        assert ds is mock_ds
        mock_build.assert_not_awaited()
        config_service.get_config.assert_not_awaited()
        inner_client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_same_token_no_set_token(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-oauth-2"
        inner_client = MagicMock()
        inner_client.get_token.return_value = "same-token"
        mock_ds = MagicMock()
        mock_ds._client = inner_client

        enrichment_service._data_sources[connector_id] = mock_ds
        enrichment_service._auth_types[connector_id] = "OAUTH"

        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "credentials": {"access_token": "same-token"},
        }

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(),
        ):
            await _get_data_source(config_service, connector_id, Connectors.JIRA)

        config_service.get_config.assert_awaited_once()
        inner_client.set_token.assert_not_called()


class TestDiscoverCustomFields:
    @pytest.mark.asyncio
    async def test_returns_empty_when_data_source_unavailable(self):
        config_service = AsyncMock()
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(return_value=None),
        ):
            result = await _discover_custom_fields(
                config_service,
                "conn-missing",
                Connectors.JIRA,
            )
        assert result == {}

    @pytest.mark.asyncio
    async def test_uses_provided_data_source(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        config_service = AsyncMock()
        mock_ds = MagicMock()
        enrichment_service._is_cloud["conn-provided"] = True

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.discover_custom_field_ids",
            new=AsyncMock(return_value={"sprint": "customfield_1"}),
        ) as mock_discover, patch(
            "app.connectors.sources.atlassian.jira.enrichment.service._get_data_source",
            new=AsyncMock(),
        ) as mock_get:
            result = await _discover_custom_fields(
                config_service,
                "conn-provided",
                Connectors.JIRA,
                ds=mock_ds,
            )

        assert result == {"sprint": "customfield_1"}
        mock_get.assert_not_awaited()
        mock_discover.assert_awaited_once_with(
            mock_ds,
            is_cloud=True,
            connector_id="conn-provided",
        )


class TestBuildDataSource:
    @pytest.mark.asyncio
    async def test_builds_and_caches_new_data_source(self):
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        connector_id = "conn-new"
        inner_client = MagicMock()
        inner_client.base_url = "https://team.atlassian.net"
        jira_client = MagicMock()
        jira_client.get_client.return_value = inner_client

        config_service = AsyncMock()
        config_service.get_config.return_value = {"auth": {"authType": "API_TOKEN"}}

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(return_value=jira_client),
        ) as mock_build:
            ds = await _get_data_source(config_service, connector_id, Connectors.JIRA)

        assert ds is not None
        mock_build.assert_awaited_once()
        assert enrichment_service._data_sources[connector_id] is ds
        assert enrichment_service._auth_types[connector_id] == "API_TOKEN"
        assert enrichment_service._is_cloud[connector_id] is True

    @pytest.mark.asyncio
    async def test_build_failure_returns_none(self):
        config_service = AsyncMock()
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(side_effect=RuntimeError("auth failed")),
        ):
            ds = await _get_data_source(config_service, "conn-bad", Connectors.JIRA)

        assert ds is None


class TestOAuthTokenSyncEdgeCases:
    @pytest.mark.asyncio
    async def test_no_config_returns_early(self):
        mock_ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.return_value = None

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")
        mock_ds._client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_access_token_returns_early(self):
        mock_ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.return_value = {"credentials": {}}

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")
        mock_ds._client.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_without_token_methods_returns_early(self):
        mock_ds = MagicMock()
        mock_ds._client = object()
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "credentials": {"access_token": "token"},
        }

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")

    @pytest.mark.asyncio
    async def test_config_read_exception_is_swallowed(self):
        mock_ds = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("etcd down")

        await _sync_oauth_token_if_needed(mock_ds, config_service, "conn-1")
        mock_ds._client.set_token.assert_not_called()


def _real_config_service(store: AsyncMock) -> Any:
    """A ConfigurationService over a fake key-value store, with no watcher or encryption."""
    from app.config.configuration_service import ConfigurationService

    with patch("app.config.configuration_service.EncryptionService.get_instance", return_value=MagicMock()), \
            patch.object(ConfigurationService, "_start_watch"), \
            patch.dict("os.environ", {"SECRET_KEY": "test-secret-key"}):
        return ConfigurationService(logger=logging.getLogger("test-jira-enrichment"), key_value_store=store)


class TestUnavailableConnectorIsNotRetriedOnEveryAnswer:
    """A record can say JIRA without coming from a Jira connector.

    The Demo connector's records imitate Jira tickets, and a real Jira
    connector can be misconfigured. Building a client for either fails, and
    before this every answer citing such a ticket tried again and logged it.
    """

    @pytest.mark.asyncio
    async def test_a_failed_build_is_not_repeated_within_the_window(self) -> None:
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(side_effect=ValueError("no Jira credentials")),
        ) as mock_build, patch.object(enrichment_service, "logger") as mock_logger:
            first = await _get_data_source(AsyncMock(), "demo-connector", Connectors.JIRA)
            second = await _get_data_source(AsyncMock(), "demo-connector", Connectors.JIRA)

        assert first is None and second is None
        assert mock_build.await_count == 1
        assert mock_logger.warning.call_count == 1

    @pytest.mark.asyncio
    async def test_it_is_tried_again_once_the_window_has_passed(self) -> None:
        from app.connectors.sources.atlassian.jira.enrichment import service as enrichment_service

        now = [1000.0]
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(side_effect=ValueError("down")),
        ) as mock_build, patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.time.monotonic",
            side_effect=lambda: now[0],
        ):
            await _get_data_source(AsyncMock(), "jira-1", Connectors.JIRA)
            now[0] += enrichment_service._RETRY_UNAVAILABLE_AFTER_S - 1
            await _get_data_source(AsyncMock(), "jira-1", Connectors.JIRA)
            assert mock_build.await_count == 1
            now[0] += 2
            await _get_data_source(AsyncMock(), "jira-1", Connectors.JIRA)

        assert mock_build.await_count == 2

    @pytest.mark.asyncio
    async def test_one_unavailable_connector_does_not_block_another(self) -> None:
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(side_effect=ValueError("down")),
        ) as mock_build:
            await _get_data_source(AsyncMock(), "demo-connector", Connectors.JIRA)
            await _get_data_source(AsyncMock(), "real-jira", Connectors.JIRA)

        assert mock_build.await_count == 2

    @pytest.mark.asyncio
    async def test_a_transient_failure_is_tried_again_on_the_next_answer(self) -> None:
        # Only a configuration that cannot make a client pauses retries; a blip does not.
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(side_effect=ConnectionError("config store unreachable")),
        ) as mock_build:
            await _get_data_source(AsyncMock(), "jira-1", Connectors.JIRA)
            await _get_data_source(AsyncMock(), "jira-1", Connectors.JIRA)

        assert mock_build.await_count == 2

    @pytest.mark.asyncio
    async def test_a_config_store_failure_is_tried_again_on_the_next_answer(self) -> None:
        # Through the real config service and client build, which both used to
        # turn a failed read into "no config".
        store = AsyncMock()
        store.get_key.side_effect = TimeoutError("etcd timed out")
        config_service = _real_config_service(store)

        first = await _get_data_source(config_service, "jira-1", Connectors.JIRA)
        second = await _get_data_source(config_service, "jira-1", Connectors.JIRA)

        assert first is None and second is None
        assert store.get_key.await_count == 2

    @pytest.mark.asyncio
    async def test_a_connector_with_no_jira_config_is_paused(self) -> None:
        store = AsyncMock()
        store.get_key.return_value = None
        config_service = _real_config_service(store)

        await _get_data_source(config_service, "demo-connector", Connectors.JIRA)
        await _get_data_source(config_service, "demo-connector", Connectors.JIRA)

        assert store.get_key.await_count == 1

    @pytest.mark.asyncio
    async def test_a_failure_after_the_client_was_built_is_tried_again(self) -> None:
        config_service = AsyncMock()
        config_service.get_config.side_effect = [TimeoutError("etcd"), {"auth": {"authType": "API_TOKEN"}}]
        client = MagicMock()
        client.get_client.return_value = MagicMock(base_url="https://acme.atlassian.net")
        with patch(
            "app.connectors.sources.atlassian.jira.enrichment.service.JiraClient.build_from_services",
            new=AsyncMock(return_value=client),
        ) as mock_build:
            first = await _get_data_source(config_service, "jira-1", Connectors.JIRA)
            second = await _get_data_source(config_service, "jira-1", Connectors.JIRA)

        assert first is None and second is not None
        assert mock_build.await_count == 2
