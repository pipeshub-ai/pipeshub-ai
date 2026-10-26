"""Every entities processor the factory builds opens its own broker connection.

A connector that fails init() is retried on each resync, and each attempt built
one or two processors nobody closed. On a live box that left ~1,400 Redis
connections open in the connectors process, pushing new sockets past fd 1024,
where smbprotocol's select() fails. These tests pin that every processor is
closed once nothing uses it, and that a shared org processor never is.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    dispose_connector,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.sources.cifs.connector import CifsConnector
from app.connectors.sources.network_share.entities_processor import (
    NetworkShareEntitiesProcessor,
)
from app.connectors.sources.smb.connector import SmbConnector


class FakeProducer:
    """Counts connections the way a RedisStreamsProducer holds one per instance."""

    open_connections = 0

    def __init__(self) -> None:
        self.connected = False

    async def initialize(self) -> None:
        if not self.connected:
            self.connected = True
            FakeProducer.open_connections += 1

    async def cleanup(self) -> None:
        if self.connected:
            self.connected = False
            FakeProducer.open_connections -= 1


@pytest.fixture(autouse=True)
def fake_broker() -> Iterator[None]:
    """Real processors, with a producer that counts connections instead of opening them."""
    FakeProducer.open_connections = 0
    registry = ConnectorFactory._connector_registry.copy()
    ConnectorFactory._shared_org_processors.clear()
    ConnectorFactory._shared_org_processor_locks.clear()
    with (
        patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor."
            "MessagingUtils.create_producer_config_from_service",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor."
            "MessagingFactory.create_producer",
            side_effect=lambda **_: FakeProducer(),
        ),
    ):
        yield
    ConnectorFactory._connector_registry = registry
    ConnectorFactory._shared_org_processors.clear()
    ConnectorFactory._shared_org_processor_locks.clear()


def _factory_kwargs(name: str, connector_id: str = "conn-1") -> dict:
    data_store_provider = MagicMock()
    data_store_provider.org_id = None
    return {
        "name": name,
        "logger": MagicMock(),
        "data_store_provider": data_store_provider,
        "config_service": MagicMock(),
        "connector_id": connector_id,
        "scope": "team",
        "created_by": "user-1",
        "org_id": "org-1",
        "data_entities_processor_cls": DataSourceEntitiesProcessor,
    }


class TestProcessorCleanup:
    async def test_closes_the_producer_initialize_opened(self) -> None:
        processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), MagicMock())
        processor.org_id = "org-1"
        await processor.initialize()
        assert FakeProducer.open_connections == 1

        await processor.cleanup()
        await processor.cleanup()

        assert FakeProducer.open_connections == 0

    async def test_is_a_no_op_before_initialize(self) -> None:
        processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), MagicMock())

        await processor.cleanup()

        assert FakeProducer.open_connections == 0


@pytest.mark.parametrize("connector_cls", [SmbConnector, CifsConnector])
class TestNetworkShareConnectorsBuildOneProcessor:
    async def test_factory_builds_exactly_one_processor_of_the_connector_class(self, connector_cls) -> None:
        ConnectorFactory.register_connector("share_under_test", connector_cls)

        connector = await ConnectorFactory.create_connector(**_factory_kwargs("share_under_test"))

        assert isinstance(connector.data_entities_processor, NetworkShareEntitiesProcessor)
        assert connector.data_entities_processor.org_id == "org-1"
        assert FakeProducer.open_connections == 1
        assert connector._owns_entities_processor is True

    async def test_repeated_failed_init_leaves_no_connection_open(self, connector_cls) -> None:
        """The demo-box leak: every resync of an unreachable share retried this."""
        ConnectorFactory.register_connector("share_under_test", connector_cls)

        with patch.object(connector_cls, "init", new=AsyncMock(return_value=False)):
            for _ in range(5):
                assert await ConnectorFactory.initialize_connector(
                    **_factory_kwargs("share_under_test")
                ) is None

        assert FakeProducer.open_connections == 0

    async def test_init_raising_leaves_no_connection_open(self, connector_cls) -> None:
        ConnectorFactory.register_connector("share_under_test", connector_cls)

        with patch.object(connector_cls, "init", new=AsyncMock(side_effect=OSError("down"))):
            for _ in range(3):
                assert await ConnectorFactory.initialize_connector(
                    **_factory_kwargs("share_under_test")
                ) is None

        assert FakeProducer.open_connections == 0

    async def test_disposing_a_live_connector_closes_its_connection(self, connector_cls) -> None:
        ConnectorFactory.register_connector("share_under_test", connector_cls)
        with patch.object(connector_cls, "init", new=AsyncMock(return_value=True)):
            connector = await ConnectorFactory.initialize_connector(
                **_factory_kwargs("share_under_test")
            )
        assert FakeProducer.open_connections == 1

        await dispose_connector(connector)

        assert FakeProducer.open_connections == 0


class TestSmbEditionProcessorSeam:
    async def test_factory_builds_an_edition_subclass_processor(self) -> None:
        class EditionProcessor(NetworkShareEntitiesProcessor):
            pass

        class EditionSmb(SmbConnector):
            processor_cls = EditionProcessor

        ConnectorFactory.register_connector("smb_edition", EditionSmb)

        connector = await ConnectorFactory.create_connector(**_factory_kwargs("smb_edition"))

        assert type(connector.data_entities_processor) is EditionProcessor
        assert FakeProducer.open_connections == 1


class _SelfBuiltProcessorConnector(BaseConnector):
    """The S3/GCS/Azure pattern: ignore the factory's processor and build one."""

    def __init__(self, processor) -> None:
        self.data_entities_processor = processor
        self.logger = MagicMock()
        self.connector_id = "conn-1"

    @classmethod
    async def create_connector(cls, logger, data_store_provider, config_service, connector_id,
                               data_entities_processor, **kwargs) -> "_SelfBuiltProcessorConnector":
        own = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        own.org_id = kwargs["org_id"]
        await own.initialize()
        return cls(own)

    init = AsyncMock(return_value=False)
    cleanup = AsyncMock()


