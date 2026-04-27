"""Per-collection embedding-model spec persisted in the Configuration Service.

The spec records which embedding model (provider + model name + dimension +
is_multimodal) built a given vector collection. It used to live as a sentinel
point inside the Qdrant collection itself; moving it to the ConfigurationService
decouples collection metadata from vector-DB storage and removes the need for
sentinel-exclusion logic at every read/count site.

Layout in the KV store:
    /services/vectorCollections/<collection_name> = {
        "embedding_provider":  str,   # normalized (lower-cased, "models/" stripped)
        "embedding_model":     str,   # normalized
        "embedding_dimension": int,
        "is_multimodal":       bool,  # optional
        "prompt_format":       str,   # optional; e.g. "gemini2_v1" when the
                                      # ingestion path prepends the Gemini-2
                                      # prompt prefixes. Absent on collections
                                      # built without any input rewriting,
                                      # which is what older Gemini-2
                                      # collections look like.
        "signature_version":   int,
    }
"""
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.config.constants.service import config_node_constants


def normalize_identity(value: Optional[str]) -> str:
    """Normalize provider/model identifiers for identity comparisons.

    Strips an optional ``models/`` prefix (used by Google/Vertex), lower-cases
    and trims whitespace so minor casing/prefix differences don't produce
    spurious "model change" errors when the user is actually saving the same
    configuration.

    Returns ``""`` for falsy input so callers can cheaply distinguish
    "unknown" from "different".
    """
    if not value:
        return ""
    return value.removeprefix("models/").strip().lower()


# Bump when the on-disk payload shape changes. Readers that don't understand the
# version should fall back to "unknown" rather than silently misinterpreting it.
#
# v2: added optional ``prompt_format`` field. Writers stamp it for collections
#     whose ingestion path rewrites text inputs (currently only the Gemini-2
#     family, which requires Google's prompt-prefix protocol since the
#     ``task_type`` field is ignored by that model). Older v1 specs simply
#     don't carry the field, and ``_signatures_match`` treats the difference
#     between "absent" and ``"gemini2_v1"`` as a hard mismatch so legacy
#     collections get re-built before being mixed with prefixed vectors.
SIGNATURE_VERSION = 2

# Identity used to describe the built-in default embedding model when no spec
# is persisted yet. Mirrors `get_default_embedding_model()` in app.utils.aimodels
# (HuggingFace BGE). Kept provider-less so it can't be confused with a
# user-configured HuggingFace entry.
DEFAULT_PROVIDER = "default"


def collection_spec_key(collection_name: str) -> str:
    """Return the full KV store key for a collection's spec."""
    return f"{config_node_constants.VECTOR_COLLECTIONS.value}/{collection_name}"


async def get_collection_spec(
    config_service: ConfigurationService,
    collection_name: str,
    *,
    use_cache: bool = True,
) -> Optional[dict]:
    """Fetch the stored spec for ``collection_name``.

    Returns ``None`` when no spec is stored (legacy/pre-migration collections).
    The caller is expected to fall back to :func:`default_collection_spec` in
    that case.
    """
    if not collection_name:
        return None
    key = collection_spec_key(collection_name)
    value = await config_service.get_config(key, use_cache=use_cache)
    if not isinstance(value, dict) or not value:
        return None
    return value


async def set_collection_spec(
    config_service: ConfigurationService,
    collection_name: str,
    *,
    provider: Optional[str],
    model: Optional[str],
    dimension: int,
    is_multimodal: Optional[bool] = None,
    prompt_format: Optional[str] = None,
) -> bool:
    """Persist the spec for ``collection_name``.

    Provider/model are normalized so later identity comparisons don't trip on
    casing / ``models/`` prefix differences. ``is_multimodal`` and
    ``prompt_format`` are only written when explicitly known, matching the
    pre-existing sentinel semantics — readers can distinguish "unknown"
    (legacy spec) from a deliberate text-only / no-rewrite value.
    """
    if not collection_name:
        return False

    payload: dict = {
        "embedding_provider": normalize_identity(provider),
        "embedding_model": normalize_identity(model),
        "embedding_dimension": int(dimension),
        "signature_version": SIGNATURE_VERSION,
    }
    if is_multimodal is not None:
        payload["is_multimodal"] = bool(is_multimodal)
    if prompt_format:
        payload["prompt_format"] = str(prompt_format)

    return await config_service.set_config(
        collection_spec_key(collection_name), payload
    )


async def delete_collection_spec(
    config_service: ConfigurationService,
    collection_name: str,
) -> bool:
    """Remove the spec for a collection that's being dropped.

    Best-effort: returns False if the node doesn't exist or the store rejects
    the call. Callers should not fail the outer operation on this.
    """
    if not collection_name:
        return False
    try:
        return await config_service.delete_config(collection_spec_key(collection_name))
    except Exception:
        return False


def default_collection_spec(dimension: Optional[int]) -> dict:
    """Fallback spec used when no node is stored for an existing collection.

    Pre-migration collections built before this feature existed will return
    ``None`` from :func:`get_collection_spec`; we treat them as if they were
    built with the default embedding model (``DEFAULT_EMBEDDING_MODEL``). The
    dimension, when known, is forwarded so the same-dim identity check still
    functions.
    """
    spec: dict = {
        "embedding_provider": normalize_identity(DEFAULT_PROVIDER),
        "embedding_model": normalize_identity(DEFAULT_EMBEDDING_MODEL),
        "signature_version": SIGNATURE_VERSION,
    }
    if isinstance(dimension, int) and dimension > 0:
        spec["embedding_dimension"] = int(dimension)
    return spec
