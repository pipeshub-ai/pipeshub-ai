from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger("storage_cleanup")


async def cleanup_storage_and_mongo_for_prefix(
    path_prefix: str,
    org_id: str | None = None,
    config_service: ConfigurationService | None = None,
) -> None:
    """Clean up both Blob Storage files and MongoDB storage documents for a given path prefix.

    Prefix format is typically: {org_id}/PipesHub/records/{virtual_record_id}
    """
    await cleanup_storage_and_mongo_for_prefixes(
        [path_prefix], org_id=org_id, config_service=config_service
    )


async def get_unreferenced_virtual_record_ids(
    virtual_record_ids: list[str], graph_provider: IGraphDBProvider
) -> list[str]:
    """Return IDs with no live graph records, failing closed on lookup errors."""
    unreferenced_ids = []
    for virtual_record_id in dict.fromkeys(
        record_id for record_id in virtual_record_ids if record_id
    ):
        records = await graph_provider.get_records_by_virtual_record_id(
            virtual_record_id, raise_on_error=True
        )
        if not records:
            unreferenced_ids.append(virtual_record_id)
    return unreferenced_ids


async def cleanup_storage_and_mongo_for_prefixes(
    path_prefixes: list[str],
    org_id: str | None = None,
    config_service: ConfigurationService | None = None,
) -> None:
    """Clean up blob objects and MongoDB documents for record path prefixes."""
    path_prefixes = list(
        dict.fromkeys(
            prefix for prefix in path_prefixes if isinstance(prefix, str) and prefix
        )
    )
    if not path_prefixes:
        return

    logger.info("Cleaning up storage for %d path prefix(es)", len(path_prefixes))

    # Extract org_id reliably for tenant-scoped JWT token
    extracted_org_id = org_id
    if not extracted_org_id:
        parts = [p for p in path_prefixes[0].strip("/").split("/") if p]
        if parts:
            extracted_org_id = parts[0]

    if config_service is None:
        raise ValueError("Configuration service is required for storage cleanup")

    import aiohttp

    from app.config.constants import config_node_constants
    from app.config.constants.service import DefaultEndpoints, TokenScopes
    from app.utils.jwt import generate_jwt

    token = await generate_jwt(
        config_service,
        {
            "scopes": [TokenScopes.STORAGE_TOKEN.value],
            **({"orgId": str(extracted_org_id)} if extracted_org_id else {}),
        },
    )
    endpoints = await config_service.get_config(
        config_node_constants.ENDPOINTS.value
    )
    nodejs_endpoint = (endpoints or {}).get("cm", {}).get("endpoint") or os.getenv(
        "NODEJS_ENDPOINT", DefaultEndpoints.NODEJS_ENDPOINT.value
    )
    url = f"{nodejs_endpoint.rstrip('/')}/api/v1/storage/internal/by-path-prefix"

    async with aiohttp.ClientSession() as session:
        for offset in range(0, len(path_prefixes), 500):
            batch = path_prefixes[offset : offset + 500]
            async with session.delete(
                url,
                json={"pathPrefixes": batch},
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                try:
                    result = await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    result = {}

                if (
                    response.status != 200
                    or not isinstance(result, dict)
                    or not result.get("success")
                ):
                    detail = (
                        result.get("error", "unsuccessful response")
                        if isinstance(result, dict)
                        else "invalid response"
                    )
                    raise RuntimeError(
                        f"Storage service cleanup failed for {len(batch)} path prefix(es): "
                        f"HTTP {response.status}, {detail}"
                    )

    logger.info(
        "Storage service cleaned blobs and MongoDB documents for %d prefix(es)",
        len(path_prefixes),
    )

