from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# How many ids one lookup or one write asks about.
_BATCH = 100


class ServiceAccountKindMigrationService:
    """Marks the user nodes that belong to service accounts rather than people.

    ``kind`` is new on the user node, and nodes written before it existed do
    not carry it. We cannot work out which is which: the rule lives in the user
    management service's Mongo collection, which this service cannot reach, and
    the reserved email domain is a consequence of that rule, not the rule.

    So the two halves split the work. The user management service stages the
    ``userId`` of every live service account under ``MIGRATION_FLAG_KEY``
    (``service_account_kind_backfill.migration.ts``); this stamps ``kind`` on
    those nodes and writes ``done`` back to the same key. Nothing records the
    backfill as finished until the write has actually happened here — queuing
    an event is not the same as applying it, and during a rolling upgrade the
    previous replica is still consuming, so an event carrying ``kind`` can be
    read by a binary that drops it.

    Only ``kind`` is written. The nodes are resolved first, in batched queries
    rather than one lookup per account, so nothing is created by a stale id and
    the startup health gate is not held open by a round trip per account. No
    other field is touched — in particular not ``isActive``, so an account
    deleted after it was staged stays deleted.

    Idempotent, and retried on the next startup when it fails. A staged id with
    no node yet usually means that account's own ``userAdded`` has not been
    consumed: the accounts that do have nodes are still stamped, and only
    ``done`` is withheld, so the next startup stamps the straggler and
    finishes. What has already been written is recorded alongside the staged
    list, so that next startup writes only the straggler rather than every
    node again — the upsert merges, so a repeat would still move
    ``updatedAtTimestamp`` on nodes that are already correct.
    """

    MIGRATION_FLAG_KEY = "/migrations/service_account_kind_v1"
    STATE_VERSION = 1

    def __init__(self, graph_provider: IGraphDBProvider, config_service: ConfigurationService, logger) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _read_state(self) -> dict | None:
        """The staged document, or None when nothing has been staged yet.

        raise_on_error, so a store that cannot be read is not mistaken for a
        key that was never written — that reads as "nothing to do" and the
        boot looks idle while the backfill silently waits for another restart.
        """
        state = await self.config_service.get_config(
            self.MIGRATION_FLAG_KEY, raise_on_error=True
        )
        return state if isinstance(state, dict) else None

    async def _resolve_nodes(self, wanted: list[str]) -> tuple[dict[str, str], list[str]]:
        """Map each staged `userId` to its node key, and name the ones with no node."""
        found: dict[str, str] = {}
        missing: list[str] = []

        for start in range(0, len(wanted), _BATCH):
            chunk = wanted[start : start + _BATCH]
            try:
                found.update(
                    await self.graph_provider.get_graph_user_keys_by_mongo_user_ids(
                        chunk, chunk_size=_BATCH
                    )
                )
            except ValueError:
                # That call raises for the whole chunk when any one id has no
                # node, and does not hand back the ones it did find. So only
                # the chunk holding a straggler pays a lookup per id, and only
                # while that id is missing. A store failure is not a ValueError
                # and still surfaces.
                for user_id in chunk:
                    node = await self.graph_provider.get_user_by_user_id(
                        user_id, raise_on_error=True
                    )
                    key = (node or {}).get("id") or (node or {}).get("_key")
                    if key:
                        found[user_id] = str(key)
                    else:
                        missing.append(user_id)

        return found, missing

    async def _unchanged(
        self, processed: list[str], disabled: set[str]
    ) -> dict | None:
        """The stored document, when it is still the one this pass applied.

        Both lists are the answer, so both have to match. A different
        `disabledUserIds` with the same `userIds` is still a different answer,
        and writing over it would strand an account whose disable has no other
        event to correct it.

        None means "leave the key alone": this pass stamped what the old
        document said, so whatever it now says has to be applied again.
        """
        try:
            current = await self._read_state()
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not re-read the service-account kind state ({e}); "
                "leaving it alone, which repeats harmlessly next startup"
            )
            return None

        if current is None or list(current.get("userIds") or []) != processed:
            return None
        stored_disabled = {
            str(user_id) for user_id in (current.get("disabledUserIds") or [])
        }
        return current if stored_disabled == disabled else None

    async def _record_done(
        self, processed: list[str], disabled: set[str], stamped: int
    ) -> None:
        """Write `done`, unless the staged lists moved while we were working.

        The store has no compare-and-set, so this re-reads instead. Lists that
        have changed mean the user management service staged something after
        we read: writing `done` over that would leave it unapplied for good,
        since staging stops once `done` is set. Leaving the key alone costs one
        more pass on the next startup.
        """
        if await self._unchanged(processed, disabled) is None:
            self.logger.info(
                "ℹ️ The staged service-account list changed while it was being "
                "applied; finishing on the next startup"
            )
            return

        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "version": self.STATE_VERSION,
                    "done": True,
                    # Kept, not replaced by the flag. The user management
                    # service compares these against the collection on every
                    # boot: `done` says these accounts were marked, not that
                    # the answer has not moved since, and during a rolling
                    # upgrade an older consumer can swallow the event for one
                    # that changed in between.
                    "userIds": processed,
                    "disabledUserIds": sorted(disabled),
                    "stamped": stamped,
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set the service-account kind flag: {e}; it will re-run (harmlessly)"
            )

    async def _record_progress(
        self, processed: list[str], disabled: set[str], stamped_ids: set[str]
    ) -> None:
        """Remember what has been written, so an unfinished pass is not redone.

        Built on the document as it is now rather than the one read at the
        start, and written only while that is still the document this pass
        applied. Writing the old snapshot back would undo a list the user
        management service staged meanwhile and then mark its ids stamped, so
        the next startup would skip exactly the accounts it needed to write.
        """
        current = await self._unchanged(processed, disabled)
        if current is None:
            return

        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {**current, "stampedUserIds": sorted(stamped_ids)},
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not record service-account kind progress: {e}; "
                "the next startup repeats work that changes nothing"
            )

    async def migrate(self) -> dict[str, Any]:
        try:
            state = await self._read_state()
        except Exception as e:
            self.logger.error(f"❌ Could not read the service-account kind state: {e}")
            return {"success": False, "stamped": 0, "error": str(e)}

        if state is None:
            # Nothing staged yet. The user management service stages on every
            # boot until we answer, so this is not a failure and must not be
            # recorded as done.
            return {"success": True, "stamped": 0, "skipped": True}
        if state.get("done") is True:
            return {"success": True, "stamped": 0, "skipped": True}

        processed = [str(user_id) for user_id in (state.get("userIds") or [])]
        # Only the ones switched off are named. Absence already reads as not
        # disabled, and an account disabled before any of this shipped emits
        # no event of its own, so this is its only chance to be marked.
        disabled = {str(user_id) for user_id in (state.get("disabledUserIds") or [])}
        # Written by an earlier pass that could not finish. The staged list is
        # unchanged, so these nodes already hold what this would write.
        already = {str(user_id) for user_id in (state.get("stampedUserIds") or [])}
        if not processed:
            # Staged explicitly as empty: this deployment has no service
            # accounts, so there is nothing to mark and the backfill is done.
            await self._record_done(processed, set(), 0)
            return {"success": True, "stamped": 0, "skipped": True}

        stamped = 0
        try:
            found, missing = await self._resolve_nodes(processed)

            resolved = [user_id for user_id in processed if user_id in found]
            node_keys = [
                found[user_id] for user_id in resolved if user_id not in already
            ]
            disabled_keys = {
                found[user_id]: user_id in disabled for user_id in resolved
            }
            for start in range(0, len(node_keys), _BATCH):
                batch = [
                    {
                        "id": node_key,
                        "kind": "service",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        # Written both ways rather than only when true: an
                        # account enabled again before this ran would
                        # otherwise keep a stale flag from an earlier pass.
                        "isDisabled": disabled_keys.get(node_key, False),
                    }
                    for node_key in node_keys[start : start + _BATCH]
                ]
                # Both backends answer falsy for a write that did not land, and
                # counting those as stamped would retire them from the retry.
                if not await self.graph_provider.batch_upsert_nodes(
                    batch, CollectionNames.USERS.value
                ):
                    raise RuntimeError(
                        f"The graph rejected {len(batch)} service-account kind write(s)"
                    )
                stamped += len(batch)
        except Exception as e:
            self.logger.error(f"❌ Service-account kind migration failed: {e}", exc_info=True)
            return {"success": False, "stamped": stamped, "error": str(e)}

        if missing:
            # Marked what we could, and recorded it so the next startup writes
            # only the stragglers. `done` is withheld until none are left.
            self.logger.warning(
                f"⚠️ {len(missing)} staged service account(s) have no user node yet; "
                "marked the rest and will finish on a later startup"
            )
            if resolved:
                await self._record_progress(processed, disabled, already | set(resolved))
            return {"success": True, "stamped": stamped, "missing": len(missing)}

        await self._record_done(processed, disabled, stamped)
        return {"success": True, "stamped": stamped, "missing": 0}


async def run_service_account_kind_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await ServiceAccountKindMigrationService(graph_provider, config_service, logger).migrate()
