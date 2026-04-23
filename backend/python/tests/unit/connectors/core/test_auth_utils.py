"""Unit tests for app.connectors.core.registry.auth_utils."""

import pytest

from app.connectors.core.registry.auth_builder import OAuthConfig, OAuthScopeConfig
from app.connectors.core.registry.auth_utils import auth_field_to_dict, auto_add_oauth_fields
from app.connectors.core.registry.types import AuthField, FileContentValidationRule, ValidationRuleType


# ---------------------------------------------------------------------------
# auth_field_to_dict
# ---------------------------------------------------------------------------

class TestAuthFieldToDict:
    def test_basic_text_field(self):
        field = AuthField(
            name="clientId",
            display_name="Client ID",
            placeholder="Enter client ID",
            description="The OAuth client identifier",
            field_type="TEXT",
            required=True,
            usage="BOTH",
            default_value="",
            min_length=1,
            max_length=500,
            is_secret=False,
        )
        result = auth_field_to_dict(field)
        assert result["name"] == "clientId"
        assert result["displayName"] == "Client ID"
        assert result["placeholder"] == "Enter client ID"
        assert result["description"] == "The OAuth client identifier"
        assert result["fieldType"] == "TEXT"
        assert result["required"] is True
        assert result["usage"] == "BOTH"
        assert result["defaultValue"] == ""
        assert result["validation"]["minLength"] == 1
        assert result["validation"]["maxLength"] == 500
        assert "acceptedFileTypes" not in result["validation"]
        assert "validationRules" not in result["validation"]
        assert result["isSecret"] is False

    def test_secret_field(self):
        field = AuthField(
            name="clientSecret",
            display_name="Client Secret",
            is_secret=True,
        )
        result = auth_field_to_dict(field)
        assert result["isSecret"] is True

    def test_checkbox_field_min_length_zero(self):
        field = AuthField(
            name="enable_feature",
            display_name="Enable Feature",
            field_type="CHECKBOX",
            min_length=10,  # should be overridden to 0
        )
        result = auth_field_to_dict(field)
        assert result["validation"]["minLength"] == 0

    def test_non_checkbox_preserves_min_length(self):
        field = AuthField(
            name="token",
            display_name="Token",
            field_type="TEXT",
            min_length=5,
        )
        result = auth_field_to_dict(field)
        assert result["validation"]["minLength"] == 5

    def test_validation_rules_serialized_sparse(self):
        rules = [
            FileContentValidationRule(
                type=ValidationRuleType.JSON_HAS_FIELDS,
                required_fields=["a"],
                error_message="err",
            ),
        ]
        field = AuthField(
            name="key",
            display_name="Key",
            field_type="FILE",
            min_length=0,
            validation_rules=rules,
        )
        result = auth_field_to_dict(field)
        assert result["validation"]["acceptedFileTypes"] == []
        vr = result["validation"]["validationRules"]
        assert len(vr) == 1
        assert vr[0] == {"type": "json_has_fields", "requiredFields": ["a"], "errorMessage": "err"}

    def test_default_values(self):
        field = AuthField(name="f", display_name="F")
        result = auth_field_to_dict(field)
        assert result["fieldType"] == "TEXT"
        assert result["required"] is True
        assert result["usage"] == "BOTH"
        assert result["defaultValue"] == ""
        assert result["validation"]["minLength"] == 1
        assert result["validation"]["maxLength"] == 1000
        assert "acceptedFileTypes" not in result["validation"]
        assert "validationRules" not in result["validation"]

    def test_file_field_includes_empty_file_validation_keys(self):
        field = AuthField(
            name="creds",
            display_name="Credentials",
            field_type="FILE",
            min_length=0,
        )
        result = auth_field_to_dict(field)
        assert result["validation"]["acceptedFileTypes"] == []
        assert result["validation"]["validationRules"] == []


# ---------------------------------------------------------------------------
# auto_add_oauth_fields
# ---------------------------------------------------------------------------

class TestAutoAddOAuthFields:
    def _make_config_dict(self, schemas=None):
        config = {"auth": {}}
        if schemas is not None:
            config["auth"]["schemas"] = schemas
        return config

    def _make_oauth_config(self, auth_fields):
        return OAuthConfig(
            connector_name="Test",
            authorize_url="https://example.com/auth",
            token_url="https://example.com/token",
            redirect_uri="callback",
            scopes=OAuthScopeConfig(),
            auth_fields=auth_fields,
        )

    def test_adds_fields_to_empty_schema(self):
        config = self._make_config_dict()
        oauth = self._make_oauth_config([
            AuthField(name="clientId", display_name="Client ID"),
            AuthField(name="clientSecret", display_name="Client Secret"),
        ])
        auto_add_oauth_fields(config, oauth, "OAUTH")
        fields = config["auth"]["schemas"]["OAUTH"]["fields"]
        assert len(fields) == 2
        assert fields[0]["name"] == "clientId"
        assert fields[1]["name"] == "clientSecret"

    def test_does_not_duplicate_existing_fields(self):
        existing = {
            "OAUTH": {
                "fields": [{"name": "clientId", "displayName": "Already There"}]
            }
        }
        config = self._make_config_dict(schemas=existing)
        oauth = self._make_oauth_config([
            AuthField(name="clientId", display_name="Client ID"),
            AuthField(name="clientSecret", display_name="Client Secret"),
        ])
        auto_add_oauth_fields(config, oauth, "OAUTH")
        fields = config["auth"]["schemas"]["OAUTH"]["fields"]
        assert len(fields) == 2  # 1 existing + 1 new
        names = [f.get("name") or f.get("displayName") for f in fields]
        assert "clientId" in names
        assert "clientSecret" in [f["name"] for f in fields if isinstance(f, dict) and "name" in f]

    def test_creates_schema_structure_if_missing(self):
        config = self._make_config_dict()
        oauth = self._make_oauth_config([AuthField(name="token", display_name="Token")])
        auto_add_oauth_fields(config, oauth, "API_TOKEN")
        assert "API_TOKEN" in config["auth"]["schemas"]
        assert len(config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 1

    def test_creates_schemas_key_if_missing(self):
        config = {"auth": {}}
        oauth = self._make_oauth_config([AuthField(name="x", display_name="X")])
        auto_add_oauth_fields(config, oauth, "OAUTH")
        assert "schemas" in config["auth"]
        assert "OAUTH" in config["auth"]["schemas"]

    def test_no_fields_no_change(self):
        config = self._make_config_dict()
        oauth = self._make_oauth_config([])
        auto_add_oauth_fields(config, oauth, "OAUTH")
        fields = config["auth"]["schemas"]["OAUTH"]["fields"]
        assert fields == []

    def test_multiple_auth_types(self):
        config = self._make_config_dict()
        oauth = self._make_oauth_config([AuthField(name="a", display_name="A")])
        auto_add_oauth_fields(config, oauth, "OAUTH")
        auto_add_oauth_fields(config, oauth, "API_TOKEN")
        assert "OAUTH" in config["auth"]["schemas"]
        assert "API_TOKEN" in config["auth"]["schemas"]
