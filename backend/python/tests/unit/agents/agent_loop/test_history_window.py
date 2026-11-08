"""`history_window.py`: which earlier exchanges a run replays, and how (HG-01)."""

from __future__ import annotations

from app.agents.agent_loop.history_window import (
    REDUCED_ANSWER_CHARS,
    REDUCED_QUESTION_CHARS,
    attachment_stub,
    exchange_tokens,
    plan_history,
    reduce_exchange,
    split_exchanges,
)


def _opens(row: dict) -> bool:
    return row.get("role") == "user_query" and not str(row.get("content", "")).startswith("User selections:")


def _exchange(t: int, answer_chars: int = 2_000) -> list[dict]:
    return [
        {"role": "user_query", "content": f"Q{t}"},
        {"role": "bot_response", "content": f"A{t} " + "x" * answer_chars,
         "tool_results": [{"tool_name": "search", "tool_id": f"c{t}", "result": "r" * 4_000, "args": {}}]},
    ]


class TestSplit:
    def test_a_card_answer_stays_with_the_question_it_answers(self) -> None:
        rows = [
            {"role": "user_query", "content": "Book a room"},
            {"role": "bot_response", "content": "Which floor?"},
            {"role": "user_query", "content": "User selections:\n1. \"Which floor?\" → 3"},
            {"role": "bot_response", "content": "Booked."},
            {"role": "user_query", "content": "Thanks"},
        ]

        assert [len(e) for e in split_exchanges(rows, _opens)] == [4, 1]


class TestReduce:
    def test_keeps_question_answer_opening_and_tool_names_only(self) -> None:
        exchange = [
            {"role": "user_query", "content": "q" * 5_000, "attachments": [{"recordName": "a.pdf"}]},
            {"role": "bot_response", "content": "a" * 5_000, "status": "stopped",
             "tool_results": [{"tool_name": "search", "result": "r" * 9_000}, {"tool_name": "fetch"}]},
        ]

        question, answer = reduce_exchange(exchange)

        assert len(question["content"]) <= REDUCED_QUESTION_CHARS + 10
        assert question["attachments"] == [{"recordName": "a.pdf"}]
        assert "tool_results" not in answer and answer["status"] == "stopped"
        assert answer["content"].startswith("a" * REDUCED_ANSWER_CHARS)
        assert answer["content"].endswith("(Tools used: search, fetch.)")
        assert exchange_tokens([question, answer]) < exchange_tokens(exchange) // 5


class TestPlan:
    def test_newest_whole_then_reduced_then_omitted(self) -> None:
        exchanges = [_exchange(t) for t in range(100)]
        budget = 10 * exchange_tokens(exchanges[0])

        plan = plan_history(exchanges, budget)

        assert plan.tokens <= budget
        assert max(plan.kept) == 99 and 99 in plan.whole
        whole = sorted(plan.whole)
        assert whole == list(range(whole[0], 100)), "whole exchanges are the newest, contiguous"
        assert plan.reduced > 0 and plan.omitted > 0
        assert plan.kept == tuple(range(plan.omitted, 100))

    def test_attachment_tokens_count_only_for_whole_exchanges(self) -> None:
        exchanges = [_exchange(t) for t in range(3)]
        budget = sum(exchange_tokens(e) for e in exchanges)

        assert plan_history(exchanges, budget).whole == frozenset({0, 1, 2})
        plan = plan_history(exchanges, budget, [0, 0, 10_000])
        assert 2 not in plan.whole and plan.omitted == 0

    def test_same_input_same_plan(self) -> None:
        exchanges = [_exchange(t, answer_chars=t * 37) for t in range(50)]

        assert plan_history(exchanges, 5_000) == plan_history(exchanges, 5_000)


def test_the_stub_names_the_file_and_how_to_read_it() -> None:
    stub = attachment_stub(["handbook.pdf"])

    assert "handbook.pdf" in stub and "knowledgegraph__fetch_record" in stub
