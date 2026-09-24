"""Per-model image capability: how many images this model accepts in one
request, and how large each may be.

PipesHub configures 19 LLM providers whose image limits differ by two orders
of magnitude, and the binding constraint is rarely the documented maximum:

* Azure OpenAI hard-rejects a chat request carrying more than 10 images,
  while OpenAI direct accepts 1,500 of the same model family.
* Bedrock's Converse API caps a request at 20 images of at most 3.75 MB.
* Anthropic accepts 100 (200k-context models) or 600, but applies a stricter
  per-image dimension limit to any request above 20 image blocks -- images
  nested in `tool_result` included -- and rejects oversized ones outright.
* Ollama's LLaVA/Qwen-VL builds process one image per turn; a self-hosted
  vLLM endpoint refuses anything above its `--limit-mm-per-prompt`, which is
  commonly left small.

Above all of those sits a quality ceiling that binds first. Multi-image
benchmarks (Visual Haystacks, MuirBench) show accuracy falling as irrelevant
images accumulate -- at ~20 images a caption-aggregation baseline catches up
to open-source multimodal models -- and production multimodal-RAG systems
send a handful of reranked images per query, not dozens. So each default here
is the smaller of "what the API accepts" and "what still helps", which is why
the OpenAI entry says 8 rather than 1,500.

Scope note: whether a *tool-result* message may carry an image is
deliberately NOT decided here. That depends on the wire shape a transport
chose (Chat Completions vs Responses, `/api/chat` vs `/v1/messages`), which
only the transport knows -- see `LangChainTransport._supports_multipart_tool_result`.
Answering it from a provider string as well would be a second source of truth
for one question, and the two would drift.

Resolution order is env override -> provider table -> conservative unknown
default, and a model the deployment marked text-only resolves to zero images
regardless of provider. Unknown means conservative: an OpenAI-compatible
endpoint is a black box, so assume it behaves like the weakest thing it could
be and let an operator raise it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.utils.env_utils import env_int

# Anthropic's standard-tier native raster: images are downscaled to a 1568 px
# long edge before the model sees them, so sending more resolution than that
# costs tokens and buys nothing. It also sits under the 2000 px ceiling
# Anthropic imposes on requests carrying more than 20 image blocks, so an
# image normalized to this size can never trip that rule.
_STANDARD_LONG_EDGE_PX = 1568
# Local/quantized vision models work at lower native resolutions and pay for
# large rasters in wall-clock time rather than tokens.
_LOCAL_LONG_EDGE_PX = 1120

_MB = 1024 * 1024

# An operator can raise a cap, but not past the point where every provider in
# the table would reject the request outright.
MAX_CONFIGURABLE_IMAGES = 100

GLOBAL_ENV_VAR = "PIPESHUB_MAX_IMAGES_PER_REQUEST"
PROVIDER_ENV_PREFIX = "PIPESHUB_MAX_IMAGES_"


@dataclass(frozen=True)
class ImagePolicy:
    """What one model will accept as image input for a single request."""

    max_images_per_request: int
    max_long_edge_px: int
    max_bytes_per_image: int
    # Where `max_images_per_request` came from, for logs and tests:
    # "env" | "provider" | "unknown-default" | "text-only".
    source: str

    @property
    def allows_images(self) -> bool:
        return self.max_images_per_request > 0


def _policy(
    max_images: int,
    *,
    long_edge: int = _STANDARD_LONG_EDGE_PX,
    max_bytes: int = 5 * _MB,
    source: str = "provider",
) -> ImagePolicy:
    return ImagePolicy(
        max_images_per_request=max_images,
        max_long_edge_px=long_edge,
        max_bytes_per_image=max_bytes,
        source=source,
    )


# A model the deployment marked as text-only. Renderers still emit every image
# block's text; this policy only means no pixels are ever attached.
TEXT_ONLY_POLICY = _policy(0, source="text-only")

# Gateways that forward to an upstream we cannot name from here, and
# self-hosted OpenAI-compatible servers. Two images is what the weakest
# plausible backend accepts.
UNKNOWN_POLICY = _policy(
    2, long_edge=_LOCAL_LONG_EDGE_PX, max_bytes=4 * _MB, source="unknown-default",
)

# Keyed on normalized `LLMProvider` values (`app/utils/aimodels.py`). Only
# providers with a documented limit get an entry -- inventing a number for a
# provider that hosts arbitrary third-party models would be a guess dressed
# as a fact, and `UNKNOWN_POLICY` is the honest answer for those.
_PROVIDER_POLICIES: dict[str, ImagePolicy] = {
    # Hard cap 1,500 images / 512 MB payload.
    "openai": _policy(8, max_bytes=20 * _MB),
    # Hard cap 10 per chat request -- the tightest of any hosted provider.
    "azureopenai": _policy(8, max_bytes=20 * _MB),
    # Azure AI Foundry fronts several model families whose limits differ;
    # stay under the tightest of them.
    "azureai": _policy(6, max_bytes=20 * _MB),
    # 100 per request on 200k-context models, 600 otherwise; 10 MB per image
    # direct, 5 MB via Bedrock/Vertex. The cap below also keeps every request
    # under the 20-block threshold that tightens per-image dimensions.
    "anthropic": _policy(12, max_bytes=5 * _MB),
    # Converse API: hard cap 20 images, 3.75 MB and 8000 px each.
    "bedrock": _policy(10, max_bytes=3 * _MB + 512 * 1024),
    # Hard cap 3,600 images; 768x768 tiles at 258 tokens each.
    "gemini": _policy(12, max_bytes=7 * _MB),
    "vertexai": _policy(12, max_bytes=5 * _MB),
    # LLaVA and Qwen-VL under Ollama process one image per turn.
    "ollama": _policy(1, long_edge=_LOCAL_LONG_EDGE_PX, max_bytes=4 * _MB),
    "lmstudio": _policy(1, long_edge=_LOCAL_LONG_EDGE_PX, max_bytes=4 * _MB),
    # Routes to whichever upstream has capacity, so an `openai/*` model can
    # land on Azure and inherit its 10.
    "openrouter": _policy(6),
    # A proxy in front of anything; treated like any other gateway.
    "litellmproxy": _policy(4),
}


def permissive_policy(max_images: int) -> ImagePolicy:
    """Policy for a call site that has no model policy to work from -- older
    callers and tests. Bounded only by the conversation ceiling the caller
    already passes, so behaviour there is unchanged until it is wired up.

    Kept here so every `ImagePolicy` value in the system is constructed by
    this module and nothing else invents limits.
    """
    return _policy(max_images, source="permissive")


def _normalize_provider(provider: str | None) -> str:
    """Fold the spellings a provider string arrives in (`azureOpenAI`,
    `azure_openai`, `AWS Bedrock`) onto one table key."""
    folded = (provider or "").strip().lower()
    for ch in ("-", "_", " "):
        folded = folded.replace(ch, "")
    return folded


def provider_env_var(provider: str | None) -> str:
    """The env var that overrides this provider's cap -- for log lines that
    tell an operator which knob to turn."""
    return f"{PROVIDER_ENV_PREFIX}{_normalize_provider(provider).upper()}"


def _env_override(provider_key: str) -> int | None:
    """Operator override for this provider, else the global one, else None."""
    specific = env_int(
        f"{PROVIDER_ENV_PREFIX}{provider_key.upper()}",
        default=None, lo=0, hi=MAX_CONFIGURABLE_IMAGES,
    )
    if specific is not None:
        return specific
    return env_int(GLOBAL_ENV_VAR, default=None, lo=0, hi=MAX_CONFIGURABLE_IMAGES)


def resolve_image_policy(*, provider: str | None, is_multimodal: bool) -> ImagePolicy:
    """The image policy for the model this request is actually using.

    `is_multimodal` is the deployment's own `isMultimodal` flag for the model
    (`aimodels.is_multimodal_llm`) -- the single source of truth for whether
    pixels are worth sending at all.

    Resolve per model rather than per request: a sub-agent can run a
    different model than its parent (`domain_agents.py` builds its own
    `ModelSpec`), and a conversation can switch models between turns.
    """
    if not is_multimodal:
        return TEXT_ONLY_POLICY

    provider_key = _normalize_provider(provider)
    policy = _PROVIDER_POLICIES.get(provider_key, UNKNOWN_POLICY)

    override = _env_override(provider_key)
    if override is not None:
        policy = replace(policy, max_images_per_request=override, source="env")

    return policy


__all__ = [
    "GLOBAL_ENV_VAR",
    "permissive_policy",
    "MAX_CONFIGURABLE_IMAGES",
    "PROVIDER_ENV_PREFIX",
    "TEXT_ONLY_POLICY",
    "UNKNOWN_POLICY",
    "ImagePolicy",
    "provider_env_var",
    "resolve_image_policy",
]
