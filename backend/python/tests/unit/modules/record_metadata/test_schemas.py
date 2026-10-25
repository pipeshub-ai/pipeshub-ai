"""The schema registry (versions in the config store) and the built-in ticket schema."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import Connectors
from app.models.entities import ItemType, Priority, RecordType, Status, TicketRecord
from app.models.record_metadata import (
    MetadataProvenance,
    PropertyDefinition,
    PropertyType,
    searchable_lines,
)
from app.modules.record_metadata.builtin import (
    TICKET_PROPERTIES,
    TICKET_SCHEMA,
    TICKET_SCHEMA_ID,
    builtin_properties,
    connector_source,
    ticket_metadata,
)
from app.modules.record_metadata.schemas import (
    MAX_SCHEMA_VERSIONS,
    MetadataSchemaError,
    MetadataSchemaRegistry,
    hash_key,
    latest_key,
    schema_key,
)

ORG, CONNECTOR = "org-1", "conn-1"


class _Config:
    """``ConfigurationService``'s get, set and create-if-absent over a dict, as mocks."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.get_config = AsyncMock(side_effect=self._get)
        self.create_config_if_absent = AsyncMock(side_effect=self._create)
        self.set_config = AsyncMock(side_effect=self._set)

    async def _set(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.values[key] = value
        return True

    async def _get(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        return self.values.get(key, default)

    async def _create(self, key: str, value: Any) -> bool:  # noqa: ANN401
        if key in self.values:
            return False
        self.values[key] = value
        return True


def _props(*names: str) -> tuple[PropertyDefinition, ...]:
    return tuple(PropertyDefinition(name=n, type=PropertyType.STRING, display_label=n) for n in names)


@pytest.fixture
def config() -> _Config:
    return _Config()


@pytest.fixture
def registry(config: _Config) -> MetadataSchemaRegistry:
    return MetadataSchemaRegistry(config)  # type: ignore[arg-type]


class TestRegistry:
    async def test_first_registration_is_version_1(self, registry: MetadataSchemaRegistry, config: _Config) -> None:
        schema = await registry.register(ORG, CONNECTOR, _props("a", "b"))

        assert schema.schema_id == f"{ORG}:{CONNECTOR}:1"
        stored = config.values[schema_key(CONNECTOR, 1)]
        assert stored["orgId"] == ORG and stored["connectorId"] == CONNECTOR and stored["version"] == 1
        assert [p["name"] for p in stored["properties"]] == ["a", "b"]
        # Reads skip the cache and fail loudly, so an outage never reads as "no schema yet".
        for call in config.get_config.await_args_list:
            assert call.kwargs == {"use_cache": False, "raise_on_error": True}
        # Only the latest pointer is ever rewritten; a version never is.
        assert [c.args[0] for c in config.set_config.await_args_list] == [latest_key(CONNECTOR)]
        assert config.values[latest_key(CONNECTOR)] == {"orgId": ORG, "version": 1}
        assert config.values[hash_key(CONNECTOR, _props("b", "a"))] == {"orgId": ORG, "version": 1}

    async def test_same_properties_same_version(self, registry: MetadataSchemaRegistry, config: _Config) -> None:
        first = await registry.register(ORG, CONNECTOR, _props("a", "b"))
        again = await registry.register(ORG, CONNECTOR, _props("b", "a"))

        assert again == first
        assert [k for k in config.values if k.rsplit("/", 1)[1].isdigit()] == [schema_key(CONNECTOR, 1)]
        # The second registration is a lookup: no create, no rewrite of the pointer.
        assert config.create_config_if_absent.await_count == 2
        assert config.set_config.await_count == 1

    async def test_changed_properties_new_version(self, registry: MetadataSchemaRegistry) -> None:
        first = await registry.register(ORG, CONNECTOR, _props("a"))
        second = await registry.register(ORG, CONNECTOR, _props("a", "b"))
        assert second.schema_id.endswith(":2")
        assert await registry.get(ORG, CONNECTOR) == second
        # Going back to an earlier set (a rollback) is that set's version again, and the latest.
        back = await registry.register(ORG, CONNECTOR, _props("a"))

        assert back == first
        assert await registry.get(ORG, CONNECTOR) == first
        assert (await registry.get(ORG, CONNECTOR, version=2)) == second
        assert await registry.get(ORG, CONNECTOR, version=3) is None

    async def test_no_schema(self, registry: MetadataSchemaRegistry) -> None:
        assert await registry.get(ORG, CONNECTOR) is None

    async def test_alternating_sets_mint_one_version_each(
        self, registry: MetadataSchemaRegistry, config: _Config,
    ) -> None:
        # Old and new workers registering at startup during a rolling deploy.
        ids = [(await registry.register(ORG, CONNECTOR, _props(*names))).schema_id
               for names in [("a",), ("a", "b"), ("a",), ("a", "b"), ("a",), ("a", "b")]]

        assert ids == [f"{ORG}:{CONNECTOR}:{v}" for v in (1, 2, 1, 2, 1, 2)]
        assert schema_key(CONNECTOR, 3) not in config.values

    async def test_the_latest_is_two_reads(self, registry: MetadataSchemaRegistry, config: _Config) -> None:
        for n in range(1, 51):
            await registry.register(ORG, CONNECTOR, _props(f"v{n}"))
        config.get_config.reset_mock()

        latest = await registry.get(ORG, CONNECTOR)

        assert latest is not None and latest.schema_id.endswith(":50")
        assert [c.args[0] for c in config.get_config.await_args_list] == [
            latest_key(CONNECTOR), schema_key(CONNECTOR, 50),
        ]

    async def test_without_a_pointer_the_latest_is_the_last_version(
        self, registry: MetadataSchemaRegistry, config: _Config,
    ) -> None:
        for n in (1, 2, 3):
            await registry.register(ORG, CONNECTOR, _props(f"v{n}"))
        del config.values[latest_key(CONNECTOR)]

        assert (await registry.get(ORG, CONNECTOR)).schema_id.endswith(":3")

    async def test_a_hash_entry_naming_other_properties_is_not_trusted(
        self, registry: MetadataSchemaRegistry, config: _Config,
    ) -> None:
        await registry.register(ORG, CONNECTOR, _props("a"))
        config.values[hash_key(CONNECTOR, _props("b"))] = {"orgId": ORG, "version": 1}

        schema = await registry.register(ORG, CONNECTOR, _props("b"))

        assert schema.schema_id.endswith(":2") and schema.properties == _props("b")

    async def test_at_the_version_cap(self, registry: MetadataSchemaRegistry, config: _Config) -> None:
        # Versions stored before the pointer and hash keys existed.
        for v in range(1, MAX_SCHEMA_VERSIONS + 1):
            config.values[schema_key(CONNECTOR, v)] = {
                "orgId": ORG, "version": v, "properties": [p.model_dump(mode="json") for p in _props(f"v{v}")],
            }

        assert (await registry.get(ORG, CONNECTOR)).schema_id.endswith(f":{MAX_SCHEMA_VERSIONS}")
        with pytest.raises(MetadataSchemaError, match="schema versions"):
            await registry.register(ORG, CONNECTOR, _props("one_more"))
        same = await registry.register(ORG, CONNECTOR, _props(f"v{MAX_SCHEMA_VERSIONS}"))
        assert same.schema_id.endswith(f":{MAX_SCHEMA_VERSIONS}")
        assert schema_key(CONNECTOR, MAX_SCHEMA_VERSIONS + 1) not in config.values

    async def test_a_lost_race_compares_against_the_winner(
        self, registry: MetadataSchemaRegistry, config: _Config,
    ) -> None:
        winner = {"orgId": ORG, "connectorId": CONNECTOR, "version": 1,
                  "properties": [p.model_dump(mode="json") for p in _props("a")]}

        async def racing_create(key: str, value: Any) -> bool:  # noqa: ANN401
            # Another process wrote version 1 between this one's read and create.
            config.values.setdefault(schema_key(CONNECTOR, 1), winner)
            return await config._create(key, value)

        config.create_config_if_absent.side_effect = racing_create
        same = await registry.register(ORG, CONNECTOR, _props("a"))
        assert same.schema_id == f"{ORG}:{CONNECTOR}:1"

        config.values.clear()
        other = await registry.register(ORG, CONNECTOR, _props("b"))
        assert other.schema_id == f"{ORG}:{CONNECTOR}:2"
        assert config.values[schema_key(CONNECTOR, 1)] == winner

    async def test_another_orgs_schema_is_refused(self, registry: MetadataSchemaRegistry) -> None:
        await registry.register("org-2", CONNECTOR, _props("a"))

        with pytest.raises(MetadataSchemaError, match="another org"):
            await registry.get(ORG, CONNECTOR)
        with pytest.raises(MetadataSchemaError, match="another org"):
            await registry.register(ORG, CONNECTOR, _props("a"))

    async def test_an_unreadable_version_is_an_error(self, registry: MetadataSchemaRegistry, config: _Config) -> None:
        config.values[schema_key(CONNECTOR, 1)] = {"orgId": ORG, "properties": [{"name": "orgId"}]}
        with pytest.raises(MetadataSchemaError, match="invalid"):
            await registry.get(ORG, CONNECTOR)

    async def test_builtin_registration(self, registry: MetadataSchemaRegistry) -> None:
        schema = await registry.register_builtin(ORG, CONNECTOR, Connectors.JIRA)
        assert schema is not None and schema.properties == TICKET_PROPERTIES
        assert await registry.register_builtin(ORG, CONNECTOR, "JIRA") == schema
        assert await registry.register_builtin(ORG, "conn-2", Connectors.KNOWLEDGE_BASE) is None


class TestBuiltinTicketSchema:
    @pytest.mark.parametrize("connector", [
        Connectors.JIRA, Connectors.JIRA_DATA_CENTER, Connectors.LINEAR, Connectors.ZENDESK,
        Connectors.ZAMMAD, Connectors.SALESFORCE, Connectors.GITHUB_TEAMS, Connectors.GITLAB,
    ])
    def test_ticket_connectors_ship_the_ticket_schema(self, connector: Connectors) -> None:
        assert builtin_properties(connector) == TICKET_PROPERTIES
        assert builtin_properties(connector.value) == TICKET_PROPERTIES

    @pytest.mark.parametrize("connector", [Connectors.KNOWLEDGE_BASE, Connectors.GOOGLE_DRIVE, "NOT A CONNECTOR"])
    def test_others_ship_none(self, connector: Connectors | str) -> None:
        assert builtin_properties(connector) is None

    def test_shape(self) -> None:
        by_name = {p.name: p for p in TICKET_SCHEMA.properties}
        assert by_name["status"].semantic_label == "status" and by_name["status"].facetable
        assert by_name["assignee"].semantic_label == "owner"
        assert by_name["reporterName"].semantic_label == "author"
        assert "UNKNOWN" not in by_name["priority"].enum_values
        assert by_name["labels"].type is PropertyType.STRING_LIST and by_name["labels"].facetable
        assert by_name["dueDate"].type is PropertyType.DATETIME


def _ticket(**fields: Any) -> TicketRecord:  # noqa: ANN401
    return TicketRecord(
        id="t-1", record_name="PA-1 Login fails", record_type=RecordType.TICKET, external_record_id="PA-1",
        version=1, origin="CONNECTOR", connector_name=Connectors.JIRA, connector_id=CONNECTOR, org_id=ORG,
        **fields,
    )


class TestTicketMetadata:
    def test_maps_the_ticket_fields(self) -> None:
        ticket = _ticket(
            status=Status.IN_PROGRESS, source_status="In Review", priority=Priority.HIGH, type=ItemType.BUG,
            assignee="Ada", assignee_email="ada@example.com", reporter_name="Grace",
            reporter_email="grace@example.com", labels=["auth", "web"], resolved_at=None,
            due_date=1_767_225_600_000,
        )

        metadata = ticket_metadata(ticket)

        assert metadata is not None and metadata.schema_id == TICKET_SCHEMA_ID
        assert {k: v.value for k, v in metadata.values.items()} == {
            "status": "IN_PROGRESS", "sourceStatus": "In Review", "priority": "HIGH", "issueType": "BUG",
            "assignee": "Ada", "assigneeEmail": "ada@example.com", "reporterName": "Grace",
            "reporterEmail": "grace@example.com", "labels": ["auth", "web"], "dueDate": 1_767_225_600_000,
        }
        assert {v.provenance for v in metadata.values.values()} == {MetadataProvenance.PROVIDED}
        assert {v.source for v in metadata.values.values()} == {"connector:jira"}
        assert searchable_lines(TICKET_SCHEMA, metadata) == [
            "Source status: In Review", "Assignee: Ada", "Reporter: Grace", "Labels: auth, web",
        ]

    def test_values_that_do_not_fit_are_left_out(self) -> None:
        ticket = _ticket(
            status="Triage", priority=Priority.UNKNOWN, assignee="x" * 2000, labels=[f"l{i}" for i in range(70)],
        )

        metadata = ticket_metadata(ticket)

        assert metadata is not None
        assert set(metadata.values) == {"labels"}
        assert metadata.values["labels"].value == [f"l{i}" for i in range(64)]

    def test_hidden_emails_are_source_ids_and_left_out(self) -> None:
        ticket = _ticket(assignee="Ada", assignee_email="5b10ac8d", reporter_email="5b10ac8e", is_email_hidden=True)

        metadata = ticket_metadata(ticket)

        assert metadata is not None and set(metadata.values) == {"assignee"}

    def test_a_ticket_with_nothing_set_has_none(self) -> None:
        assert ticket_metadata(_ticket()) is None

    def test_source_names_the_connector_type(self) -> None:
        assert connector_source(Connectors.JIRA_DATA_CENTER) == "connector:jira_data_center"
