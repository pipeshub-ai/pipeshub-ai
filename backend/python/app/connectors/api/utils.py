import asyncio
import logging

_log = logging.getLogger(__name__)

async def increment_org_corpus_revision_with_retry(
    graph_provider,
    org_id: str | None,
    mutation_id: str | None = None
) -> bool:
    """Bump the corpus revision for *org_id*.

    Retries up to 3 times with a short backoff so transient graph errors do not
    leave semantic-cache hits silently enabled after a mutation.
    
    If the org_id is missing, this function will log a warning and return safely.
    
    Returns:
        True if the revision was bumped, False if all attempts failed (or org_id
        was missing) so callers can flag the invalidation as pending.
    """
    if not org_id:
        _log.warning(
            "increment_org_corpus_revision_with_retry called without an org_id; skipping bump."
        )
        return False

    max_attempts = 3
    last_exc: Exception | None = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            await graph_provider.increment_corpus_revision(org_id, mutation_id)
            return True  # success
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                await asyncio.sleep(0.5 * attempt)
                
    _log.warning(
        "Could not increment corpus revision for org '%s' after %d attempts: %s",
        org_id, max_attempts, str(last_exc),
    )
    return False
