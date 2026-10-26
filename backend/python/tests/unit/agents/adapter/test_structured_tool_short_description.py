"""The prompt's tool list gets one line per per-request tool, not its whole docstring."""

from langchain_core.tools import StructuredTool

from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter

DOC = (
    "Fetch a Slack thread by its permalink and return every reply.\n\n"
    "Args:\n    url: the permalink\n\nReturns the replies in order."
)


def _tool(description: str) -> StructuredTool:
    return StructuredTool.from_function(lambda url="": url, name="fetch_thread", description=description)


def test_uses_the_first_paragraph_of_the_docstring() -> None:
    adapter = PipesHubStructuredToolAdapter(_tool(DOC), "slack", "fetch_thread")
    assert adapter.short_description == "Fetch a Slack thread by its permalink and return every reply."


def test_an_explicit_one_liner_wins() -> None:
    adapter = PipesHubStructuredToolAdapter(_tool(DOC), "slack", "fetch_thread", short_description="Read a thread.")
    assert adapter.short_description == "Read a thread."


def test_a_long_first_paragraph_is_capped() -> None:
    adapter = PipesHubStructuredToolAdapter(_tool("word " * 100), "slack", "fetch_thread")
    assert len(adapter.short_description) <= 160
    assert adapter.short_description.endswith("…")


def test_the_full_docstring_still_goes_to_the_schema() -> None:
    adapter = PipesHubStructuredToolAdapter(_tool(DOC), "slack", "fetch_thread")
    assert "Returns the replies in order." in adapter.description
