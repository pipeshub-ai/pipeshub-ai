"""edition_config serves the per-service routers on first attribute access (T6)."""
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


def _run(code: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n{code}"],
        cwd=BACKEND, capture_output=True, text=True, timeout=250,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout.strip().splitlines()[-1]


@pytest.mark.slow
@pytest.mark.import_runtime
@pytest.mark.timeout(300)
def test_importing_edition_config_does_not_load_the_chat_agent_or_entity_routes() -> None:
    out = _run(
        "import app.edition_config as c\n"
        "print(sorted(m for m in ('app.api.routes.chatbot','app.api.routes.agent','app.api.routes.entity') if m in sys.modules))"
    )
    assert out == "[]"


@pytest.mark.slow
@pytest.mark.import_runtime
@pytest.mark.timeout(300)
def test_the_routers_are_still_importable_from_edition_config() -> None:
    out = _run(
        "from app.edition_config import chatbot_router, agent_router, entity_router\n"
        "import app.edition_config as c\n"
        "print(all(hasattr(r, 'routes') for r in (chatbot_router, agent_router, entity_router)), "
        "chatbot_router is c.chatbot_router)"
    )
    assert out == "True True"


def test_an_unknown_attribute_still_raises() -> None:
    import app.edition_config as c

    with pytest.raises(AttributeError):
        c.no_such_router  # noqa: B018
