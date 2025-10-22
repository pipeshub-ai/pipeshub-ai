"""
Caching utilities for performance optimization
"""
import asyncio
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import BaseArangoService

# In-memory cache for user/org info (using OrderedDict for O(1) LRU eviction)
_user_info_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_cache_lock = asyncio.Lock()

# Cache configuration
USER_INFO_CACHE_TTL = 600  # 10 minutes
MAX_CACHE_SIZE = 1000  # Maximum number of cached entries


async def get_cached_user_info(
    arango_service: BaseArangoService,
    user_id: str,
    org_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Get user and org info with caching to reduce database calls.

    Args:
        arango_service: ArangoDB service instance
        user_id: User ID
        org_id: Organization ID

    Returns:
        Tuple of (user_info, org_info)
    """
    cache_key = f"{user_id}:{org_id}"

    # Check cache first
    async with _cache_lock:
        cached = _user_info_cache.get(cache_key)

        if cached:
            # Check if cache is still valid
            age = (datetime.now() - cached['timestamp']).total_seconds()
            if age < USER_INFO_CACHE_TTL:
                # Move to end to mark as recently used (LRU)
                _user_info_cache.move_to_end(cache_key)
                return cached['user_info'], cached['org_info']
            else:
                # Remove stale entry
                del _user_info_cache[cache_key]

    # Cache miss or expired - fetch from database
    # Use asyncio.gather for parallel fetching
    try:
        user_info, org_info = await asyncio.gather(
            arango_service.get_user_by_user_id(user_id),
            arango_service.get_document(org_id, CollectionNames.ORGS.value),
            return_exceptions=True
        )

        # Handle exceptions from gather
        if isinstance(user_info, Exception):
            user_info = None
        if isinstance(org_info, Exception):
            org_info = None

        # Cache the result
        async with _cache_lock:
            # Implement LRU cache eviction if cache is full
            if len(_user_info_cache) >= MAX_CACHE_SIZE:
                # Remove least recently used entry in O(1) time
                _user_info_cache.popitem(last=False)

            _user_info_cache[cache_key] = {
                'user_info': user_info,
                'org_info': org_info,
                'timestamp': datetime.now()
            }

        return user_info, org_info

    except Exception:
        # If fetching fails, return None for both
        return None, None


async def clear_user_info_cache(user_id: Optional[str] = None, org_id: Optional[str] = None) -> None:
    """
    Clear user info cache entries.

    Args:
        user_id: If provided, clear cache for this user only
        org_id: If provided, clear cache for this org only
        If both None, clear entire cache
    """
    async with _cache_lock:
        if user_id is None and org_id is None:
            _user_info_cache.clear()
        else:
            # Clear specific entries
            keys_to_remove = []
            for key in _user_info_cache:
                key_user_id, key_org_id = key.split(':')
                if (user_id is None or key_user_id == user_id) and \
                   (org_id is None or key_org_id == org_id):
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del _user_info_cache[key]


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics for monitoring"""
    return {
        'size': len(_user_info_cache),
        'max_size': MAX_CACHE_SIZE,
        'ttl_seconds': USER_INFO_CACHE_TTL
    }

