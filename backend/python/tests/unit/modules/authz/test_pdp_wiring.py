"""The PDP client is registered in the query and connector containers."""

from __future__ import annotations

import pytest
from dependency_injector import providers

from app.edition_containers import ConnectorAppContainer, QueryAppContainer
from app.modules.authz.node_pdp_client import AiohttpPdpHttp, NodePdpClient


@pytest.mark.parametrize("container_cls", [QueryAppContainer, ConnectorAppContainer], ids=["query", "connector"])
def test_container_registers_a_node_pdp_client_singleton(container_cls) -> None:
    provider = container_cls.node_pdp_client
    assert isinstance(provider, providers.Singleton)
    assert provider.cls is NodePdpClient
    assert provider.kwargs["config_service"] is container_cls.config_service
    assert provider.kwargs["http"].cls is AiohttpPdpHttp
