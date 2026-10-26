"""The repo-instructions hook stays unwired (S-3).

It would put a committer's CLAUDE.md / AGENTS.md into the system prompt under
"Follow them". Wire it only once that text renders as untrusted data, is opt-in
per repo by an admin, and passes injection tests -- then change this test.
"""
import inspect
import re

from app.agents.agent_loop import factory


def test_the_factory_does_not_register_repo_instructions() -> None:
    live = [line for line in inspect.getsource(factory).splitlines()
            if "repo_instructions" in line and not line.lstrip().startswith("#")]
    assert not [line for line in live if re.search(r"repo_instructions_\w+\s*\(", line)], live
