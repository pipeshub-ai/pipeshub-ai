"""Decision models: typed classification calls (yes/no, pick one, score) with calibrated probabilities.

Usage::

    from app.services.ai_models.decision import ChoiceQuestion, DecisionOption
    from app.utils.llm import get_decision_model_instance

    built = await get_decision_model_instance(config_service)
    if built is None:
        ...  # no decision model configured
    model, _config = built
    result = await model.decide(
        "I was charged twice for my order.",
        [ChoiceQuestion(name="team", instructions="Which team should handle this?", options=[
            DecisionOption(value="billing", description="Payments and refunds"),
            DecisionOption(value="technical", description="Problems using the product"),
        ])],
    )
    result.answers["team"].choice
"""

from app.services.ai_models.decision.clients import (
    AzureOpenAIDecisionModel,
    DecisionModel,
    OpenAIDecisionModel,
    TypeSafeDecisionModel,
)
from app.services.ai_models.decision.factory import DecisionProvider, get_decision_model
from app.services.ai_models.decision.types import (
    ChoiceQuestion,
    DecisionAnswer,
    DecisionInput,
    DecisionModelError,
    DecisionOption,
    DecisionQuestion,
    DecisionResult,
    DecisionUsage,
    PredicateQuestion,
    ScoreLevel,
    ScoreQuestion,
)

__all__ = [
    "AzureOpenAIDecisionModel",
    "ChoiceQuestion",
    "DecisionAnswer",
    "DecisionInput",
    "DecisionModel",
    "DecisionModelError",
    "DecisionOption",
    "DecisionProvider",
    "DecisionQuestion",
    "DecisionResult",
    "DecisionUsage",
    "OpenAIDecisionModel",
    "PredicateQuestion",
    "ScoreLevel",
    "ScoreQuestion",
    "TypeSafeDecisionModel",
    "get_decision_model",
]
