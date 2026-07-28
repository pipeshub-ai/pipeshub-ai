from app.modules.agents.context.user_context import _format_user_context


def test_format_user_context_handles_none_org_and_user_info() -> None:
    """org_info/user_info can be explicitly None in state, not just absent."""
    state = {
        "user_email": "jane@example.com",
        "user_info": None,
        "org_info": None,
    }

    result = _format_user_context(state)

    assert "jane@example.com" in result
    assert "Account Type" not in result


def test_format_user_context_includes_account_type_when_present() -> None:
    state = {
        "user_email": "jane@example.com",
        "user_info": {"fullName": "Jane Doe"},
        "org_info": {"accountType": "business"},
    }

    result = _format_user_context(state)

    assert "Jane Doe" in result
    assert "**Account Type**: business" in result


def test_format_user_context_empty_when_no_identity() -> None:
    assert _format_user_context({}) == ""
