from app.agents.agent_loop.context import AgentContext


class TestSendUserInfoFromChatState:
    def test_absent_defaults_to_true(self) -> None:
        context = AgentContext.from_chat_state({})
        assert context.send_user_info is True
        assert context.tool_state["send_user_info"] is True

    def test_false_is_threaded_through(self) -> None:
        context = AgentContext.from_chat_state({"send_user_info": False})
        assert context.send_user_info is False
        assert context.tool_state["send_user_info"] is False
