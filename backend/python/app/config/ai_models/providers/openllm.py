"""OpenLLM provider registration.

OpenLLM is a local-first gateway that routes API keys, OAuth subscription
accounts and local models through one OpenAI-compatible endpoint. Model ids
keep the ``<provider>/<model>`` form, and the aliases ``ultra``, ``plus`` and
``lite`` may be present. Docs: https://docs.openllm.sh

PipesHub usually runs in Docker, so the default endpoint is the daemon on the
host. The local daemon needs no key; the cloud endpoint takes an ``sk-llm-`` key.
Chat only: OpenLLM embeddings need a provider key behind the gateway.
"""

from app.config.ai_models.registry import AIModelProviderBuilder
from app.config.ai_models.types import AIModelField, ModelCapability
from app.config.constants.ai_models import OPENLLM_DEFAULT_ENDPOINT

from .common_fields import CONTEXT_LENGTH, FRIENDLY_NAME, IS_MULTIMODAL_LLM, model_field

_OPENLLM_ENDPOINT = AIModelField(
    name="endpoint",
    display_name="Endpoint URL",
    field_type="URL",
    required=True,
    default_value=OPENLLM_DEFAULT_ENDPOINT,
    placeholder=OPENLLM_DEFAULT_ENDPOINT,
    description=(
        "The OpenLLM daemon on the machine running Docker. "
        "Setup and cloud keys: https://docs.openllm.sh"
    ),
    examples=[
        {"label": "Daemon on the Docker host", "value": OPENLLM_DEFAULT_ENDPOINT},
        {"label": "Daemon on the same machine, no Docker", "value": "http://127.0.0.1:8787/v1"},
        {"label": "OpenLLM cloud (needs an API key)", "value": "https://www.openllm.sh/v1"},
    ],
)

_OPENLLM_API_KEY = AIModelField(
    name="apiKey",
    display_name="API Key (Optional)",
    field_type="PASSWORD",
    required=False,
    placeholder="sk-llm-...",
    description="Only needed for the cloud endpoint. Leave empty for the local daemon.",
    is_secret=True,
)

# Off by default: a reasoning model is sent reasoning_effort, and not every model
# behind the gateway accepts every level. Discovery turns it on per model.
_OPENLLM_IS_REASONING = AIModelField(
    name="isReasoning",
    display_name="Reasoning",
    field_type="BOOLEAN",
    required=False,
    default_value=False,
    description="Supports chain-of-thought reasoning",
)


@AIModelProviderBuilder("OpenLLM", "openLLM") \
    .with_description("Your API keys, subscriptions and local models through one endpoint") \
    .with_capabilities([ModelCapability.TEXT_GENERATION]) \
    .with_icon("/icons/ai-models/openllm.svg") \
    .with_color("#1A3329") \
    .add_field(_OPENLLM_ENDPOINT, ModelCapability.TEXT_GENERATION) \
    .add_field(_OPENLLM_API_KEY, ModelCapability.TEXT_GENERATION) \
    .add_field(model_field("e.g., ultra, lite"), ModelCapability.TEXT_GENERATION) \
    .add_field(FRIENDLY_NAME, ModelCapability.TEXT_GENERATION) \
    .add_field(CONTEXT_LENGTH, ModelCapability.TEXT_GENERATION) \
    .add_field(IS_MULTIMODAL_LLM, ModelCapability.TEXT_GENERATION) \
    .add_field(_OPENLLM_IS_REASONING, ModelCapability.TEXT_GENERATION) \
    .build_decorator()
class OpenLLMProvider:
    pass
