from app.agents.agent_loop.source_ids import (
    MAX_SOURCE_RECORD_IDS,
    OPAQUE_SOURCE,
    source_record_ids,
)


def test_nothing_retrieved_is_an_empty_list() -> None:
    assert source_record_ids({}) == []
    assert source_record_ids({"all_tool_results": [{"tool_name": "final_answer"}]}) == []


def test_prefetch_and_tool_records_count_cited_or_not() -> None:
    state = {
        "virtual_record_id_to_result": {"v1": {"id": "r1"}, "v2": {"metadata": {"recordId": "r2"}}},
        "tool_records": [{"_key": "r3"}],
        "final_results": [{"virtual_record_id": "v1"}, {"metadata": {"recordId": "r4"}}],
        "known_record_ids": {"r5"},
        "all_tool_results": [{"tool_name": "retrieval__search_internal_knowledge"}, {"tool_name": "knowledgehub.list_files"}],
    }
    assert source_record_ids(state) == ["r1", "r2", "r3", "r4", "r5"]


def test_output_that_has_no_record_ids_is_opaque() -> None:
    for state in (
        {"all_tool_results": [{"tool_name": "jira__search_issues"}]},
        {"all_tool_results": [{"tool_name": "internal_exploration_agent"}]},
        {"web_records": [{"url": "https://x"}]},
    ):
        assert source_record_ids(state)[-1] == OPAQUE_SOURCE


def test_too_many_ids_fail_closed() -> None:
    state = {"known_record_ids": {f"r{i}" for i in range(MAX_SOURCE_RECORD_IDS + 5)}}
    ids = source_record_ids(state)
    assert len(ids) == MAX_SOURCE_RECORD_IDS + 1 and ids[-1] == OPAQUE_SOURCE
