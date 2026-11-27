"""An unquoted heredoc runs every backtick in it, comments included.

R38-IDENT: run.sh's env-file heredoc held the comment "so `rebuild` restarts it", which ran the ``rebuild`` function
with no argument at every stack start (``line 1210: $1: unbound variable``) and wrote the comment with the word
missing. The env file was otherwise right, so nothing failed; this keeps the next one from hiding the same way.
A ``$(...)`` is left alone: it is written on purpose (install.sh stamps its generated file's header with the date),
while backticks in these scripts' prose are always quoting a name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPTS = sorted(
    p for root in ("deployment", "scripts") for p in (REPO / root).rglob("*.sh") if "node_modules" not in p.parts
)
# `<<EOF` or `<<-EOF`, not `<<'EOF'` / `<<"EOF"` / `<<\EOF` (those are literal).
UNQUOTED = re.compile(r"<<-?\s*([A-Za-z_][A-Za-z0-9_]*)\b")


def unquoted_heredocs(text: str) -> list[tuple[int, list[str]]]:
    """Each unquoted heredoc's first line number and body lines."""
    lines = text.splitlines()
    found: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(lines):
        match = UNQUOTED.search(lines[i])
        if match is None or lines[i].lstrip().startswith("#"):
            i += 1
            continue
        tag, start, body = match.group(1), i + 1, []
        i += 1
        while i < len(lines) and lines[i].strip() != tag:
            body.append(lines[i])
            i += 1
        found.append((start + 1, body))
        i += 1
    return found


def offenders(text: str) -> list[str]:
    bad = []
    for start, body in unquoted_heredocs(text):
        for offset, line in enumerate(body):
            stripped = line.lstrip()
            if "`" in line and "\\`" not in line:
                bad.append(f"{start + offset}: backtick runs a command: {stripped[:80]}")
    return bad


def test_the_bug_shape_is_caught() -> None:
    script = "f() {\ncat >x <<EOF\n# so `rebuild` restarts it\nA=$(rand 3)\nEOF\n}\ncat <<'EOF'\n`literal`\nEOF\n"
    assert offenders(script) == ["3: backtick runs a command: # so `rebuild` restarts it"]
    assert offenders('cat <<"EOF"\n`x` $(y)\nEOF\ncat <<-EOF\n\tok=$(z)\n\tEOF\n') == []


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: str(p.relative_to(REPO)))
def test_no_unquoted_heredoc_runs_a_command_by_accident(script: Path) -> None:
    assert offenders(script.read_text(encoding="utf-8", errors="replace")) == []
