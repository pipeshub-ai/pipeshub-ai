"""PR-7.3: no Python code path writes chat-content READER edges any more (7.4 deletes the old ones)."""

import re
from pathlib import Path

APP = Path(__file__).parents[4] / "app"


def test_chatbot_routes_write_no_permission_edges() -> None:
    text = (APP / "api/routes/chatbot.py").read_text()
    assert "batch_delete_edges" not in text
    assert "/permissions" not in text
    # The only READER edge left is the service-account upload's org edge, built by one helper.
    assert re.findall(r'"role":\s*"READER"', text) == []
    assert "service_account_upload_permission_edges(" in text
    helper = (APP / "utils/record_access.py").read_text()
    assert 'SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE = "ORGANIZATION"' in helper
    assert "CollectionNames.ORGS.value" in helper


def test_no_module_registers_a_chat_permissions_route() -> None:
    pattern = re.compile(r"chat/(attachments|artifacts)/permissions")
    hits = [str(p) for p in APP.rglob("*.py") if pattern.search(p.read_text())]
    assert hits == []
