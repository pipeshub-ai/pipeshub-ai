"""CPD3-10, CPD3-13, CPD3-15, CPD3-16, CPD3-18, CPD3-20: ``PolicyResolverContract`` against ``DimensionPolicyResolver``."""

from __future__ import annotations

from typing import Any

import pytest

from harness_core.policy import DimensionPolicyResolver
from harness_core.testing.contracts.policy_resolver import PolicyResolverContract

from .support import POLICY_VECTORS


class TestDimensionPolicyResolver(PolicyResolverContract):
    @pytest.fixture
    def resolver(self) -> DimensionPolicyResolver:
        return DimensionPolicyResolver()

    @pytest.fixture
    def policy_vectors(self) -> dict[str, Any]:
        return POLICY_VECTORS
