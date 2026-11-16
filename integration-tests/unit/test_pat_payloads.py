"""Personal-access-token payloads in the suite must satisfy the server's schema.

The server (oauth_provider/validators/pat.validators.ts) accepts ``expiryDays``
of 7, 30, 90 or 365 only. A fixture that posts another value is
refused with 400, so every test using it errors before it checks anything.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {7, 30, 90, 365}
# "expiryDays": 1   expiryDays=1   expiry_days = 1   payload["expiryDays"] = 1   expiry_days: int = 1
# Any quoted value ("30", "never") is refused too: the server wants a number. A name is resolved to a number
# assigned to it in the same file (``DAYS = 1``), when there is one.
_VALUE = re.compile(
    r"""expiry_?days["'\]]*\s*(?::\s*[\w\[\]| ]+?)?\s*[:=]\s*(?P<v>["'][^"']*["']|-?\d+(?:\.\d+)?|[A-Za-z_]\w*)""",
    re.IGNORECASE,
)
_ASSIGN = re.compile(r"^\s*(?P<n>[A-Za-z_]\w*)\s*(?::\s*\w+\s*)?=\s*(?P<v>\d+)\s*$", re.MULTILINE)


def _bad_values(text: str) -> list[str]:
    constants = {m.group("n"): m.group("v") for m in _ASSIGN.finditer(text)}
    bad = []
    for match in _VALUE.finditer(text):
        raw = match.group("v")
        if raw[0] in "\"'":
            bad.append(raw)
            continue
        value = constants.get(raw) if raw[0].isalpha() or raw[0] == "_" else raw
        # "7.5" and "-1" are refused: only a whole number in ALLOWED passes.
        if value is not None and (not value.isdigit() or int(value) not in ALLOWED):
            bad.append(value)
    return bad


def test_the_guard_reads_every_spelling() -> None:
    for text in (
        '{"expiryDays": 1}',
        "dict(expiryDays=8)",
        'payload["expiryDays"] = "never"',
        '{"expiryDays": 7.5}',
        "dict(expiryDays=-1)",
        "body['expiryDays'] = 1",
        "expiry_days = 14",
        "def mint(expiry_days: int = 1): ...",
        '"expiryDays":1',
        '{"expiryDays": "30"}',
        "DAYS = 1\nbody = {'expiryDays': DAYS}",
    ):
        assert _bad_values(text), text
    for text in ("DAYS = 30\nbody = {'expiryDays': DAYS}", '{"expiryDays": 30}', "dict(expiryDays=7)", "expiry_days=365"):
        assert not _bad_values(text), text



def test_expiry_days_literals_are_allowed_values() -> None:
    bad = []
    for path in ROOT.rglob("*.py"):
        if path == Path(__file__).resolve() or "venv" in path.parts:
            continue
        for value in _bad_values(path.read_text(encoding="utf-8")):
            bad.append(f"{path.relative_to(ROOT)}: expiryDays={value}")
    assert not bad, f"expiryDays must be one of {sorted(ALLOWED)}: {bad}"
