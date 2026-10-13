"""Fixtures for the object-store connector behaviour tests (fakes live in object_store_behaviour_fakes)."""

import pytest
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
)


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def checkpoints() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture
def config() -> FakeConfigService:
    return FakeConfigService()
