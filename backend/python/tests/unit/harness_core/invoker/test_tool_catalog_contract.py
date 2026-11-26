"""Both tool catalogs pass the shared ``ToolCatalog`` contract (TC-1..TC-8)."""

from __future__ import annotations

import pytest

from harness_core.testing.contracts.tool_catalog import ToolCatalogContract
from tests.unit.harness_core.invoker.catalogs import MemoryCatalogs, RegistryCatalogs


class TestInMemoryToolCatalog(ToolCatalogContract):
    @pytest.fixture
    def harness(self) -> MemoryCatalogs:  # type: ignore[override]
        return MemoryCatalogs()


class TestRegistryToolPort(ToolCatalogContract):
    @pytest.fixture
    def harness(self) -> RegistryCatalogs:  # type: ignore[override]
        return RegistryCatalogs()


def test_a_grant_naming_a_tool_nobody_offers_is_refused_at_build() -> None:
    from harness_core.testing.contracts.tool_catalog import TOOLS

    with pytest.raises(ValueError, match="nobody offers"):
        _ = MemoryCatalogs().build(TOOLS, ["crm@inst-1/ghost"])
