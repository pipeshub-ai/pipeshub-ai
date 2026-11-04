"""``UNSHOWN_MATCHES_LIST``: which matched records a result names, and how."""

from __future__ import annotations

import pytest

from app.modules.retrieval.context.unshown import (
    MAX_TITLE_CHARS,
    MAX_UNSHOWN_MATCHES,
    UNSHOWN_MATCHES_LIST_ENV_VAR,
    UnshownMatch,
    render_unshown_matches,
    unshown_matches,
    unshown_matches_enabled,
)


def _permitted(*vrids: str) -> dict[str, dict]:
    return {vrid: {"_key": f"r-{vrid}", "recordName": f"Doc {vrid}"} for vrid in vrids}


class TestUnshownMatches:
    def test_best_first_skipping_what_is_shown_or_held(self) -> None:
        found = unshown_matches(["a", "b", "c", "d"], {"b", "d"}, _permitted("a", "b", "c", "d"))

        assert found == [UnshownMatch("a", "r-a", "Doc a"), UnshownMatch("c", "r-c", "Doc c")]

    def test_a_record_whose_id_is_already_known_is_skipped(self) -> None:
        found = unshown_matches(["a", "b"], set(), _permitted("a", "b"), known_record_ids={"r-a"})

        assert [m.record_id for m in found] == ["r-b"]

    def test_a_record_the_permission_check_did_not_return_is_never_named(self) -> None:
        """The hits alone are not proof of access: only the retrieval service's
        permission-checked map names a record."""
        found = unshown_matches(["denied", "a"], set(), _permitted("a"))

        assert [m.virtual_record_id for m in found] == ["a"]

    def test_a_record_without_an_id_or_with_a_malformed_entry_is_skipped(self) -> None:
        permitted = {"no-id": {"recordName": "x"}, "bad": "not a record", **_permitted("a")}

        assert [m.record_id for m in unshown_matches(["no-id", "bad", "a"], set(), permitted)] == ["r-a"]

    def test_at_most_the_limit_and_each_record_once(self) -> None:
        vrids = [f"v{i}" for i in range(MAX_UNSHOWN_MATCHES + 5)]

        found = unshown_matches(["v0", *vrids], set(), _permitted(*vrids))

        assert [m.virtual_record_id for m in found] == vrids[:MAX_UNSHOWN_MATCHES]

    def test_a_long_title_is_cut_so_the_list_stays_small(self) -> None:
        """A record name can be a whole message or subject line; ten of them must
        not crowd the records out of the result."""
        found = unshown_matches(["a"], set(), {"a": {"_key": "r-a", "recordName": "x" * 5000}})

        assert len(found[0].name) == MAX_TITLE_CHARS
        assert found[0].name.endswith("…")
        assert len(render_unshown_matches(found * MAX_UNSHOWN_MATCHES)) < 3_000

    def test_graph_ids_and_snake_case_names_are_read_too(self) -> None:
        found = unshown_matches(["a"], set(), {"a": {"id": "r-a", "record_name": "Runbook"}})

        assert found == [UnshownMatch("a", "r-a", "Runbook")]


class TestRenderUnshownMatches:
    def test_lists_each_record_by_id_and_name_with_the_fetch_tool(self) -> None:
        text = render_unshown_matches([UnshownMatch("a", "r1", "Runbook"), UnshownMatch("b", "r2", "")])

        assert text.startswith("\n\nAlso matched, not shown")
        assert "`knowledgegraph__fetch_record`" in text
        assert text.endswith("- Record ID: r1 | Runbook\n- Record ID: r2 | (unnamed)")

    def test_nothing_to_list_renders_nothing(self) -> None:
        assert render_unshown_matches([]) == ""


class TestSwitch:
    @pytest.mark.parametrize(("mode", "own", "expected"), [
        (None, None, False),
        ("true", None, True),
        ("true", "", True),
        ("true", "false", False),
        (None, "true", True),
        ("false", "TRUE", True),
    ])
    def test_follows_high_accuracy_mode_unless_set(
        self, monkeypatch: pytest.MonkeyPatch, mode: str | None, own: str | None, expected: bool,
    ) -> None:
        for name, value in (("HIGH_ACCURACY_MODE", mode), (UNSHOWN_MATCHES_LIST_ENV_VAR, own)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

        assert unshown_matches_enabled() is expected
