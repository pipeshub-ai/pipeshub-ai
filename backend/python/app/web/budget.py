"""How many web searches and page reads one answer, or one long-running run, may use.

A chat answer gets a count sized for a reply. A coding engine run (``code_task``, a coder spawn) is long, multi-step
work that DEEPROUTE hands off precisely because it needs many sources: its spend is governed by the run's USD budget
(the gateway meters every model call that reads what the tools return), so its count is only a backstop against a
runaway loop, set high enough that a research task never meets it first (WP-62: a research child stopped at 20
pages with USD 4.66 of its USD 5 left). The defaults are documented in docs/agent-harness-v2/FLAGS.md.
"""

from dataclasses import dataclass
from typing import Literal

from app.web.errors import BudgetExhausted

# Enough for a careful answer; a cap on what one answer can cost and how long it can run.
STANDARD = (10, 20)
# Deep research reads widely before answering.
DEEP_RESEARCH = (24, 40)
# A long-running run (an engine child): a backstop, not the limiter; the run's USD budget is.
RUN = (150, 400)

Scope = Literal["answer", "run"]
# Where a run's tool state keeps its budget, for the web tools' adapter to say what is left.
WEB_BUDGET_KEY = "web_budget"


@dataclass
class WebBudget:
    searches: int
    pages: int
    scope: Scope = "answer"

    def __post_init__(self) -> None:
        self._searches_total = self.searches
        self._pages_total = self.pages

    @classmethod
    def for_answer(cls, *, deep_research: bool) -> "WebBudget":
        searches, pages = DEEP_RESEARCH if deep_research else STANDARD
        return cls(searches, pages)

    @classmethod
    def for_run(cls) -> "WebBudget":
        searches, pages = RUN
        return cls(searches, pages, scope="run")

    @classmethod
    def for_context(cls, *, scope: Scope, deep_research: bool) -> "WebBudget":
        return cls.for_run() if scope == "run" else cls.for_answer(deep_research=deep_research)

    def left(self) -> dict[str, int]:
        return {"searches": max(self.searches, 0), "pages": max(self.pages, 0)}

    def allowance_line(self) -> str | None:
        """The line a long run's web results end with; ``None`` for a chat answer, whose small count the prompt
        already sets out."""
        if self.scope != "run":
            return None
        return (
            f"[web allowance left for this task: {max(self.searches, 0)} searches, {max(self.pages, 0)} page reads]"
        )

    def take_search(self) -> None:
        if self.searches <= 0:
            raise BudgetExhausted(self._used_up("searches"), left=self.left(), hint=self._hint("searches"))
        self.searches -= 1

    def take_page(self) -> None:
        if self.pages <= 0:
            raise BudgetExhausted(self._used_up("pages"), left=self.left(), hint=self._hint("pages"))
        self.pages -= 1

    def _used_up(self, kind: Literal["searches", "pages"]) -> str:
        if self.scope == "answer":
            return (
                "The web searches for this answer are used up; answer with what you have found"
                if kind == "searches"
                else "The page reads for this answer are used up; answer with what you have read"
            )
        total = self._searches_total if kind == "searches" else self._pages_total
        what = "web searches" if kind == "searches" else "page reads"
        return f"This task's {what} are used up (all {total} of them)"

    def _hint(self, kind: Literal["searches", "pages"]) -> str:
        """What the model should do next, with what is left of the other kind: it is told, not stopped."""
        if kind == "searches":
            other = f"{max(self.pages, 0)} page reads are left: fetch pages you already found" if self.pages > 0 else (
                "no page reads are left either"
            )
        else:
            other = f"{max(self.searches, 0)} web searches are left" if self.searches > 0 else "no web searches are left"
        return (
            f"Do not call this tool again in this {'answer' if self.scope == 'answer' else 'task'}; {other}. Finish "
            "the work from what you have already gathered, and say which points you could not check."
        )
