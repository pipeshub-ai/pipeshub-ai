import json
from pathlib import Path

from app.modules.agents import handles

REPO = Path(__file__).resolve().parents[6]
NODE = REPO / "backend/nodejs/apps/src/modules/enterprise_search/services/collaboration/mentions/reserved-aliases.json"
PYTHON = REPO / "backend/python/app/config/reserved_mention_aliases.json"


def test_python_copy_equals_the_node_file() -> None:
    assert json.loads(PYTHON.read_text()) == json.loads(NODE.read_text())


def test_handles_module_loads_that_copy() -> None:
    data = json.loads(PYTHON.read_text())
    assert set(handles.RESERVED) == set(data["assistant"]) | set(data["inert"])
    assert list(handles.ASSISTANT_ALIASES) == data["assistant"]


def test_the_help_card_reuses_the_same_aliases() -> None:
    from app.modules.agents.collaboration import help_card

    assert help_card.ASSISTANT_ALIASES is handles.ASSISTANT_ALIASES
