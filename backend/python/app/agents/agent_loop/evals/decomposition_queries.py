"""~20-query dataset for deep mode's decomposition-quality regression
harness (`decomposition_harness.py`). Each query pins down what a GOOD
`create_plan` call should look like for it — not the model's exact
wording, which will always vary, but structural invariants
`decomposition_scorer.py` can check mechanically: how many steps, which
domains should be touched, whether a dependency between steps is expected.

Deliberately spans both failure modes the Phase 1 planning instructions
(`orchestrator.py::build_phase1_planning_instructions`) explicitly warn
against: under-decomposition (one step covering a goal that actually
spans independent domains) and over-decomposition (manufacturing multiple
steps for a goal one step — or zero, answered directly — would cover).
A regression here means either that prompt drifted, or the underlying
model's planning behavior did.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecompositionEvalQuery:
    """One eval case. `expected_domains` is intentionally loose (see
    `decomposition_scorer.py`'s keyword matching) — the model is never
    forced to spell a domain name exactly like `DomainAgentDefinition.
    name`, only to touch that AREA of work somewhere in the plan."""

    id: str
    query: str
    min_steps: int
    max_steps: int
    expected_domains: frozenset[str] = frozenset()
    """Domain keywords (see `decomposition_scorer.DOMAIN_KEYWORDS`) at
    least one step's `domain`/`tool_names`/`description` should evidence."""
    requires_dependency: bool = False
    """True when the goal has a genuine data dependency between two of its
    parts (e.g. "calculate X using Y fetched from Z") — a good plan MUST
    use `depends_on` for it, not just happen to list steps in a sensible
    order."""
    notes: str = ""


DECOMPOSITION_EVAL_QUERIES: tuple[DecompositionEvalQuery, ...] = (
    DecompositionEvalQuery(
        id="simple_calculation",
        query="What's 15% of 2,400,000?",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"calculator"}),
        notes="Single trivial calculation — must not be split into multiple steps.",
    ),
    DecompositionEvalQuery(
        id="simple_internal_lookup",
        query="What is our company's PTO policy?",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"internal"}),
        notes="Single-domain internal-knowledge lookup, no dependent step.",
    ),
    DecompositionEvalQuery(
        id="simple_web_lookup",
        query="Summarize the latest announcement from OpenAI about GPT-5 from the web.",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"web"}),
        notes="Single-domain web research — must not fan out into several web steps.",
    ),
    DecompositionEvalQuery(
        id="simple_calendar_lookup",
        query="Am I free next Tuesday afternoon?",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"calendar"}),
    ),
    DecompositionEvalQuery(
        id="jira_table_single_domain",
        query="Find all open Jira tickets assigned to me and summarize them in a table with columns Ticket, Status, Priority.",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"internal"}),
        notes="Single internal-knowledge domain even though it names a specific output_format.",
    ),
    DecompositionEvalQuery(
        id="web_plus_internal_plus_file",
        query=(
            "Research our top 3 competitors' current pricing on the web, cross-reference it "
            "against our own internal pricing document, and produce a CSV file comparing all four."
        ),
        min_steps=2, max_steps=3,
        expected_domains=frozenset({"web", "internal", "coding"}),
        requires_dependency=True,
        notes="File-generation step must depend on both the web-research and internal-lookup steps.",
    ),
    DecompositionEvalQuery(
        id="revenue_growth_calc",
        query="Find our Q3 revenue figures internally and calculate the year-over-year growth rate from them.",
        min_steps=2, max_steps=2,
        expected_domains=frozenset({"internal", "calculator"}),
        requires_dependency=True,
        notes="The calculation step needs the fetched figures — must depend_on the fetch step.",
    ),
    DecompositionEvalQuery(
        id="calendar_and_internal_independent",
        query=(
            "Check my calendar for open slots this week, and separately look up the attendee "
            "list for the 'Q3 Planning' meeting from our internal records."
        ),
        min_steps=1, max_steps=2,
        expected_domains=frozenset({"calendar", "internal"}),
        notes="Two genuinely independent asks — no dependency expected between them.",
    ),
    DecompositionEvalQuery(
        id="confluence_jira_pdf_summary",
        query=(
            "Pull all Confluence pages about our onboarding process and all Jira tickets tagged "
            "'onboarding', then write a combined summary as a PDF document."
        ),
        min_steps=2, max_steps=3,
        expected_domains=frozenset({"internal", "coding"}),
        requires_dependency=True,
        notes="PDF-generation step depends on the internal-knowledge fetch step.",
    ),
    DecompositionEvalQuery(
        id="news_plus_internal_guidance",
        query=(
            "What's the latest news on the Fed's interest rate decision, and how might that "
            "affect the Q4 financial guidance in our internal board deck?"
        ),
        min_steps=1, max_steps=3,
        expected_domains=frozenset({"web", "internal"}),
        notes="Two source domains that both feed one synthesis — over-decomposing into 4+ steps is a smell.",
    ),
    DecompositionEvalQuery(
        id="calendar_list_tomorrow",
        query="List every meeting on my calendar tomorrow.",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"calendar"}),
    ),
    DecompositionEvalQuery(
        id="business_days_with_holidays",
        query=(
            "Calculate how many business days are between March 1 and April 15, excluding any "
            "company holidays listed on our internal HR page."
        ),
        min_steps=2, max_steps=2,
        expected_domains=frozenset({"internal", "calculator"}),
        requires_dependency=True,
        notes="The date calculation needs the holiday list first — depends_on required.",
    ),
    DecompositionEvalQuery(
        id="runbook_and_freeze_check",
        query=(
            "Find our internal engineering runbook for deploying the payments service, and check "
            "whether today is a company-designated no-deploy freeze day on the calendar."
        ),
        min_steps=1, max_steps=2,
        expected_domains=frozenset({"internal", "calendar"}),
        notes="Two independent lookups feeding one answer — no dependency expected.",
    ),
    DecompositionEvalQuery(
        id="competitor_research_spreadsheet",
        query="Research 5 competitor products for AI coding assistants on the web and generate a comparison spreadsheet.",
        min_steps=2, max_steps=2,
        expected_domains=frozenset({"web", "coding"}),
        requires_dependency=True,
        notes="Spreadsheet-generation step depends on the web-research step's findings.",
    ),
    DecompositionEvalQuery(
        id="unrelated_trivial_pair",
        query="What's 37 times 289, and separately, what's the capital of France?",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"calculator"}),
        notes=(
            "One trivial calculation plus one fact the model can answer directly with no tool — "
            "must not manufacture a second step or domain for the general-knowledge half."
        ),
    ),
    DecompositionEvalQuery(
        id="fiscal_year_end_days",
        query="How many days until the end of the fiscal year?",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"calculator"}),
    ),
    DecompositionEvalQuery(
        id="stock_price_vs_board_deck",
        query=(
            "Search the web for the current stock price of our main competitor and compare it to "
            "the figures in our last internal board deck."
        ),
        min_steps=1, max_steps=3,
        expected_domains=frozenset({"web", "internal"}),
    ),
    DecompositionEvalQuery(
        id="jira_overdue_csv_chain",
        query=(
            "Fetch the list of open tasks for the 'Atlas' project from Jira, calculate how many are "
            "overdue based on today's date, and export the result as a CSV file."
        ),
        min_steps=3, max_steps=3,
        expected_domains=frozenset({"internal", "calculator", "coding"}),
        requires_dependency=True,
        notes="Three-step dependency chain: fetch -> calculate (depends on fetch) -> export (depends on calculate).",
    ),
    DecompositionEvalQuery(
        id="holiday_plus_calendar_independent",
        query="Is there a US public holiday next Monday, and do I have anything on my calendar that day?",
        min_steps=1, max_steps=2,
        expected_domains=frozenset({"web", "calendar"}),
        notes="Two independent lookups — no dependency required between them.",
    ),
    DecompositionEvalQuery(
        id="internal_only_no_external",
        query="Summarize this internal wiki page for me — do not use any external web tools.",
        min_steps=0, max_steps=1,
        expected_domains=frozenset({"internal"}),
        notes="Explicitly single-domain — a plan that reaches for web_agent here is simply wrong.",
    ),
)


def query_by_id(query_id: str) -> DecompositionEvalQuery:
    """Lookup by id — used by tests/harness callers that want ONE query
    instead of the full dataset. Raises `KeyError` (with the full id list
    in the message) on a typo instead of returning `None`."""
    by_id = {q.id: q for q in DECOMPOSITION_EVAL_QUERIES}
    if query_id not in by_id:
        raise KeyError(f"No eval query with id {query_id!r}. Known ids: {sorted(by_id)}")
    return by_id[query_id]


__all__ = ["DECOMPOSITION_EVAL_QUERIES", "DecompositionEvalQuery", "query_by_id"]
