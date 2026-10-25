"""A connector instance's metadata schemas, versioned, in the config store.

Keys, under ``/services/connectors/{connectorId}/metadataSchemas/``:

- ``{n}``: version ``n``, created once with ``create_config_if_absent`` and never
  rewritten. Versions run 1, 2, ... with no gaps, and no two hold the same
  properties: a registration compares against every version before it takes
  the next number, and one that loses the race for a number compares against
  the winner. An id therefore always names the same properties.
- ``byHash/{sha256}``: the version holding a property set, so registering a
  known set again (another worker starting, a rollback) is a lookup.
- ``latest``: the version registered last, so ``get`` is two reads. Rewritten
  on every registration and never ahead of a stored version; without one (a
  connector registered before it existed) ``get`` counts up to the last version.
  "Latest" is the most recent registration, not the highest number (A, B, A
  leaves it on version 1), and racing registrations leave it on either of
  theirs. A record keeps the id it was written with, so nothing already
  written changes meaning; a caller that wants "the current schema" must not
  assume the highest version.
"""

import hashlib
import json
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


def _schemas_key(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/metadataSchemas"


def schema_key(connector_id: str, version: int) -> str:
    return f"{_schemas_key(connector_id)}/{version}"


def latest_key(connector_id: str) -> str:
    return f"{_schemas_key(connector_id)}/latest"


def hash_key(connector_id: str, properties: Iterable[PropertyDefinition]) -> str:
    return f"{_schemas_key(connector_id)}/byHash/{properties_hash(properties)}"


def properties_hash(properties: Iterable[PropertyDefinition]) -> str:
    """The same for the same properties in any order."""
    dumped = sorted((p.model_dump(mode="json") for p in properties), key=lambda p: p["name"])
    return hashlib.sha256(json.dumps(dumped, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def schema_id(org_id: str, connector_id: str, version: int) -> str:
    return f"{org_id}:{connector_id}:{version}"


def _same(a: Iterable[PropertyDefinition], b: Iterable[PropertyDefinition]) -> bool:
    return sorted(a, key=lambda p: p.name) == sorted(b, key=lambda p: p.name)


class MetadataSchemaRegistry:
    def __init__(self, config_service: "ConfigurationService") -> None:
        self.config_service = config_service

    async def _get(self, key: str) -> object:
        return await self.config_service.get_config(key, use_cache=False, raise_on_error=True)

    async def _read(self, org_id: str, connector_id: str, version: int) -> MetadataSchema | None:
        stored = await self._get(schema_key(connector_id, version))
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

    async def _pointed(self, org_id: str, connector_id: str, key: str) -> MetadataSchema | None:
        """The version a ``latest`` or ``byHash`` entry names, or None when it names none."""
        stored = await self._get(key)
        if stored is None:
            return None
        if not isinstance(stored, dict):
            return None
        if stored.get("orgId") != org_id:
            raise MetadataSchemaError(f"connector {connector_id} has schemas of another org")
        version = stored.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= MAX_SCHEMA_VERSIONS:
            return None
        return await self._read(org_id, connector_id, version)

    async def _scan(
        self, org_id: str, connector_id: str, start: int = 1, properties: tuple[PropertyDefinition, ...] | None = None,
    ) -> tuple[int, MetadataSchema | None]:
        """Counting up from ``start`` to the last version: the first that holds
        ``properties``, or (``properties`` None) the last; ``(last, None)`` when
        none holds them."""
        version, latest = start - 1, None
        while version < MAX_SCHEMA_VERSIONS:
            found = await self._read(org_id, connector_id, version + 1)
            if found is None:
                break
            version, latest = version + 1, found
            if properties is not None and _same(found.properties, properties):
                return version, found
        return version, latest if properties is None else None

    async def get(self, org_id: str, connector_id: str, version: int | None = None) -> MetadataSchema | None:
        """Version ``version`` of the connector's schema, or the one registered last; None when there is none."""
        if version is not None:
            return await self._read(org_id, connector_id, version)
        pointed = await self._pointed(org_id, connector_id, latest_key(connector_id))
        if pointed is not None:
            return pointed
        return (await self._scan(org_id, connector_id))[1]

    async def register(
        self, org_id: str, connector_id: str, properties: Iterable[PropertyDefinition],
    ) -> MetadataSchema:
        """The version holding these properties, created when no version holds them."""
        proposed = MetadataSchema(schema_id=schema_id(org_id, connector_id, 1), properties=tuple(properties))
        by_hash = hash_key(connector_id, proposed.properties)
        known = await self._pointed(org_id, connector_id, by_hash)
        if known is None or not _same(known.properties, proposed.properties):
            known = await self._find_or_create(org_id, connector_id, proposed.properties)
            await self.config_service.create_config_if_absent(
                by_hash, {"orgId": org_id, "version": _version_of(known)},
            )
        await self._point_latest(org_id, connector_id, known)
        return known

    async def _find_or_create(
        self, org_id: str, connector_id: str, properties: tuple[PropertyDefinition, ...],
    ) -> MetadataSchema:
        version, found = await self._scan(org_id, connector_id, properties=properties)
        while found is None:
            candidate = version + 1
            if candidate > MAX_SCHEMA_VERSIONS:
                raise MetadataSchemaError(f"connector {connector_id} has {MAX_SCHEMA_VERSIONS} schema versions")
            created = await self.config_service.create_config_if_absent(
                schema_key(connector_id, candidate),
                {
                    "orgId": org_id,
                    "connectorId": connector_id,
                    "version": candidate,
                    "properties": [p.model_dump(mode="json") for p in properties],
                },
            )
            if created:
                return MetadataSchema(schema_id=schema_id(org_id, connector_id, candidate), properties=properties)
            # Another registration took this version; compare against what it wrote, and any after it.
            version, found = await self._scan(org_id, connector_id, start=candidate, properties=properties)
        return found

    async def _point_latest(self, org_id: str, connector_id: str, schema: MetadataSchema) -> None:
        version = _version_of(schema)
        key = latest_key(connector_id)
        current = await self._get(key)
        if isinstance(current, dict) and current.get("version") == version and current.get("orgId") == org_id:
            return
        # Last writer wins between racing registrations; either names a stored version.
        await self.config_service.set_config(key, {"orgId": org_id, "version": version})

    async def register_builtin(
        self, org_id: str, connector_id: str, connector: Connectors | str,
    ) -> MetadataSchema | None:
        """Register the schema the connector type ships with; None when it ships none."""
        properties = builtin_properties(connector)
        if properties is None:
            return None
        return await self.register(org_id, connector_id, properties)


def _version_of(schema: MetadataSchema) -> int:
    return int(schema.schema_id.rsplit(":", 1)[1])
