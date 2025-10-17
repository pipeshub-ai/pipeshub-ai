"""
Global pytest configuration and fixtures for PipesHub integration tests.

This file contains shared fixtures and configurations that are available
to all test modules without explicit import.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, Generator

import pytest # type: ignore
from faker import Faker # type: ignore

# Add the project root to Python path
project_root: Path = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Initialize Faker for generating test data
fake: Faker = Faker()


# ============================================================================
# Session-level fixtures
# ============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """
    Create an event loop for the entire test session.
    This is required for async tests to work properly.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def faker_instance() -> Faker:
    """
    Provide a Faker instance for generating test data.
    
    Returns:
        Configured Faker instance
    """
    return fake


# ============================================================================
# Function-level fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def reset_environment() -> Generator[None, None, None]:
    """
    Reset environment state before each test.
    This ensures tests don't interfere with each other.
    """
    # Store original environment
    original_env: Dict[str, str] = os.environ.copy()
    
    yield
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def test_data_dir() -> Path:
    """
    Provide path to test data directory.
    
    Returns:
        Path to test data directory
    """
    data_dir: Path = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir


@pytest.fixture
def temp_dir(tmp_path) -> Path:
    """
    Provide a temporary directory for test artifacts.
    
    Args:
        tmp_path: pytest built-in temporary directory fixture
        
    Returns:
        Path to temporary directory
    """
    return tmp_path


# ============================================================================
# Test lifecycle hooks
# ============================================================================


def pytest_configure(config):
    """
    Configure pytest with custom settings.
    
    This runs before test collection and allows us to set up
    test environment, create directories, etc.
    """
    # Create reports directory if it doesn't exist
    reports_dir: Path = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    
    # Create logs directory for test logs
    logs_dir: Path = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)


def pytest_collection_modifyitems(config, items):
    """
    Modify test items after collection.
    
    This allows us to:
    - Add markers to tests based on their names
    - Skip tests based on environment
    - Reorder tests for optimal execution
    """
    for item in items:
        # Auto-mark integration tests
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        
        # Auto-mark slow tests based on naming
        if "slow" in item.nodeid.lower():
            item.add_marker(pytest.mark.slow)
        
        # Auto-mark auth tests
        if "auth" in item.nodeid.lower():
            item.add_marker(pytest.mark.auth)


def pytest_runtest_setup(item):
    """
    Run setup before each test.
    
    This is called before each test function is executed.
    Useful for checking markers and skipping tests conditionally.
    """
    # Example: Skip external tests if no network
    if "external" in item.keywords:
        if os.getenv("SKIP_EXTERNAL_TESTS", "false").lower() == "true":
            pytest.skip("Skipping external test (SKIP_EXTERNAL_TESTS=true)")


def pytest_runtest_teardown(item, nextitem):
    """
    Run teardown after each test.
    
    This is called after each test function completes.
    Useful for cleanup operations.
    """
    pass


# ============================================================================
# Custom pytest hooks for reporting
# ============================================================================


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    Create custom test report with additional information.
    
    This hook allows us to capture test results and add
    custom data to the report.
    """
    outcome = yield
    report = outcome.get_result()
    
    # Add custom data to report
    if report.when == "call":
        report.test_metadata: Dict[str, Any] = {
            "test_name": item.name,
            "test_file": str(item.fspath),
            "markers": [marker.name for marker in item.iter_markers()],
        }


# ============================================================================
# Assertion helpers
# ============================================================================


def pytest_assertrepr_compare(op, left, right):
    """
    Custom assertion representation for better error messages.
    
    This provides more readable output when assertions fail.
    """
    if isinstance(left, dict) and isinstance(right, dict) and op == "==":
        return [
            "Comparing dictionaries:",
            f"Left keys: {set(left.keys())}",
            f"Right keys: {set(right.keys())}",
            f"Different keys: {set(left.keys()) ^ set(right.keys())}",
        ]

