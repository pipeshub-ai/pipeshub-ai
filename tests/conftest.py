"""
Test configuration and fixtures for PipesHub AI integration tests.
"""
import asyncio
import logging
import os
import subprocess
import time
from typing import AsyncGenerator, Dict, List, Optional, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

# Configure logging
logger = logging.getLogger(__name__)

# Docker is optional for tests; enable with TEST_USE_DOCKER=1
USE_DOCKER = os.getenv("TEST_USE_DOCKER", "0") == "1"

# Strict mode: when enabled, tests will FAIL if dependent services are unavailable.
# Enabled by default; set TEST_STRICT_SERVICES=0 (or false) to disable strict mode.
def _env_flag(name: str, default: str = "1") -> bool:
    """Parse common truthy env var values.

    Returns True for values like: 1, true, yes, y, on (case-insensitive).
    """
    return os.getenv(name, default).lower() in ("1", "true", "yes", "y", "on")

STRICT_MODE = _env_flag("TEST_STRICT_SERVICES", "1")

# Test configuration
TEST_CONFIG = {
    "nodejs_backend_url": "http://localhost:3000",
    "query_service_url": "http://localhost:8000",
    "indexing_service_url": "http://localhost:8091",
    "connector_service_url": "http://localhost:8088",
    "docling_service_url": "http://localhost:8092",
    "timeout": 30,
    "retry_attempts": 3,
    "retry_delay": 2,
}

# Docker container configurations
DOCKER_CONTAINERS = {
    "redis": {
        "image": "redis:bookworm",
        "ports": {"6379/tcp": 6379},
        "name": "pipeshub-test-redis",
        "environment": {},
    },
    "mongodb": {
        "image": "mongo:8.0.6",
        "ports": {"27017/tcp": 27017},
        "name": "pipeshub-test-mongodb",
        "environment": {
            "MONGO_INITDB_ROOT_USERNAME": "admin",
            "MONGO_INITDB_ROOT_PASSWORD": "password",
        },
    },
    "arangodb": {
        "image": "arangodb:3.12.4",
        "ports": {"8529/tcp": 8529},
        "name": "pipeshub-test-arangodb",
        "environment": {"ARANGO_ROOT_PASSWORD": "test_password"},
    },
    "qdrant": {
        "image": "qdrant/qdrant:v1.13.6",
        "ports": {"6333/tcp": 6333, "6334/tcp": 6334},
        "name": "pipeshub-test-qdrant",
        "environment": {"QDRANT__SERVICE__API_KEY": "test_qdrant_key"},
    },
}


