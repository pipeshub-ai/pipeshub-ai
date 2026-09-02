"""Tests for app.modules.agents.context.tool_names."""

from app.modules.agents.context.tool_names import (
    _split,
    granted,
    granted_any,
)


class TestSplit:
    def test_double_underscore(self):
        assert _split("retrieval__search_internal_knowledge") == (
            "retrieval", "search_internal_knowledge",
        )

    def test_dotted(self):
        assert _split("retrieval.search_internal_knowledge") == (
            "retrieval", "search_internal_knowledge",
        )

    def test_bare_name(self):
        assert _split("run_code") == ("", "run_code")

    def test_double_underscore_takes_priority_over_dot(self):
        assert _split("app__tool.name") == ("app", "tool.name")

    def test_empty_string(self):
        assert _split("") == ("", "")

    def test_multiple_double_underscores(self):
        app, tool = _split("a__b__c")
        assert app == "a"
        assert tool == "b__c"


class TestGranted:
    def test_exact_match(self):
        names = ["retrieval__search", "outlook__get_mail"]
        assert granted("retrieval__search", names) == "retrieval__search"

    def test_dotted_matches_double_underscore(self):
        names = ["retrieval__search"]
        assert granted("retrieval.search", names) == "retrieval__search"

    def test_double_underscore_matches_dotted(self):
        names = ["retrieval.search"]
        assert granted("retrieval__search", names) == "retrieval.search"

    def test_bare_matches_any_prefix(self):
        names = ["dynamic__web_search", "other__something"]
        assert granted("web_search", names) == "dynamic__web_search"

    def test_no_match_returns_none(self):
        names = ["retrieval__search"]
        assert granted("outlook__get_mail", names) is None

    def test_bare_no_match(self):
        names = ["retrieval__search"]
        assert granted("web_search", names) is None

    def test_empty_names(self):
        assert granted("anything", []) is None

    def test_app_prefix_mismatch(self):
        names = ["retrieval__search"]
        assert granted("outlook__search", names) is None

    def test_bare_exact_match_priority(self):
        names = ["run_code", "sandbox__run_code"]
        assert granted("run_code", names) == "run_code"


class TestGrantedAny:
    def test_multiple_matches(self):
        names = ["retrieval__search", "outlook__get_mail", "slack__send"]
        logicals = ["retrieval.search", "slack.send"]
        result = granted_any(logicals, names)
        assert result == ("retrieval__search", "slack__send")

    def test_unmatched_skipped(self):
        names = ["retrieval__search"]
        logicals = ["retrieval.search", "nonexistent.tool"]
        result = granted_any(logicals, names)
        assert result == ("retrieval__search",)

    def test_deduplicates(self):
        names = ["retrieval__search"]
        logicals = ["retrieval.search", "retrieval__search"]
        result = granted_any(logicals, names)
        assert result == ("retrieval__search",)
        assert len(result) == 1

    def test_empty_logicals(self):
        result = granted_any([], ["retrieval__search"])
        assert result == ()

    def test_preserves_order(self):
        names = ["a__x", "b__y", "c__z"]
        logicals = ["c.z", "a.x"]
        result = granted_any(logicals, names)
        assert result == ("c__z", "a__x")
