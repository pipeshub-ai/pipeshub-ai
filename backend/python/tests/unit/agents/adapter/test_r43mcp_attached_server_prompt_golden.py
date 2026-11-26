"""R36LIVE-FU-2 golden: what the default chat's prompt says about an attached MCP server, byte for byte.

The two sections a model reads to decide where to look: "Finding Information" (the source list) and the toolset
listing under "Tools outside your tool list". The golden is the reviewed text; a change to either must be a change
someone read. Regenerate with ``PXH_REGEN_GOLDEN=1`` and review the diff.

Size: the fixture's whole prompt is held under a ceiling measured when this landed, so naming servers stays a few
lines, never a listing of their tools.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.unit.agents.adapter.test_prompt_invariants import build_prompt_for_fixture

GOLDEN = Path(__file__).parent / "goldens" / "r43mcp_attached_server_prompt.txt"
# Measured at landing: 16,086 chars with the two servers; 797 more than lazy_with_pinned (no server, a jira group).
# R43MCP-FU-4 named both ways in (`fetch_tools` on the server's toolset, or `search_tools`): +74, 16,160 and 871.
# R47LIVE-FU-1 tied a fitting server to the first step alongside knowledge: +158, 16,318 and 1,029.
PROMPT_CHAR_CEILING = 16_500
ADDED_CHARS_MAX = 1_100


def _sections(prompt: str) -> str:
    finding = "## Finding Information" + prompt.split("## Finding Information", 1)[1].split("\n## ", 1)[0]
    listing = "Available toolsets:" + prompt.split("Available toolsets:", 1)[1].split("\n## ", 1)[0]
    return f"{finding.rstrip()}\n\n{listing.rstrip()}\n"


def test_the_attached_server_sections_match_the_golden() -> None:
    text = _sections(build_prompt_for_fixture("lazy_with_attached_server"))
    if os.environ.get("PXH_REGEN_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text)
    assert text == GOLDEN.read_text()


def test_each_server_is_named_once_in_the_source_list_and_once_in_the_listing() -> None:
    prompt = build_prompt_for_fixture("lazy_with_attached_server")
    finding = prompt.split("## Finding Information", 1)[1].split("\n## ", 1)[0]
    assert "Attached MCP servers (e2esaas, Tracker)" in finding
    listing = prompt.split("Available toolsets:", 1)[1]
    assert "- `mcp_e2esaas` (2 tools): e2esaas — Tools: create ticket, lookup note." in listing
    assert "- `mcp_tracker` (tools listed when searched): Tracker — attached MCP server tools." in listing
    assert "lookup_note" not in finding, "the source list names servers, never their tools"


def test_the_source_line_names_both_ways_to_a_servers_tools() -> None:
    """R43MCP-FU-4: a real model given the server's name loads its toolset with ``fetch_tools`` (R43-LIVE 2/2); the
    line said only ``search_tools``, so prompt and behaviour disagreed. Both ways in, then ``call_tool``."""
    finding = build_prompt_for_fixture("lazy_with_attached_server").split("## Finding Information", 1)[1]
    item = next(line for line in finding.splitlines() if "Attached MCP servers" in line)
    assert "`fetch_tools` on its toolset" in item and "`search_tools`" in item and "`call_tool`" in item
    assert item.index("`fetch_tools`") < item.index("`search_tools`") < item.index("`call_tool`")


def _finding(fixture: str) -> str:
    return build_prompt_for_fixture(fixture).split("## Finding Information", 1)[1].split("\n## ", 1)[0]


def test_a_server_that_fits_is_checked_in_the_first_step_with_knowledge() -> None:
    """R47LIVE-FU-1: with knowledge listed first, a real model searched the person's collection 9 times before the
    server whose ``lookup_note`` held the note. The line ties the server to the first step by its own words (its
    description or tool names), not by topic, and never ranks it ahead of knowledge."""
    finding = _finding("lazy_with_attached_server")
    item = next(line for line in finding.splitlines() if "Attached MCP servers" in line)
    assert "own description or tool names fit the question" in item
    assert "first step alongside internal knowledge, not after knowledge comes up empty" in item
    numbered = [line for line in finding.splitlines() if line[:3] in ("1. ", "2. ")]
    assert numbered[0].startswith("1. Internal knowledge"), "knowledge stays the first source for what only it holds"


def test_a_knowledge_question_without_a_server_reads_as_before() -> None:
    """The first-step sentence exists only where a server is attached, so a knowledge-only agent's source list (what
    "what's our travel policy?" is answered from) is unchanged and names knowledge first."""
    finding = _finding("lazy_with_pinned")
    assert "first step alongside internal knowledge" not in finding
    assert "1. Internal knowledge" in finding


def test_the_prompt_stays_small() -> None:
    with_servers = build_prompt_for_fixture("lazy_with_attached_server")
    without = build_prompt_for_fixture("lazy_with_pinned")
    assert len(with_servers) < PROMPT_CHAR_CEILING, len(with_servers)
    # lazy_with_pinned carries a jira group the server fixture does not, so this bounds the servers' lines from above.
    assert len(with_servers) - len(without) < ADDED_CHARS_MAX, len(with_servers) - len(without)
