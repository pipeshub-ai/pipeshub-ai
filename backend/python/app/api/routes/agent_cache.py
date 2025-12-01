"""
Cache Management Endpoints for World-Class Agent

Provides monitoring and management endpoints for the multi-level caching system.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.modules.agents.qna.cache_manager import get_cache_manager

router = APIRouter()


@router.get("/cache/stats")
async def get_cache_stats():
    """
    Get cache statistics across all cache layers.

    Returns hit rates, sizes, and performance metrics for:
    - LLM Response Cache
    - Tool Result Cache
    - Retrieval Cache
    """
    try:
        cache = get_cache_manager()
        stats = cache.get_all_stats()

        # Calculate overall metrics
        total_hits = sum(
            cache_stats["total_hits"]
            for cache_stats in stats.values()
        )
        total_misses = sum(
            cache_stats["total_misses"]
            for cache_stats in stats.values()
        )
        total_requests = total_hits + total_misses
        overall_hit_rate = (total_hits / total_requests * 100) if total_requests > 0 else 0

        return JSONResponse(
            content={
                "status": "success",
                "data": {
                    "caches": stats,
                    "overall": {
                        "total_hits": total_hits,
                        "total_misses": total_misses,
                        "total_requests": total_requests,
                        "overall_hit_rate_percent": round(overall_hit_rate, 2)
                    }
                }
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear")
async def clear_all_caches():
    """
    Clear all caches.

    Use with caution - this will invalidate all cached data and
    the next requests will be slower until caches warm up again.
    """
    try:
        cache = get_cache_manager()

        # Get stats before clearing
        stats_before = cache.get_all_stats()

        # Clear all caches
        cache.clear_all()

        return JSONResponse(
            content={
                "status": "success",
                "message": "All caches cleared successfully",
                "cleared": {
                    "llm_cache_entries": stats_before["llm_cache"]["size"],
                    "tool_cache_entries": stats_before["tool_cache"]["size"],
                    "retrieval_cache_entries": stats_before["retrieval_cache"]["size"]
                }
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear-expired")
async def clear_expired_caches():
    """
    Clear only expired cache entries.

    This is safer than clearing all caches and is done automatically,
    but you can trigger it manually if needed.
    """
    try:
        cache = get_cache_manager()

        # Clear expired entries
        cleared = cache.clear_expired()

        total_cleared = sum(cleared.values())

        return JSONResponse(
            content={
                "status": "success",
                "message": f"Cleared {total_cleared} expired entries",
                "cleared": cleared
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/health")
async def get_cache_health():
    """
    Get cache health metrics and recommendations.

    Provides insights into:
    - Hit rate health (low hit rate might indicate cache is too small or TTL is too short)
    - Size health (approaching limits)
    - Eviction rate (high eviction might indicate cache is too small)
    """
    try:
        cache = get_cache_manager()
        stats = cache.get_all_stats()

        health_issues = []
        recommendations = []

        # Check each cache
        for cache_name, cache_stats in stats.items():
            hit_rate = cache_stats["hit_rate_percent"]
            size = cache_stats["size"]
            max_size = cache_stats["max_size"]
            utilization = (size / max_size * 100) if max_size > 0 else 0

            # Check hit rate
            if hit_rate < 30 and cache_stats["total_requests"] > 100:
                health_issues.append(
                    f"{cache_name}: Low hit rate ({hit_rate:.1f}%)"
                )
                recommendations.append(
                    f"Consider increasing TTL for {cache_name}"
                )

            # Check utilization
            if utilization > 90:
                health_issues.append(
                    f"{cache_name}: High utilization ({utilization:.1f}%)"
                )
                recommendations.append(
                    f"Consider increasing max size for {cache_name}"
                )

        status = "healthy" if not health_issues else "needs_attention"

        return JSONResponse(
            content={
                "status": status,
                "health_check": {
                    "issues": health_issues if health_issues else ["No issues detected"],
                    "recommendations": recommendations if recommendations else ["Cache is performing optimally"],
                },
                "stats": stats
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

