"""How many web searches and page reads one answer may use."""

from dataclasses import dataclass

from app.web.errors import BudgetExhausted

# Enough for a careful answer; a cap on what one answer can cost and how long it can run.
STANDARD = (10, 20)
# Deep research reads widely before answering.
DEEP_RESEARCH = (24, 40)


@dataclass
class WebBudget:
    searches: int
    pages: int

    @classmethod
    def for_answer(cls, *, deep_research: bool) -> "WebBudget":
        searches, pages = DEEP_RESEARCH if deep_research else STANDARD
        return cls(searches, pages)

    def take_search(self) -> None:
        if self.searches <= 0:
            raise BudgetExhausted("The web searches for this answer are used up; answer with what you have found")
        self.searches -= 1

    def take_page(self) -> None:
        if self.pages <= 0:
            raise BudgetExhausted("The page reads for this answer are used up; answer with what you have read")
        self.pages -= 1
