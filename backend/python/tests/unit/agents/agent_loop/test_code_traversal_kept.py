"""L3 clearing keeps the code-graph walks an analysis builds on across turns."""
from app.agents.agent_loop.factory import _CODE_TRAVERSAL_TOOLS


def test_a_diff_walk_is_kept_like_a_symbol_walk() -> None:
    """impact_of_diff returns the changed symbols' addresses and their impact walks: the next turns read and
    follow them, as they do impact's."""
    assert {"codegraph__impact", "codegraph__impact_of_diff"} <= _CODE_TRAVERSAL_TOOLS
