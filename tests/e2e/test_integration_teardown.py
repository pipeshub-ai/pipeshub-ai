"""
Integration negative scenario: tear down a supporting container (Redis) and verify a dependent
service reports unhealthy status via the health checker. The test restores the container afterwards.

Requirements:
- Tests must be run with TEST_USE_DOCKER=1 so the `docker_manager` fixture is available.
"""
import asyncio
import logging
import time
from typing import Any, Dict

import pytest

logger = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tear_down_redis_and_verify_service_unhealthy(docker_manager, http_client, test_config, health_checker):
    """Stop the Redis test container and verify a dependent Python service (query) becomes unhealthy.

    Flow:
    - Require docker_manager fixture (which will skip if TEST_USE_DOCKER is not enabled)
    - Verify the target service is healthy before teardown; skip test if it's not.
    - Stop the 'redis' container.
    - Poll the service health until it becomes unhealthy or timeout.
    - Recreate the redis container to restore environment.
    """
    service_key = "query_service_url"
    service_name = "query"
    url = test_config[service_key]

    # Ensure service is healthy before we perform teardown
    before = await health_checker.check_python_service(url, service_name)
    if before.get("status") != "healthy":
        pytest.skip("Dependent service is not healthy before teardown; cannot run negative teardown test")

    # Ensure redis container exists in docker_manager
    if "redis" not in docker_manager.containers:
        pytest.skip("Redis test container not available in docker_manager; skipping teardown test")

    redis_container = docker_manager.containers["redis"]

    try:
        # Stop the redis container to simulate teardown
        logger.info("Stopping redis container to simulate teardown")
        try:
            redis_container.stop(timeout=5)
        except Exception as e:
            # If stop fails, attempt to remove if already dead
            logger.warning(f"Failed stopping redis container: {e}")

        # Wait briefly for services to notice
        await asyncio.sleep(3)

        # Poll health until it becomes unhealthy or timeout
        timeout = 30
        start = time.time()
        became_unhealthy = False
        while time.time() - start < timeout:
            res = await health_checker.check_python_service(url, service_name)
            if res.get("status") == "unhealthy":
                became_unhealthy = True
                break
            await asyncio.sleep(1)

        assert became_unhealthy, "Service did not become unhealthy after redis teardown"

    finally:
        # Attempt to recreate redis using the docker_manager's client and known configuration
        try:
            cfg = None
            try:
                # try to import DOCKER_CONTAINERS from tests.conftest
                from tests.conftest import DOCKER_CONTAINERS as _DC  # type: ignore
                cfg = _DC.get("redis")
            except Exception:
                cfg = None

            if cfg is None:
                logger.warning("Redis container configuration not available; cannot recreate automatically")
            else:
                logger.info("Recreating redis container to restore environment")
                # Remove any lingering container object
                try:
                    # If the container still exists, remove it
                    container = docker_manager.client.containers.get(cfg["name"])  # type: ignore
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass
                except Exception:
                    pass

                newc = docker_manager.client.containers.run(
                    cfg["image"],
                    name=cfg["name"],
                    ports=cfg.get("ports", {}),
                    environment=cfg.get("environment", {}),
                    detach=True,
                    remove=True,
                )
                docker_manager.containers["redis"] = newc

                # Wait for redis to be ready (quick ping loop)
                start = time.time()
                ready = False
                while time.time() - start < 30:
                    try:
                        import redis as _redis  # type: ignore
                        r = _redis.Redis(host='localhost', port=6379, db=0, socket_connect_timeout=1)
                        r.ping()
                        ready = True
                        break
                    except Exception:
                        await asyncio.sleep(1)

                if not ready:
                    logger.warning("Redis did not become ready after recreation; you may need to restart manually")

        except Exception as e:
            logger.exception(f"Error while attempting to restore redis container: {e}")