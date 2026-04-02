from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.docling import DoclingAppContainer, initialize_container


class TestDoclingAppContainerInstantiation:
    def test_container_can_be_instantiated(self):
        container = DoclingAppContainer()
        assert container is not None

    def test_logger_provider_exists(self):
        container = DoclingAppContainer()
        logger = container.logger()
        assert logger is not None

    def test_logger_is_singleton(self):
        container = DoclingAppContainer()
        l1 = container.logger()
        l2 = container.logger()
        assert l1 is l2

    def test_key_value_store_provider_exists(self):
        container = DoclingAppContainer()
        assert container.key_value_store is not None

    def test_config_service_provider_exists(self):
        container = DoclingAppContainer()
        assert container.config_service is not None

    def test_container_utils_on_class(self):
        assert DoclingAppContainer.container_utils is not None

    def test_wiring_config_modules(self):
        container = DoclingAppContainer()
        wiring = container.wiring_config
        expected = [
            "app.docling_main",
            "app.services.docling.docling_service",
            "app.modules.parsers.pdf.docling",
            "app.utils.converters.docling_doc_to_blocks",
        ]
        for mod in expected:
            assert mod in wiring.modules

    def test_wiring_config_module_count(self):
        container = DoclingAppContainer()
        assert len(container.wiring_config.modules) == 4


class TestInitializeContainer:
    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        container = MagicMock()
        container.logger.return_value = MagicMock()
        result = await initialize_container(container)
        assert result is True

    @pytest.mark.asyncio
    async def test_logs_init_message(self):
        logger = MagicMock()
        container = MagicMock()
        container.logger.return_value = logger
        await initialize_container(container)
        assert any("Initializing Docling" in str(c) for c in logger.info.call_args_list)

    @pytest.mark.asyncio
    async def test_logs_success_message(self):
        logger = MagicMock()
        container = MagicMock()
        container.logger.return_value = logger
        await initialize_container(container)
        assert any("configuration initialized" in str(c) for c in logger.info.call_args_list)

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        container = MagicMock()
        container.logger.side_effect = RuntimeError("logger init failed")
        with pytest.raises(RuntimeError, match="logger init failed"):
            await initialize_container(container)

    @pytest.mark.asyncio
    async def test_exception_logs_error(self):
        logger = MagicMock()
        logger.info.side_effect = [None, RuntimeError("mid-init")]
        container = MagicMock()
        container.logger.return_value = logger
        with pytest.raises(RuntimeError):
            await initialize_container(container)
        logger.exception.assert_called()
