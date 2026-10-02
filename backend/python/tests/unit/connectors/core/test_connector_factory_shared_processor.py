"""ConnectorFactory hands KB connectors one processor per org; every other connector keeps its own."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector


@pytest.fixture(autouse=True)
def _isolate_factory_state():
    registry = ConnectorFactory._connector_registry.copy()
    ConnectorFactory._shared_org_processors.clear()
    ConnectorFactory._shared_org_processor_locks.clear()
    yield
    ConnectorFactory._connector_registry = registry
    ConnectorFactory._shared_org_processors.clear()
    ConnectorFactory._shared_org_processor_locks.clear()


def _processor_cls(fail_first_init: bool = False):
    """A real class (hashable cache key) that records every instance it builds."""

    class FakeProcessor:
        instances: list["FakeProcessor"] = []
        _init_failures_left = 1 if fail_first_init else 0

        def __init__(self, logger, data_store_provider, config_service) -> None:
            self.org_id = ""
            self.data_store_provider = data_store_provider
            self.initialize_calls = 0
            FakeProcessor.instances.append(self)

        async def initialize(self) -> None:
            await asyncio.sleep(0)
            self.initialize_calls += 1
            if FakeProcessor._init_failures_left:
                FakeProcessor._init_failures_left -= 1
                raise RuntimeError("broker down")

    return FakeProcessor


async def _create(name, connector_id, org_id, processor_cls, data_store_provider=None):
    return await ConnectorFactory.create_connector(
        name=name,
        logger=MagicMock(),
        data_store_provider=data_store_provider or SimpleNamespace(),
        config_service=MagicMock(),
        connector_id=connector_id,
        scope="team",
        created_by="user1",
        org_id=org_id,
        data_entities_processor_cls=processor_cls,
    )


def _register_private_connector(name: str) -> MagicMock:
    connector_cls = MagicMock()
    connector_cls.shares_org_processor = False
    connector_cls.create_connector = AsyncMock(
        side_effect=lambda **kw: SimpleNamespace(data_entities_processor=kw["data_entities_processor"])
    )
    ConnectorFactory.register_connector(name, connector_cls)
    return connector_cls


class TestOptIn:
    def test_only_the_kb_connector_opts_in(self):
        assert BaseConnector.shares_org_processor is False
        assert KnowledgeBaseConnector.shares_org_processor is True
        sharing = {
            name
            for name, connector_cls in ConnectorFactory.list_connectors().items()
            if getattr(connector_cls, "shares_org_processor", False) is True
        }
        assert sharing == {"kb"}


class TestKbConnectorsShareAnOrgProcessor:
    @pytest.mark.asyncio
    async def test_kbs_in_one_org_share_one_initialized_processor(self):
        processor_cls = _processor_cls()

        kb1 = await _create("kb", "kb1", "org1", processor_cls)
        kb2 = await _create("kb", "kb2", "org1", processor_cls)

        assert isinstance(kb1, KnowledgeBaseConnector) and isinstance(kb2, KnowledgeBaseConnector)
        assert kb1 is not kb2
        assert kb1.data_entities_processor is kb2.data_entities_processor
        assert len(processor_cls.instances) == 1
        processor = processor_cls.instances[0]
        assert processor.org_id == "org1"
        assert processor.initialize_calls == 1

    @pytest.mark.asyncio
    async def test_each_org_gets_its_own_processor(self):
        processor_cls = _processor_cls()

        kb1 = await _create("kb", "kb1", "org1", processor_cls)
        kb2 = await _create("kb", "kb2", "org2", processor_cls)

        assert kb1.data_entities_processor is not kb2.data_entities_processor
        assert kb1.data_entities_processor.org_id == "org1"
        assert kb2.data_entities_processor.org_id == "org2"

    @pytest.mark.asyncio
    async def test_processor_classes_never_share(self):
        base_cls, edition_cls = _processor_cls(), _processor_cls()

        kb1 = await _create("kb", "kb1", "org1", base_cls)
        kb2 = await _create("kb", "kb2", "org1", edition_cls)

        assert isinstance(kb1.data_entities_processor, base_cls)
        assert isinstance(kb2.data_entities_processor, edition_cls)

    @pytest.mark.asyncio
    async def test_concurrent_first_creates_build_one_processor(self):
        processor_cls = _processor_cls()

        connectors = await asyncio.gather(
            *[_create("kb", f"kb{i}", "org1", processor_cls) for i in range(10)]
        )

        assert len(processor_cls.instances) == 1
        assert all(c.data_entities_processor is processor_cls.instances[0] for c in connectors)

    @pytest.mark.asyncio
    async def test_failed_initialize_is_not_cached(self):
        processor_cls = _processor_cls(fail_first_init=True)

        assert await _create("kb", "kb1", "org1", processor_cls) is None
        kb = await _create("kb", "kb1", "org1", processor_cls)

        assert kb is not None
        assert len(processor_cls.instances) == 2
        assert kb.data_entities_processor is processor_cls.instances[1]
        assert kb.data_entities_processor.initialize_calls == 1


class TestKbConnectorsFallBackToPrivateProcessor:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("store_org_id", ["org2", ""], ids=["other-org-store", "unscoped-store"])
    async def test_store_scoped_to_another_org(self, store_org_id):
        processor_cls = _processor_cls()
        store = SimpleNamespace(org_id=store_org_id)

        kb1 = await _create("kb", "kb1", "org1", processor_cls, data_store_provider=store)
        kb2 = await _create("kb", "kb2", "org1", processor_cls, data_store_provider=store)

        assert kb1.data_entities_processor is not kb2.data_entities_processor
        assert ConnectorFactory._shared_org_processors == {}

    @pytest.mark.asyncio
    async def test_store_scoped_to_the_same_org_shares(self):
        processor_cls = _processor_cls()
        store = SimpleNamespace(org_id="org1")

        kb1 = await _create("kb", "kb1", "org1", processor_cls, data_store_provider=store)
        kb2 = await _create("kb", "kb2", "org1", processor_cls, data_store_provider=store)

        assert kb1.data_entities_processor is kb2.data_entities_processor

    @pytest.mark.asyncio
    async def test_no_org(self):
        processor_cls = _processor_cls()

        kb1 = await _create("kb", "kb1", None, processor_cls)
        kb2 = await _create("kb", "kb2", None, processor_cls)

        assert kb1.data_entities_processor is not kb2.data_entities_processor
        assert ConnectorFactory._shared_org_processors == {}


class TestOtherConnectorsKeepPrivateProcessors:
    @pytest.mark.asyncio
    async def test_non_opted_in_connector_gets_a_processor_per_instance(self):
        _register_private_connector("private_test")
        processor_cls = _processor_cls()

        c1 = await _create("private_test", "c1", "org1", processor_cls)
        c2 = await _create("private_test", "c2", "org1", processor_cls)

        assert c1.data_entities_processor is not c2.data_entities_processor
        assert all(p.org_id == "org1" and p.initialize_calls == 1 for p in processor_cls.instances)
        assert ConnectorFactory._shared_org_processors == {}

    @pytest.mark.asyncio
    async def test_mock_connector_class_does_not_opt_in_by_accident(self):
        connector_cls = MagicMock()  # shares_org_processor is a MagicMock, not True
        connector_cls.create_connector = AsyncMock(
            side_effect=lambda **kw: SimpleNamespace(data_entities_processor=kw["data_entities_processor"])
        )
        ConnectorFactory.register_connector("mock_test", connector_cls)
        processor_cls = _processor_cls()

        c1 = await _create("mock_test", "c1", "org1", processor_cls)
        c2 = await _create("mock_test", "c2", "org1", processor_cls)

        assert c1.data_entities_processor is not c2.data_entities_processor

    @pytest.mark.asyncio
    async def test_private_connector_never_receives_the_kb_shared_processor(self):
        _register_private_connector("private_test")
        processor_cls = _processor_cls()

        kb = await _create("kb", "kb1", "org1", processor_cls)
        other = await _create("private_test", "c1", "org1", processor_cls)

        assert other.data_entities_processor is not kb.data_entities_processor
