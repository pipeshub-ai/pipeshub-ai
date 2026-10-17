"""No file under backend/python carries a leftover merge-conflict marker.

One sat inside a docstring on main for days: valid Python, so nothing failed.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# The outer lines and diff3's base line; a bare row of "=" is also a docstring underline.
# Seven or more: git merge-file --marker-size writes longer runs.
_MARKER = re.compile(r"^(<{7,} |\|{7,} |>{7,} )", re.MULTILINE)
_SOURCES = ("app", "tests", "scripts")


def test_no_file_holds_a_conflict_marker() -> None:
    found = [
        str(path.relative_to(_ROOT))
        for source in _SOURCES
        for path in (_ROOT / source).rglob("*.py")
        if _MARKER.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert found == [], f"merge-conflict markers left in: {found}"
