"""QA-CLASSIC-FU-5 (CJFIX-B): a deep run's sub-agent with no ``tools`` of its own is given the run's tools but the
coordination ones. Offered them, the scripted sub-agent planned, critiqued and then failed its own ``spawn_agent``
("could not find what the agent asked for"), and a job that finished Done listed a failed step."""

from app.harness.assembly.deep import (
    ROUTED_TOOLS,
    SPAWN_AGENT,
    define_sub_agent,
    sub_agent_pool,
)


def test_the_pool_keeps_the_runs_tools_and_drops_planning_and_spawning() -> None:
    names = ["jira", "create_plan", "search", "critique_plan", SPAWN_AGENT, "verify_result", "lookup_note"]
    assert sub_agent_pool(names) == ["jira", "search", "lookup_note"]
    assert not set(sub_agent_pool([*ROUTED_TOOLS, SPAWN_AGENT]))


def test_a_spawn_with_no_tools_gets_that_pool_and_one_naming_tools_gets_those() -> None:
    class Registry:
        def expand_tool_names(self, names: list[str]) -> list[str]:
            return list(names)

        def resolve_by_name(self, name: str) -> None:
            return None

    pool = sub_agent_pool(["search", "create_plan", SPAWN_AGENT, "lookup_note"])
    assert define_sub_agent(Registry(), pool, {"role": "general"}).tool_names == ("search", "lookup_note")  # type: ignore[arg-type]
    assert define_sub_agent(Registry(), pool, {"role": "general", "tools": ["search"]}).tool_names == ("search",)  # type: ignore[arg-type]
