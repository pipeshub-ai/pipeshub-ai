import os
import re
import shutil
import logging
from typing import Optional

from app.config.configuration_service import ConfigurationService

logger = logging.getLogger("storage_cleanup")


def _get_mongo_client():
    """Get a pymongo MongoClient instance if available."""
    try:
        from pymongo import MongoClient
        uri = (
            os.getenv("MONGO_URI")
            or os.getenv("TEST_MONGO_URI")
            or "mongodb://admin:password@localhost:27017/?authSource=admin"
        )
        db_name = (
            os.getenv("MONGO_DB_NAME")
            or os.getenv("TEST_MONGO_DB_NAME")
            or "es"
        )
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        return client, db_name
    except Exception as e:
        logger.debug(f"Could not connect to MongoDB for cleanup: {e}")
        return None, None


async def cleanup_storage_and_mongo_for_prefix(
    path_prefix: str,
    org_id: Optional[str] = None,
    config_service: Optional[ConfigurationService] = None,
) -> None:
    """Clean up both Blob Storage files and MongoDB storage documents for a given path prefix.

    Prefix format is typically: {org_id}/PipesHub/records/{virtual_record_id}
    """
    if not path_prefix or not isinstance(path_prefix, str):
        return

    logger.info(f"🗑️ Cleaning up storage files and MongoDB documents for prefix: {path_prefix}")

    # Extract org_id reliably for tenant-scoped JWT token
    extracted_org_id = org_id
    if not extracted_org_id and path_prefix:
        parts = [p for p in path_prefix.strip("/").split("/") if p]
        if parts:
            extracted_org_id = parts[0]

    # 1. HTTP notification to Node.js Storage Service (S3 / Azure / Local adapter + MongoDB document deletion)
    try:
        import aiohttp
        headers = {}
        try:
            from app.config.constants.service import TokenScopes
            from app.utils.jwt import generate_jwt

            if config_service is None:
                raise ValueError("Configuration service is required for storage cleanup")
            scope = getattr(TokenScopes, "STORAGE_TOKEN", None)
            scope_str = scope.value if hasattr(scope, "value") else "storage:token"
            jwt_payload = {"scopes": [scope_str]}
            if extracted_org_id:
                jwt_payload["orgId"] = str(extracted_org_id)
            token = await generate_jwt(config_service, jwt_payload)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        except Exception as jwt_err:
            logger.debug(f"Could not generate JWT token for storage cleanup call: {jwt_err}")

        nodejs_endpoint = os.getenv("NODEJS_ENDPOINT", "http://localhost:3000")
        urls = [
            f"{nodejs_endpoint}/api/v1/document/internal/by-path-prefix",
            f"{nodejs_endpoint}/api/v1/storage/internal/by-path-prefix",
        ]
        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    async with session.delete(url, json={"pathPrefix": path_prefix}, headers=headers, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("success"):
                                logger.info(f"✅ Node.js storage service notified for prefix {path_prefix} via {url}")
                                break
                except Exception as req_err:
                    logger.debug(f"Call to {url} skipped or failed: {req_err}")
    except Exception as e:
        logger.debug(f"Node.js storage endpoint call for {path_prefix} skipped or failed: {e}")

    # 2. Local Blob Storage file deletion (supporting Linux, macOS, and Windows mount paths)
    try:
        mount_root = os.getenv(
            "PIPESHUB_LOCAL_STORAGE_ROOT",
            os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local")),
        )
        mount_name = os.getenv("PIPESHUB_LOCAL_STORAGE_MOUNT", "PipesHub")
        home = os.path.expanduser("~")

        possible_roots = [
            os.path.join(mount_root, mount_name),
            os.path.join("/root/.local", mount_name),
            os.path.join(home, ".local", mount_name),
            os.path.join(home, "AppData", mount_name),
            os.path.join(home, "Library", mount_name),
        ]

        for base_path in set(possible_roots):
            target_path = os.path.join(base_path, path_prefix)
            if os.path.exists(target_path):
                if os.path.isdir(target_path):
                    shutil.rmtree(target_path, ignore_errors=True)
                    logger.info(f"✅ Removed local storage directory: {target_path}")
                else:
                    os.remove(target_path)
                    logger.info(f"✅ Removed local storage file: {target_path}")
    except Exception as e:
        logger.warning(f"⚠️ Local blob storage cleanup warning for {path_prefix}: {e}")

    # 3. Direct MongoDB document cleanup fallback (only if any unhandled records remain)
    try:
        client, db_name = _get_mongo_client()
        if client and db_name:
            db = client[db_name]
            regex = f"^{re.escape(path_prefix)}"
            result = db["documents"].delete_many({"documentPath": {"$regex": regex}})
            if result.deleted_count > 0:
                logger.info(
                    f"✅ Direct cleanup removed {result.deleted_count} remaining MongoDB storage document(s) matching prefix {path_prefix}"
                )
            client.close()
    except Exception as e:
        logger.warning(f"⚠️ Direct MongoDB cleanup warning for {path_prefix}: {e}")
