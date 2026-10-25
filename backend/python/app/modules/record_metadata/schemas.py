"""A connector instance's metadata schemas, versioned, in the config store.

Version ``n`` of a connector's schema is its own key,
``/services/connectors/{connectorId}/metadataSchemas/{n}``, created once with
``create_config_if_absent`` and never rewritten: two registrations racing for
the same version cannot overwrite each other, and the loser re-reads and
compares against the winner. Versions run 1, 2, ... with no gaps, so the
latest is the last one found counting up.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors
from app.models.record_metadata import MetadataSchema, PropertyDefinition
from app.modules.record_metadata.builtin import builtin_properties

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

#: More versions than any connector should need; a guard against a runaway loop.
MAX_SCHEMA_VERSIONS = 1000


class MetadataSchemaError(Exception):
    """A stored schema is unreadable, another org's, or past ``MAX_SCHEMA_VERSIONS``."""


def schema_key(connector_id: str, version: int) -> str:
    return f"/services/connectors/{connector_id}/metadataSchemas/{version}"


def schema_id(org_id: str, connector_id: str, version: int) -> str:
    return f"{org_id}:{connector_id}:{version}"


def _same(a: Iterable[PropertyDefinition], b: Iterable[PropertyDefinition]) -> bool:
    return sorted(a, key=lambda p: p.name) == sorted(b, key=lambda p: p.name)


class MetadataSchemaRegistry:
    def __init__(self, config_service: "ConfigurationService") -> None:
        self.config_service = config_service

    async def _read(self, org_id: str, connector_id: str, version: int) -> MetadataSchema | None:
        stored = await self.config_service.get_config(
            schema_key(connector_id, version), use_cache=False, raise_on_error=True,
        )
        if stored is None:
            return None
        if not isinstance(stored, dict) or not isinstance(stored.get("properties"), list):
            raise MetadataSchemaError(f"schema version {version} of connector {connector_id} is unreadable")
        if stored.get("orgId") != org_id:
            raise MetadataSchemaError(f"connector {connector_id} has schemas of another org")
        try:
            properties = tuple(PropertyDefinition.model_validate(p) for p in stored["properties"])
            return MetadataSchema(schema_id=schema_id(org_id, connector_id, version), properties=properties)
        except ValueError as exc:
            raise MetadataSchemaError(
                f"schema version {version} of connector {connector_id} is invalid"
            ) from exc

    async def _latest(self, org_id: str, connector_id: str, start: int = 1) -> tuple[int, MetadataSchema | None]:
        """Counting up from ``start``: the last version found and its schema, or ``(start - 1, None)``."""
        version, latest = start - 1, None
        while True:
            if version >= MAX_SCHEMA_VERSIONS:
                raise MetadataSchemaError(f"connector {connector_id} has {MAX_SCHEMA_VERSIONS} schema versions")
            found = await self._read(org_id, connector_id, version + 1)
            if found is None:
                return version, latest
            version, latest = version + 1, found

    async def get(self, org_id: str, connector_id: str, version: int | None = None) -> MetadataSchema | None:
        """Version ``version`` of the connector's schema, or its latest; None when there is none."""
        if version is not None:
            return await self._read(org_id, connector_id, version)
        return (await self._latest(org_id, connector_id))[1]

    async def register(
        self, org_id: str, connector_id: str, properties: Iterable[PropertyDefinition],
    ) -> MetadataSchema:
        """The connector's latest schema when it has these properties, else a new version holding them."""
        proposed = MetadataSchema(schema_id=schema_id(org_id, connector_id, 1), properties=tuple(properties))
        version, latest = await self._latest(org_id, connector_id)
        while True:
            if latest is not None and _same(latest.properties, proposed.properties):
                return latest
            candidate = version + 1
            if candidate > MAX_SCHEMA_VERSIONS:
                raise MetadataSchemaError(f"connector {connector_id} has {MAX_SCHEMA_VERSIONS} schema versions")
            created = await self.config_service.create_config_if_absent(
                schema_key(connector_id, candidate),
                {
                    "orgId": org_id,
                    "connectorId": connector_id,
                    "version": candidate,
                    "properties": [p.model_dump(mode="json") for p in proposed.properties],
                },
            )
            if created:
                return MetadataSchema(
                    schema_id=schema_id(org_id, connector_id, candidate), properties=proposed.properties,
                )
            # Another registration took this version; compare against what it wrote.
            version, latest = await self._latest(org_id, connector_id, start=candidate)

    async def register_builtin(
        self, org_id: str, connector_id: str, connector: Connectors | str,
    ) -> MetadataSchema | None:
        """Register the schema the connector type ships with; None when it ships none."""
        properties = builtin_properties(connector)
        if properties is None:
            return None
        return await self.register(org_id, connector_id, properties)
