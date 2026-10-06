"""Node's AI chat payload always carries aclVersion; both chat routes accept it."""

import pytest

from app.api.routes.agent import ChatQuery as AgentChatQuery
from app.api.routes.chatbot import ChatQuery as ChatbotChatQuery


@pytest.mark.parametrize("model", [ChatbotChatQuery, AgentChatQuery], ids=["chatbot", "agent"])
class TestAclVersionField:
    def test_present(self, model) -> None:
        assert model(query="q", aclVersion=0).aclVersion == 0
        assert model(query="q", aclVersion=7).aclVersion == 7

    def test_absent_means_do_not_cache(self, model) -> None:
        assert model(query="q").aclVersion is None