_SelfBuiltProcessorConnector.__abstractmethods__ = frozenset()


class TestFactoryClosesProcessorsItDoesNotHandOff:
    async def test_closes_its_processor_when_the_connector_builds_its_own(self) -> None:
        ConnectorFactory.register_connector("self_built", _SelfBuiltProcessorConnector)

        connector = await ConnectorFactory.create_connector(**_factory_kwargs("self_built"))

        assert FakeProducer.open_connections == 1
        assert connector._owns_entities_processor is True
        await dispose_connector(connector)
        assert FakeProducer.open_connections == 0

    async def test_closes_its_processor_when_create_connector_raises(self) -> None:
        broken = MagicMock()
        broken.processor_cls = None
        broken.create_connector = AsyncMock(side_effect=RuntimeError("bad config"))
        ConnectorFactory.register_connector("broken", broken)

        assert await ConnectorFactory.create_connector(**_factory_kwargs("broken")) is None

        assert FakeProducer.open_connections == 0

    async def test_closes_its_processor_when_create_connector_returns_none(self) -> None:
        empty = MagicMock()
        empty.processor_cls = None
        empty.create_connector = AsyncMock(return_value=None)
        ConnectorFactory.register_connector("empty", empty)

        assert await ConnectorFactory.create_connector(**_factory_kwargs("empty")) is None

        assert FakeProducer.open_connections == 0

    async def test_closes_its_processor_when_initialize_fails_after_connecting(self) -> None:
        ConnectorFactory.register_connector("share_under_test", SmbConnector)
        kwargs = _factory_kwargs("share_under_test")
        kwargs["org_id"] = None

        class NoOrgProcessor(NetworkShareEntitiesProcessor):
            allow_first_org_fallback = False

        with patch.object(SmbConnector, "processor_cls", NoOrgProcessor):
            assert await ConnectorFactory.create_connector(**kwargs) is None

        assert FakeProducer.open_connections == 0


class _SharedProcessorConnector(_SelfBuiltProcessorConnector):
    shares_org_processor = True

    @classmethod
    async def create_connector(cls, logger, data_store_provider, config_service, connector_id,
                               data_entities_processor, **kwargs) -> "_SelfBuiltProcessorConnector":
        return cls(data_entities_processor)


class TestSharedOrgProcessorStaysOpen:
    async def test_failed_init_does_not_close_the_shared_processor(self) -> None:
        ConnectorFactory.register_connector("shared", _SharedProcessorConnector)

        for connector_id in ("kb-1", "kb-2"):
            assert await ConnectorFactory.initialize_connector(
                **_factory_kwargs("shared", connector_id)
            ) is None

        assert FakeProducer.open_connections == 1

    async def test_disposing_a_connector_does_not_close_the_shared_processor(self) -> None:
        ConnectorFactory.register_connector("shared", _SharedProcessorConnector)
        first = await ConnectorFactory.create_connector(**_factory_kwargs("shared", "kb-1"))
        second = await ConnectorFactory.create_connector(**_factory_kwargs("shared", "kb-2"))
        assert first.data_entities_processor is second.data_entities_processor

        await dispose_connector(first)

        assert first._owns_entities_processor is False
        assert FakeProducer.open_connections == 1


class TestDisposeConnector:
    async def test_closes_the_processor_even_when_cleanup_raises(self) -> None:
        ConnectorFactory.register_connector("share_under_test", SmbConnector)
        connector = await ConnectorFactory.create_connector(**_factory_kwargs("share_under_test"))

        with (
            patch.object(SmbConnector, "cleanup", new=AsyncMock(side_effect=RuntimeError("x"))),
            pytest.raises(RuntimeError),
        ):
            await dispose_connector(connector)

        assert FakeProducer.open_connections == 0

    async def test_leaves_a_processor_it_was_never_given(self) -> None:
        """A connector built outside the factory (tests, scripts) owns nothing."""
        processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), MagicMock())
        processor.org_id = "org-1"
        await processor.initialize()
        connector = _SelfBuiltProcessorConnector(processor)

        await dispose_connector(connector)

        connector.cleanup.assert_awaited()
        assert FakeProducer.open_connections == 1

    async def test_only_cleans_up_an_object_that_is_not_a_connector(self) -> None:
        connector = MagicMock()
        connector.cleanup = AsyncMock()

        await dispose_connector(connector)

        connector.cleanup.assert_awaited_once()
