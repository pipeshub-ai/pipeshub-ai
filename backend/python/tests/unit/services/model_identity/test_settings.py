import pytest

from app.services.model_identity.sanitize import (
    clean_header_value,
    clean_tag_value,
    clean_text,
    is_forbidden_header_name,
    is_valid_header_name,
    join_capped,
)
from app.services.model_identity.settings import (
    ConnectionForwardingSettings,
    IdentityGovernancePolicy,
    SettingsError,
)


class TestGovernance:
    def test_defaults_minimise(self) -> None:
        p = IdentityGovernancePolicy.parse(None)
        assert p.subject_source == "userId"
        assert not p.include_email and not p.include_display_name and not p.pseudonymize
        assert p.max_forwarded_groups == 50

    def test_round_trip(self) -> None:
        raw = {
            "subjectSource": "idpObjectId",
            "includeEmail": True,
            "groups": {"enabled": True, "allowList": ["b", "a"], "maxForwarded": 10},
            "pseudonymize": True,
            "onMissingIdentity": "block",
            "queryEmbeddingIdentity": "service",
            "serviceIdentityPrefix": "svc:acme",
        }
        parsed = IdentityGovernancePolicy.parse(raw, strict=True)
        assert parsed.groups_allow_list == frozenset({"a", "b"})
        again = IdentityGovernancePolicy.parse(parsed.to_dict(), strict=True)
        assert again == parsed

    def test_runtime_parse_falls_back_on_bad_values(self) -> None:
        p = IdentityGovernancePolicy.parse({"subjectSource": "nope", "groups": {"maxForwarded": -1}})
        assert p.subject_source == "userId" and p.max_forwarded_groups == 50

    def test_non_object_is_default(self) -> None:
        assert IdentityGovernancePolicy.parse("junk") == IdentityGovernancePolicy()

    @pytest.mark.parametrize(
        "raw",
        [
            {"subjectSource": "nope"},
            {"includeEmail": "yes"},
            {"groups": {"maxForwarded": 0}},
            {"groups": {"maxForwarded": True}},
            {"groups": {"allowList": "g1"}},
            {"onMissingIdentity": "ignore"},
            {"serviceIdentityPrefix": ""},
            {"groups": "x"},
        ],
    )
    def test_strict_parse_rejects(self, raw) -> None:
        with pytest.raises(SettingsError):
            IdentityGovernancePolicy.parse(raw, strict=True)


class TestConnection:
    def test_off_by_default(self) -> None:
        assert ConnectionForwardingSettings.parse(None).enabled is False
        assert ConnectionForwardingSettings.parse({}).enabled is False

    def test_templates_are_validated_strictly(self) -> None:
        ok = ConnectionForwardingSettings.parse(
            {
                "enabled": True,
                "mode": "template",
                "headerTemplates": {"X-User": "{{SUBJECT}}", "X-G": "{{ GROUP_IDS }}"},
            },
            strict=True,
        )
        assert dict(ok.header_templates)["X-User"] == "{{SUBJECT}}"

    @pytest.mark.parametrize(
        "templates",
        [
            {"Authorization": "{{SUBJECT}}"},
            {"Host": "x"},
            {"bad name": "x"},
            {"X-A": "{{PASSWORD}}"},
            {"X-A": "{{SUBJECT"},
            {"X-A": 5},
        ],
    )
    def test_unsafe_templates_rejected(self, templates) -> None:
        with pytest.raises(SettingsError):
            ConnectionForwardingSettings.parse(
                {"enabled": True, "mode": "template", "headerTemplates": templates}, strict=True
            )

    def test_unsafe_templates_dropped_at_runtime(self) -> None:
        s = ConnectionForwardingSettings.parse(
            {"mode": "template", "headerTemplates": {"Authorization": "x", "X-User": "{{SUBJECT}}"}}
        )
        assert [n for n, _ in s.header_templates] == ["X-User"]

    def test_template_mode_needs_templates_and_jwt_needs_audience(self) -> None:
        with pytest.raises(SettingsError):
            ConnectionForwardingSettings.parse({"enabled": True, "mode": "template"}, strict=True)
        with pytest.raises(SettingsError):
            ConnectionForwardingSettings.parse({"enabled": True, "mode": "signedJwt"}, strict=True)

    def test_api_key_is_redacted_and_not_in_repr(self) -> None:
        s = ConnectionForwardingSettings.parse({"enabled": True, "serviceApiKey": "sk-secret"})
        assert "sk-secret" not in repr(s)
        assert s.to_dict()["serviceApiKey"] == "***"
        assert s.to_dict(redact=False)["serviceApiKey"] == "sk-secret"

    def test_fingerprint_tracks_every_setting_without_leaking_the_key(self) -> None:
        base = ConnectionForwardingSettings.parse({"enabled": True})
        assert base.fingerprint == ConnectionForwardingSettings.parse({"enabled": True}).fingerprint
        others = [
            {"enabled": True, "preset": "portkey"},
            {"enabled": True, "serviceApiKey": "k1"},
            {"enabled": True, "serviceApiKey": "k2"},
            {"enabled": True, "mode": "signedJwt", "jwtAudience": "a"},
        ]
        prints = {ConnectionForwardingSettings.parse(o).fingerprint for o in others}
        assert base.fingerprint not in prints and len(prints) == len(others)
        assert "k1" not in ConnectionForwardingSettings.parse(others[1]).fingerprint


class TestSanitize:
    def test_strips_crlf_and_control_characters(self) -> None:
        assert clean_text("a\r\nX-Evil: 1\x00") == "aX-Evil: 1"

    def test_non_ascii_is_dropped(self) -> None:
        assert clean_text("jos\u00e9 \u202e") == "jos"

    def test_caps_length(self) -> None:
        assert len(clean_text("x" * 1000)) == 256

    def test_tag_commas_are_escaped(self) -> None:
        assert clean_tag_value("a,b") == "a%2Cb"

    def test_header_value_cap(self) -> None:
        assert len(clean_header_value("x" * 10000)) == 4096

    def test_none_is_empty(self) -> None:
        assert clean_text(None) == ""

    def test_join_capped_stops_before_the_limit(self) -> None:
        out = join_capped(["aaaa", "bbbb", "cccc"], max_length=9)
        assert out == "aaaa,bbbb"

    def test_header_name_rules(self) -> None:
        assert is_valid_header_name("x-litellm-end-user-id")
        assert not is_valid_header_name("bad name") and not is_valid_header_name("a:b")
        assert is_forbidden_header_name("Authorization") and is_forbidden_header_name("HOST")
        assert not is_forbidden_header_name("x-user")

    @pytest.mark.parametrize("junk", ["\r\n", "a\nb", "\x00\x01", "\u2028", "' OR 1=1 --\r\n"])
    def test_no_value_can_split_a_header(self, junk) -> None:
        for fn in (clean_text, clean_tag_value, clean_header_value):
            assert "\r" not in fn(junk) and "\n" not in fn(junk)
