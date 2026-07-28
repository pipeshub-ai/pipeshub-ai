"""`modes.py` — the declarative chat-mode catalog `router.py` and
`factory.py` both key off (see that module's docstring for the full
name -> (loop_kind, compose_domain_agents) rationale). Covers catalog
resolution (canonical names, legacy aliases, unknown/auto -> None) and
that every catalog entry's flags match the "Chat Mode Redesign" plan."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.modes import MODE_CATALOG, ModeDefinition, resolve_mode


class TestResolveMode:
    @pytest.mark.parametrize(
        ("chat_mode", "expected_name"),
        [
            ("quick", "quick"),
            ("react", "react"),
            ("planExecute", "planExecute"),
            ("deep", "deep"),
        ],
    )
    def test_resolves_canonical_names(self, chat_mode: str, expected_name: str) -> None:
        mode = resolve_mode(chat_mode)
        assert mode is not None
        assert mode.name == expected_name

    def test_resolves_legacy_verification_alias_to_plan_execute(self) -> None:
        mode = resolve_mode("verification")
        assert mode is not None
        assert mode.name == "planExecute"
        assert mode.loop_kind == "plan_execute"

    @pytest.mark.parametrize("chat_mode", ["QUICK", "Deep", "  react  ", "PlanExecute", "VERIFICATION"])
    def test_case_and_whitespace_insensitive(self, chat_mode: str) -> None:
        assert resolve_mode(chat_mode) is not None

    @pytest.mark.parametrize("chat_mode", ["auto", "", None, "not-a-real-mode"])
    def test_unrecognized_or_auto_returns_none(self, chat_mode: str | None) -> None:
        assert resolve_mode(chat_mode) is None

    def test_alias_and_canonical_name_resolve_to_the_same_object(self) -> None:
        assert resolve_mode("planExecute") is resolve_mode("verification")


class TestModeCatalogContents:
    """Pins each mode's `loop_kind`/`compose_domain_agents` to the redesign
    plan's mapping so a future one-line catalog edit is a deliberate,
    visible test change rather than a silent behavior drift."""

    def _get(self, name: str) -> ModeDefinition:
        mode = resolve_mode(name)
        assert mode is not None
        return mode

    def test_quick_is_flat_react_no_domain_agents(self) -> None:
        mode = self._get("quick")
        assert mode.loop_kind == "react"
        assert mode.compose_domain_agents is False

    def test_quick_skips_intent_understanding(self) -> None:
        mode = self._get("quick")
        assert mode.skip_intent is True

    def test_react_is_react_with_domain_agents(self) -> None:
        mode = self._get("react")
        assert mode.loop_kind == "react"
        assert mode.compose_domain_agents is True
        assert mode.skip_intent is False

    def test_plan_execute_is_plan_execute_with_domain_agents(self) -> None:
        mode = self._get("planExecute")
        assert mode.loop_kind == "plan_execute"
        assert mode.compose_domain_agents is True
        assert mode.skip_intent is False

    def test_deep_is_orchestrator_with_domain_agents(self) -> None:
        mode = self._get("deep")
        assert mode.loop_kind == "orchestrator"
        assert mode.compose_domain_agents is True
        assert mode.skip_intent is False

    def test_catalog_has_exactly_four_modes_with_unique_names(self) -> None:
        names = [m.name for m in MODE_CATALOG]
        assert len(names) == len(set(names)) == 4

    def test_mode_definition_is_frozen(self) -> None:
        mode = self._get("quick")
        with pytest.raises(AttributeError):
            mode.loop_kind = "orchestrator"  # type: ignore[misc]