class DockerManager:
    """Manages Docker containers for testing."""
    
    def __init__(self):
        if not USE_DOCKER:
            raise RuntimeError("Docker manager initialized without TEST_USE_DOCKER=1")
        # Lazy import to avoid requiring docker when not used
        from docker import DockerClient  # type: ignore
        self.client = DockerClient()
        self.containers = {}
    
    def start_containers(self) -> Dict[str, bool]:
        """Start required Docker containers for testing."""
        results = {}
        
        for name, config in DOCKER_CONTAINERS.items():
            try:
                # Remove existing container if it exists
                try:
                    from docker.errors import NotFound  # type: ignore
                    existing = self.client.containers.get(config["name"])
                    existing.remove(force=True)
                except NotFound:
                    pass
                
                # Start new container
                container = self.client.containers.run(
                    config["image"],
                    name=config["name"],
                    ports=config["ports"],
                    environment=config["environment"],
                    detach=True,
                    remove=True,
                )
                
                self.containers[name] = container
                results[name] = True
                logger.info(f"Started {name} container")
                
            except Exception as e:
                logger.error(f"Failed to start {name} container: {e}")
                results[name] = False
        
        return results
    
    def stop_containers(self):
        """Stop all test containers."""
        for container in self.containers.values():
            try:
                container.stop(timeout=5)
            except Exception as e:
                logger.warning(f"Failed to stop container {container.name}: {e}")
    
    def wait_for_containers(self, timeout: int = 30) -> bool:
        """Wait for containers to be ready."""
        for name, container in self.containers.items():
            if not self._wait_for_container_health(container, timeout):
                return False
        return True
    
    def _wait_for_container_health(self, container, timeout: int) -> bool:
        """Wait for a specific container to be healthy."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                container.reload()
                if container.status == "running":
                    # Additional health checks for specific services
                    if container.name == "pipeshub-test-mongodb":
                        if self._check_mongodb_health():
                            return True
                    elif container.name == "pipeshub-test-arangodb":
                        if self._check_arangodb_health():
                            return True
                    elif container.name == "pipeshub-test-redis":
                        if self._check_redis_health():
                            return True
                    elif container.name == "pipeshub-test-qdrant":
                        if self._check_qdrant_health():
                            return True
                    else:
                        return True
                
                time.sleep(2)
            except Exception:
                time.sleep(2)
        
        return False
    
    def _check_mongodb_health(self) -> bool:
        """Check if MongoDB is ready."""
        try:
            import pymongo
            client = pymongo.MongoClient("mongodb://admin:password@localhost:27017/", serverSelectionTimeoutMS=1000)
            client.server_info()
            return True
        except Exception:
            return False
    
    def _check_arangodb_health(self) -> bool:
        """Check if ArangoDB is ready."""
        try:
            response = httpx.get("http://localhost:8529/_api/version", timeout=5)
            return response.status_code == 200
        except Exception:
            return False
    
    def _check_redis_health(self) -> bool:
        """Check if Redis is ready."""
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, db=0, socket_connect_timeout=1)
            r.ping()
            return True
        except Exception:
            return False
    
    def _check_qdrant_health(self) -> bool:
        """Check if Qdrant is ready."""
        try:
            response = httpx.get("http://localhost:6333/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False


@pytest.fixture(scope="session")
def docker_manager():
    """Docker manager fixture for the entire test session."""
    if not USE_DOCKER:
        pytest.skip("Docker-based tests disabled. Set TEST_USE_DOCKER=1 to enable.")
    manager = DockerManager()
    yield manager
    manager.stop_containers()


@pytest.fixture(scope="session")
def test_containers(docker_manager):
    """Start and manage test containers."""
    logger.info("Starting test containers...")
    results = docker_manager.start_containers()
    
    # Check if all required containers started
    required_containers = ["redis", "mongodb", "arangodb", "qdrant"]
    failed_containers = [name for name, success in results.items() if not success and name in required_containers]
    
    if failed_containers:
        pytest.skip(f"Failed to start required containers: {failed_containers}")
    
    # Wait for containers to be ready
    if not docker_manager.wait_for_containers():
        pytest.skip("Containers failed to become ready within timeout")
    
    logger.info("All test containers are ready")
    yield docker_manager.containers
    
    logger.info("Stopping test containers...")
    docker_manager.stop_containers()


@pytest_asyncio.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP client for making API requests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


@pytest.fixture
def test_config():
    """Test configuration."""
    return TEST_CONFIG


@pytest.fixture
def sample_document():
    """Sample document for testing."""
    return {
        "content": "This is a test document for PipesHub AI integration testing. It contains sample text to verify search functionality.",
        "title": "Test Document",
        "type": "text",
        "metadata": {
            "author": "Test User",
            "created_at": "2024-01-01T00:00:00Z",
            "source": "test"
        }
    }


@pytest.fixture
def sample_search_queries():
    """Sample search queries for testing."""
    return [
        "test document",
        "integration testing",
        "PipesHub AI",
        "search functionality",
        "sample text"
    ]


@pytest.fixture
def mock_user_context():
    """Mock user context for authenticated requests."""
    return {
        "userId": "test-user-123",
        "orgId": "test-org-456",
        "email": "test@example.com",
        "role": "admin"
    }

class HealthCheckerAdapter:
    """Adapter over utils.ServiceHealthChecker for consistent interface."""
    def __init__(self, timeout: float, retry_attempts: int, retry_delay: float):
        try:
            from tests.utils.api_helpers import ServiceHealthChecker as UtilsChecker  # type: ignore
        except ModuleNotFoundError:
            # Load directly from file so that 'tests' doesn't need to be a package
            import importlib.util
            from pathlib import Path
            module_path = Path(__file__).resolve().parent / "utils" / "api_helpers.py"
            spec = importlib.util.spec_from_file_location("tests_utils_api_helpers", module_path)
            if spec is None or spec.loader is None:
                raise
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
            UtilsChecker = getattr(module, "ServiceHealthChecker")
        self._checker = UtilsChecker(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)

    async def check_nodejs_backend(self, base_url: str) -> Dict[str, Any]:
        result = await self._checker.check_service_health(base_url, endpoint="/api/v1/health")
        return self._normalize(result)

    async def check_python_service(self, base_url: str, service_name: str) -> Dict[str, Any]:
        result = await self._checker.check_service_health(base_url, endpoint="/health")
        result["service"] = service_name
        return self._normalize(result)

    def _normalize(self, result: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure status_code is always present for callers asserting on it
        if "status_code" not in result:
            result["status_code"] = None
        return result


@pytest.fixture
def health_checker():
    """Health checker utility using shared implementation."""
    return HealthCheckerAdapter(
        timeout=TEST_CONFIG["timeout"],
        retry_attempts=TEST_CONFIG["retry_attempts"],
        retry_delay=TEST_CONFIG["retry_delay"],
    )


@pytest.fixture(scope="session")
def strict_mode() -> bool:
    """Whether strict service availability is required for tests."""
    return STRICT_MODE


# Test markers
pytest_plugins = []

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "health: mark test as health check test")
    config.addinivalue_line("markers", "indexing: mark test as indexing test")
    config.addinivalue_line("markers", "search: mark test as search test")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "slow: mark test as slow running")


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names."""
    for item in items:
        if "health" in item.name:
            item.add_marker(pytest.mark.health)
        if "indexing" in item.name:
            item.add_marker(pytest.mark.indexing)
        if "search" in item.name:
            item.add_marker(pytest.mark.search)
        if "integration" in item.name:
            item.add_marker(pytest.mark.integration)

