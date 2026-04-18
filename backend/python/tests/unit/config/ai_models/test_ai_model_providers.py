"""Smoke tests for all registered AI model provider declarations."""

import pytest

from app.config.ai_models.providers import ALL_PROVIDER_CLASSES
from app.config.ai_models.registry import ai_model_registry


# Ensure the module-level registration has run
import app.config.ai_models.providers  # noqa: F401


class TestAllProviderRegistrations:
    """Verify every declared provider class is correctly decorated and registered."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.providers = ai_model_registry.list_providers()
        self.provider_map = {p["providerId"]: p for p in self.providers}

    def test_at_least_20_providers_registered(self):
        assert len(self.providers) >= 20

    def test_all_classes_have_metadata(self):
        for cls in ALL_PROVIDER_CLASSES:
            assert hasattr(cls, "_provider_metadata"), f"{cls.__name__} missing _provider_metadata"
            meta = cls._provider_metadata
            assert "name" in meta
            assert "providerId" in meta
            assert "capabilities" in meta
            assert "fields" in meta

    def test_each_provider_has_required_keys(self):
        required_keys = {"name", "providerId", "capabilities", "fields", "iconPath", "color"}
        for p in self.providers:
            missing = required_keys - set(p.keys())
            assert not missing, f"{p['providerId']} missing keys: {missing}"

    def test_capabilities_are_non_empty(self):
        for p in self.providers:
            assert len(p["capabilities"]) >= 1, f"{p['providerId']} has no capabilities"

    @pytest.mark.parametrize(
        "provider_id",
        [cls._provider_metadata["providerId"] for cls in ALL_PROVIDER_CLASSES],
    )
    def test_provider_registered_in_singleton(self, provider_id):
        assert provider_id in self.provider_map, f"{provider_id} not found in registry"

    def test_fields_for_each_capability_exist(self):
        for p in self.providers:
            if p["providerId"] == "default":
                continue
            for cap in p["capabilities"]:
                fields = p["fields"].get(cap, [])
                assert len(fields) >= 1, (
                    f"{p['providerId']} has no fields for capability '{cap}'"
                )

    def test_model_field_present_per_capability(self):
        """Every non-default provider should expose a 'model' field."""
        for p in self.providers:
            if p["providerId"] == "default":
                continue
            for cap in p["capabilities"]:
                field_names = [f["name"] for f in p["fields"].get(cap, [])]
                assert "model" in field_names, (
                    f"{p['providerId']} / {cap} missing 'model' field"
                )

    def test_field_names_unique_per_capability(self):
        for p in self.providers:
            for cap, fields in p["fields"].items():
                names = [f["name"] for f in fields]
                assert len(names) == len(set(names)), (
                    f"{p['providerId']} / {cap} has duplicate field names: {names}"
                )

    def test_icon_paths_start_with_slash(self):
        for p in self.providers:
            assert p["iconPath"].startswith("/"), (
                f"{p['providerId']} icon path does not start with '/': {p['iconPath']}"
            )

    def test_popular_providers_include_expected(self):
        popular_ids = [p["providerId"] for p in self.providers if p.get("isPopular")]
        assert "openAI" in popular_ids
        assert "gemini" in popular_ids
        assert "anthropic" in popular_ids
