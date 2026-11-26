"""``ScriptedWrapUp``: an engine run's wrap-up (ENGBUDGET) on a scripted model, for tests and lanes (WP-70).

It builds the real prompt (``wrap_up_prompt``) and answers as a model following ``WRAP_UP_SYSTEM`` would: a Markdown
report whose findings cite, as links, the URLs the prompt's tool results hold. No model is called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.engines.durable.wrap_up import wrap_up_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.engines.durable.ports import GatheredResult
    from harness_core.kernel.core.turn_limit import CutShort

_URL = re.compile(r"https?://[^\s)\"'<>\]]+")
RESULTS_HEADING = "Tool results (oldest first):"


@dataclass
class ScriptedWrapUp:
    prompts: list[str] = field(default_factory=list[str])
    gathered: list[tuple[GatheredResult, ...]] = field(default_factory=list[tuple["GatheredResult", ...]])
    input_tokens: int = 24_000

    async def hold(self) -> bool:
        return True

    async def answer(
        self, request: str, gathered: Sequence[GatheredResult], reason: CutShort, *, earlier: str | None = None
    ) -> str | None:
        del reason
        prompt = wrap_up_prompt(request, gathered, input_tokens=self.input_tokens, earlier=earlier)
        self.prompts.append(prompt)
        self.gathered.append(tuple(gathered))
        # Only what the results hold: a scripted request names pages too (its engine's directives).
        results = prompt.split(RESULTS_HEADING, 1)[-1]
        urls = list(dict.fromkeys(u.rstrip(".,;") for u in _URL.findall(results)))
        if not urls:
            return None
        findings = "\n".join(f"- Finding {n} ([source]({u}))" for n, u in enumerate(urls, start=1))
        return (
            f"# Report\n\n## Summary\n\nWhat the sources show ([source]({urls[0]})).\n\n## Findings\n\n{findings}\n\n"
            "## Still to do\n\n- Sources not reached before the limit."
        )

    async def release(self) -> None:
        return None


__all__ = ["RESULTS_HEADING", "ScriptedWrapUp"]
