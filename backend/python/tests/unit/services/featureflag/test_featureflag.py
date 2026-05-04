"""
Tests for the feature flag system:
  - FeatureFlagService (singleton, is_feature_enabled, set_provider, init_with_etcd_provider)
  - EtcdProvider      (refresh, get_flag_value, get_all_flags, error handling)
"""

import logging
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.services.featureflag.featureflag import FeatureFlagService
from app.services.featureflag.interfaces.config import IConfigProvider
from app.services.featureflag.provider.etcd import EtcdProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the singleton is cleared before and after every test."""
    FeatureFlagService.reset_instance()
    yield
    FeatureFlagService.reset_instance()


@pytest.fixture
def mock_provider():
    """Return a minimal mock implementing IConfigProvider."""
    provider = MagicMock(spec=IConfigProvider)
    provider.get_flag_value = MagicMock(return_value=None)
    provider.refresh = MagicMock()
    return provider


@pytest.fixture
def mock_logger():
    return logging.getLogger("test_featureflag")


# ===========================================================================
# FeatureFlagService
# ===========================================================================


class TestFeatureFlagServiceSingleton:
    """Singleton pattern and reset behaviour."""

    def test_get_service_returns_same_instance(self, mock_provider):
        svc1 = FeatureFlagService.get_service(provider=mock_provider)
        svc2 = FeatureFlagService.get_service()
        assert svc1 is svc2

    def test_reset_instance_clears_singleton(self, mock_provider):
        svc1 = FeatureFlagService.get_service(provider=mock_provider)
        FeatureFlagService.reset_instance()
        # After reset, a new instance should be created
        svc2 = FeatureFlagService.get_service(provider=mock_provider)
        assert svc1 is not svc2

    def test_direct_instantiation_after_singleton_raises(self, mock_provider):
        FeatureFlagService.get_service(provider=mock_provider)
        with pytest.raises(RuntimeError, match="Use get_service"):
            FeatureFlagService(mock_provider)

    def test_lock_attribute_exists(self):
        """Thread safety: the class-level lock must exist."""
        assert hasattr(FeatureFlagService, "_lock")


class TestIsFeatureEnabled:
    """is_feature_enabled() delegation and default handling."""

    def test_flag_present_true(self, mock_provider):
        mock_provider.get_flag_value.return_value = True
        svc = FeatureFlagService.get_service(provider=mock_provider)
        assert svc.is_feature_enabled("MY_FLAG") is True
        mock_provider.get_flag_value.assert_called_once_with("MY_FLAG")

    def test_flag_present_false(self, mock_provider):
        mock_provider.get_flag_value.return_value = False
        svc = FeatureFlagService.get_service(provider=mock_provider)
        assert svc.is_feature_enabled("MY_FLAG") is False

    def test_flag_absent_returns_default_false(self, mock_provider):
        mock_provider.get_flag_value.return_value = None
        svc = FeatureFlagService.get_service(provider=mock_provider)
        assert svc.is_feature_enabled("MISSING_FLAG") is False

    def test_flag_absent_returns_custom_default_true(self, mock_provider):
        mock_provider.get_flag_value.return_value = None
        svc = FeatureFlagService.get_service(provider=mock_provider)
        assert svc.is_feature_enabled("MISSING_FLAG", default=True) is True

    def test_flag_absent_returns_custom_default_false(self, mock_provider):
        mock_provider.get_flag_value.return_value = None
        svc = FeatureFlagService.get_service(provider=mock_provider)
        assert svc.is_feature_enabled("MISSING_FLAG", default=False) is False


class TestSetProvider:
    """set_provider() swaps the underlying provider at runtime."""

    def test_set_provider_switches_behaviour(self, mock_provider):
        svc = FeatureFlagService.get_service(provider=mock_provider)

        new_provider = MagicMock(spec=IConfigProvider)
        new_provider.get_flag_value.return_value = True
        svc.set_provider(new_provider)

        assert svc.is_feature_enabled("FLAG") is True
        new_provider.get_flag_value.assert_called_once_with("FLAG")
        # Old provider should NOT have been called for "FLAG" after swap
        mock_provider.get_flag_value.assert_not_called()


class TestInitWithEtcdProvider:
    """Async initialization path via init_with_etcd_provider."""

    @pytest.mark.asyncio
    async def test_init_with_etcd_provider_success(self, mock_logger):
        etcd_provider = MagicMock(spec=EtcdProvider)
        etcd_provider.refresh = AsyncMock()
        etcd_provider.get_flag_value = MagicMock(return_value=True)

        svc = await FeatureFlagService.init_with_etcd_provider(
            etcd_provider, mock_logger
        )
        etcd_provider.refresh.assert_awaited_once()
        assert svc.is_feature_enabled("X") is True

    @pytest.mark.asyncio
    async def test_init_with_etcd_provider_refresh_failure_still_creates(self, mock_logger):
        etcd_provider = MagicMock(spec=EtcdProvider)
        etcd_provider.refresh = AsyncMock(side_effect=Exception("connection error"))
        etcd_provider.get_flag_value = MagicMock(return_value=None)

        svc = await FeatureFlagService.init_with_etcd_provider(
            etcd_provider, mock_logger
        )
        # Service is still created even though refresh failed
        assert svc is not None
        assert svc.is_feature_enabled("X") is False  # default


class TestRefresh:
    """Async refresh() delegates to provider."""

    @pytest.mark.asyncio
    async def test_refresh_delegates_to_provider(self, mock_provider):
        mock_provider.refresh = AsyncMock()
        svc = FeatureFlagService.get_service(provider=mock_provider)
        await svc.refresh()
        mock_provider.refresh.assert_awaited_once()


# ===========================================================================
# EtcdProvider
# ===========================================================================


class TestEtcdProviderRefresh:
    """refresh() extracts featureFlags and normalises keys."""

    @pytest.mark.asyncio
    async def test_refresh_parses_feature_flags(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {
                "enableSearch": True,
                "enableWorkflow": False,
            }
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()

        assert provider.get_flag_value("ENABLESEARCH") is True
        assert provider.get_flag_value("ENABLEWORKFLOW") is False

    @pytest.mark.asyncio
    async def test_refresh_normalises_keys_to_uppercase(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {"myFlag": True}
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()

        assert provider.get_flag_value("MYFLAG") is True
        assert provider.get_flag_value("myflag") is True

    @pytest.mark.asyncio
    async def test_refresh_error_preserves_existing_flags(self):
        config_service = MagicMock()
        # First successful refresh
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {"existingFlag": True}
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_flag_value("EXISTINGFLAG") is True

        # Second refresh fails
        config_service.get_config = AsyncMock(side_effect=Exception("network error"))
        await provider.refresh()
        # Existing flags must still be there
        assert provider.get_flag_value("EXISTINGFLAG") is True

    @pytest.mark.asyncio
    async def test_refresh_missing_feature_flags_key_yields_empty(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "fileUploadMaxSizeBytes": 1024
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_all_flags() == {}

    @pytest.mark.asyncio
    async def test_refresh_empty_settings_yields_empty(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={})
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_all_flags() == {}

    @pytest.mark.asyncio
    async def test_refresh_settings_not_dict_yields_empty(self):
        """If settings itself is not a dict, featureFlags extraction should be empty."""
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value="not a dict")
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_all_flags() == {}


class TestEtcdProviderInvalidFeatureFlagsFormat:
    """When featureFlags is not a dict, should result in empty flags."""

    @pytest.mark.asyncio
    async def test_feature_flags_is_list_yields_empty(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": ["not", "a", "dict"]
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_all_flags() == {}

    @pytest.mark.asyncio
    async def test_feature_flags_is_string_yields_empty(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": "invalid"
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_all_flags() == {}

    @pytest.mark.asyncio
    async def test_feature_flags_is_none_yields_empty(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": None
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_all_flags() == {}


class TestEtcdProviderGetFlagValue:
    """get_flag_value case-insensitive lookup."""

    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {"MyFlag": True}
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()

        assert provider.get_flag_value("myflag") is True
        assert provider.get_flag_value("MYFLAG") is True
        assert provider.get_flag_value("MyFlag") is True

    @pytest.mark.asyncio
    async def test_missing_flag_returns_none(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {}
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        assert provider.get_flag_value("NOPE") is None

    def test_no_refresh_returns_none(self):
        config_service = MagicMock()
        provider = EtcdProvider(config_service)
        assert provider.get_flag_value("ANY") is None


class TestEtcdProviderGetAllFlags:
    """get_all_flags returns a copy."""

    @pytest.mark.asyncio
    async def test_returns_copy_not_reference(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {"flag": True}
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()

        flags = provider.get_all_flags()
        flags["INJECTED"] = False  # mutate the copy
        # Original must be unaffected
        assert "INJECTED" not in provider.get_all_flags()

    @pytest.mark.asyncio
    async def test_returns_all_flags(self):
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "featureFlags": {"a": True, "b": False, "c": True}
        })
        provider = EtcdProvider(config_service)
        await provider.refresh()
        flags = provider.get_all_flags()
        assert flags == {"A": True, "B": False, "C": True}
