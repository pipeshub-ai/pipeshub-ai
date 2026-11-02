"""indexing_model_identity: the model a role resolves to, as a fingerprint input."""

from app.utils.llm import indexing_model_identity


def test_a_role_assignment_wins() -> None:
    ai_models = {
        "modelRoles": {"indexing": {"modelType": "llm", "modelKey": "k-2"}},
        "llm": [{"modelKey": "k-1", "isDefault": True}, {"modelKey": "k-2", "configuration": {"model": "m2"}}],
    }
    assert indexing_model_identity(ai_models) == "llm:k-2:m2"


def test_an_assignment_that_does_not_resolve_is_the_default_as_at_runtime() -> None:
    """get_llm_for_role falls back to the default LLM; the fingerprint must follow it."""
    ai_models = {
        "modelRoles": {"indexing": {"modelType": "llm", "modelKey": "gone"}},
        "llm": [{"modelKey": "k-1", "isDefault": True, "configuration": {"model": "m1"}}],
    }
    assert indexing_model_identity(ai_models) == "llm:k-1:m1"


def test_editing_the_assigned_model_changes_the_identity() -> None:
    def with_model(model: str) -> dict[str, object]:
        return {
            "modelRoles": {"indexing": {"modelType": "llm", "modelKey": "k"}},
            "llm": [{"modelKey": "k", "configuration": {"model": model}}],
        }

    assert indexing_model_identity(with_model("gpt-4o")) != indexing_model_identity(with_model("gpt-4.1"))


def test_the_default_llm_is_next() -> None:
    ai_models = {"llm": [{"modelKey": "k-1"}, {"modelKey": "k-2", "isDefault": True, "configuration": {"model": "m"}}]}
    assert indexing_model_identity(ai_models) == "llm:k-2:m"


def test_then_the_first_llm() -> None:
    assert indexing_model_identity({"llm": [{"provider": "openAI", "configuration": {"model": "gpt"}}]}) == "llm:openAI:gpt"


def test_no_llm_is_none() -> None:
    assert indexing_model_identity({}) == "none"
    assert indexing_model_identity({"llm": []}) == "none"
    assert indexing_model_identity({"llm": ["garbage"]}) == "none"


def test_a_model_change_changes_the_identity() -> None:
    a = {"llm": [{"modelKey": "k", "isDefault": True, "configuration": {"model": "m1"}}]}
    b = {"llm": [{"modelKey": "k", "isDefault": True, "configuration": {"model": "m2"}}]}
    assert indexing_model_identity(a) != indexing_model_identity(b)
