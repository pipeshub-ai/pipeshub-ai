"""Per-organization configuration read and in-process caching for Semantic Caching.

Implements:
1. Typed `SemanticCacheConfig` with safe defaults (mode="off", threshold=0.98, ttl=86400, cap=10).
2. Global platform feature flag gating via `is_semantic_cache_enabled`.
3. In-process TTL caching (~30s) per organization to avoid KV store read latency on every turn.
4. Fail-safe fallback to mode="off" on missing config, malformed JSON/types, or store read errors.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from app.services.featureflag.platform_settings import is_semantic_cache_enabled

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

CONFIG_CACHE_TTL_SECONDS: float = 30.0
DEFAULT_SIMILARITY_THRESHOLD: float = 0.98
DEFAULT_TTL_SECONDS: int = 86400
DEFAULT_MAX_ENTRIES: int = 10
DEFAULT_JACCARD_THRESHOLD: float = 0.60

MIN_SIMILARITY_THRESHOLD: float = 0.90
MAX_SIMILARITY_THRESHOLD: float = 1.0
MIN_JACCARD_THRESHOLD: float = 0.60
MAX_JACCARD_THRESHOLD: float = 1.0
MIN_TTL_SECONDS: int = 60
MAX_TTL_SECONDS: int = 604800  # 7 days
MIN_MAX_ENTRIES: int = 1
MAX_MAX_ENTRIES: int = 50

VALID_MODES: frozenset[str] = frozenset({"off", "shadow", "serve"})


def _clamp_float(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, val))


def _clamp_int(val: int, min_val: int, max_val: int) -> int:
    return max(min_val, min(max_val, val))


@dataclass(frozen=True)
class SemanticCacheConfig:
    """Validated, immutable configuration for an organization's semantic cache."""

    mode: str = "off"
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    max_entries_per_context: int = DEFAULT_MAX_ENTRIES
    share_across_org: bool = False
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD


# Thread-safe in-process cache: org_id -> (config, cached_timestamp)
_config_cache: dict[str, tuple[SemanticCacheConfig, float]] = {}


def clear_config_cache(org_id: str | None = None) -> None:
    """Clear in-process cached configs (for testing or immediate invalidation)."""
    if org_id is not None:
        _ = _config_cache.pop(org_id, None)
    else:
        _config_cache.clear()


async def get_semantic_cache_config(
    config_service: ConfigurationService | None, org_id: str
) -> SemanticCacheConfig:
    """Retrieve and validate the semantic cache configuration for an organization.

    Resolution order:
    1. If global platform flag ``ENABLE_SEMANTIC_CACHE`` is disabled -> return mode="off".
    2. Check in-process cache (~30s TTL). If valid -> return cached config.
    3. Read KV store key ``/services/org/{org_id}/semanticCache``.
    4. On any read failure, missing key, or malformed values -> return mode="off".
    """
    default_off = SemanticCacheConfig(mode="off")

    # 1. Check in-process cache
    now = time.monotonic()
    cached = _config_cache.get(org_id)
    if cached is not None:
        cfg, timestamp = cached
        if now - timestamp < CONFIG_CACHE_TTL_SECONDS:
            return cfg

    # 2. Platform feature flag gate (result is cached below together with the org config)
    try:
        flag_enabled = await is_semantic_cache_enabled(config_service)
        if not flag_enabled:
            _config_cache[org_id] = (default_off, now)
            return default_off
    except Exception as exc:
        logger.warning(
            "Failed to evaluate ENABLE_SEMANTIC_CACHE flag for org %s; falling back to off: %s",
            org_id,
            type(exc).__name__,
        )
        _config_cache[org_id] = (default_off, now)
        return default_off

    if config_service is None:
        return default_off

    # 3. Read KV store key
    key = f"/services/org/{org_id}/semanticCache"
    try:
        raw_val = await config_service.get_config(key, default=None)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
    except Exception as exc:
        logger.warning(
            "Failed to read %s from ConfigurationService for org %s: %s; falling back to off",
            key,
            org_id,
            type(exc).__name__,
        )
        _config_cache[org_id] = (default_off, now)
        return default_off

    if not isinstance(raw_val, dict):
        if raw_val is not None:
            logger.warning(
                "Malformed semanticCache config for org %s: expected dict; falling back to off",
                org_id,
            )
        _config_cache[org_id] = (default_off, now)
        return default_off

    raw_dict: dict[str, object] = cast("dict[str, object]", raw_val)

    # 4. Parse and validate fields safely
    raw_mode = raw_dict.get("mode", "off")
    mode = str(raw_mode).lower() if raw_mode is not None else "off"
    if mode not in VALID_MODES:
        logger.warning(
            "Invalid semantic cache mode %r for org %s; falling back to off",
            mode,
            org_id,
        )
        mode = "off"

    try:
        raw_thresh = raw_dict.get("similarity_threshold")
        if raw_thresh is None:
            raw_thresh = raw_dict.get("similarityThreshold", DEFAULT_SIMILARITY_THRESHOLD)
        sim_val = float(str(raw_thresh)) if raw_thresh is not None else DEFAULT_SIMILARITY_THRESHOLD
        sim_threshold = _clamp_float(sim_val, MIN_SIMILARITY_THRESHOLD, MAX_SIMILARITY_THRESHOLD)
    except (TypeError, ValueError):
        sim_threshold = DEFAULT_SIMILARITY_THRESHOLD

    try:
        raw_ttl = raw_dict.get("ttl_seconds")
        if raw_ttl is None:
            raw_ttl = raw_dict.get("ttlSeconds", DEFAULT_TTL_SECONDS)
        ttl_val = int(str(raw_ttl)) if raw_ttl is not None else DEFAULT_TTL_SECONDS
        ttl_seconds = _clamp_int(ttl_val, MIN_TTL_SECONDS, MAX_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl_seconds = DEFAULT_TTL_SECONDS

    try:
        raw_max = raw_dict.get("max_entries_per_context")
        if raw_max is None:
            raw_max = raw_dict.get("maxEntriesPerContext", DEFAULT_MAX_ENTRIES)
        max_val = int(str(raw_max)) if raw_max is not None else DEFAULT_MAX_ENTRIES
        max_entries = _clamp_int(max_val, MIN_MAX_ENTRIES, MAX_MAX_ENTRIES)
    except (TypeError, ValueError):
        max_entries = DEFAULT_MAX_ENTRIES

    raw_share = raw_dict.get("share_across_org")
    if raw_share is None:
        raw_share = raw_dict.get("shareAcrossOrg", False)
    if isinstance(raw_share, bool):
        share_across_org = raw_share
    elif isinstance(raw_share, str):
        share_across_org = raw_share.strip().lower() in {"true", "1", "yes"}
    else:
        share_across_org = False

    try:
        raw_jaccard = raw_dict.get("jaccard_threshold")
        if raw_jaccard is None:
            raw_jaccard = raw_dict.get("jaccardThreshold", DEFAULT_JACCARD_THRESHOLD)
        jaccard_val = float(str(raw_jaccard)) if raw_jaccard is not None else DEFAULT_JACCARD_THRESHOLD
        jaccard_threshold = _clamp_float(jaccard_val, MIN_JACCARD_THRESHOLD, MAX_JACCARD_THRESHOLD)
    except (TypeError, ValueError):
        jaccard_threshold = DEFAULT_JACCARD_THRESHOLD

    parsed_config = SemanticCacheConfig(
        mode=mode,
        similarity_threshold=sim_threshold,
        ttl_seconds=ttl_seconds,
        max_entries_per_context=max_entries,
        share_across_org=share_across_org,
        jaccard_threshold=jaccard_threshold,
    )

    _config_cache[org_id] = (parsed_config, now)
    return parsed_config
