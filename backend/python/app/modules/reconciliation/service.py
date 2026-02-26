"""
Reconciliation Service
"""
import json
from typing import Any, Dict, List, Optional, Set, Tuple
import hashlib
from app.models.blocks import BlocksContainer
from app.utils.logger import create_logger

logger = create_logger("reconciliation_service")


class ReconciliationMetadata:
    """
    Metadata for reconciliation stored alongside each record in blob storage.

    Contains two mappings:
    1. hash_to_block_ids: content_hash -> List[block_id]
       Tracks ALL blocks with the same content (handles duplicates correctly).
       The list preserves insertion order: block_group IDs first, then block IDs,
       each sub-group ordered by their position in the container.
    2. block_id_to_index: block_id -> index (int) to locate blocks in the blob for O(1) access
    """

    def __init__(
        self,
        hash_to_block_ids: Optional[Dict[str, List[str]]] = None,
        block_id_to_index: Optional[Dict[str, int]] = None,
    ) -> None:
        self.hash_to_block_ids: Dict[str, List[str]] = hash_to_block_ids or {}
        self.block_id_to_index: Dict[str, int] = block_id_to_index or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hash_to_block_ids": self.hash_to_block_ids,
            "block_id_to_index": self.block_id_to_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReconciliationMetadata":
        raw = data.get("block_id_to_index", {})
        block_id_to_index: Dict[str, int] = {}
        for bid, val in raw.items():
            if isinstance(val, int):
                block_id_to_index[bid] = val
            elif isinstance(val, dict) and "index" in val:
                block_id_to_index[bid] = val["index"]

        return cls(
            hash_to_block_ids=data.get("hash_to_block_ids", {}),
            block_id_to_index=block_id_to_index,
        )


class ReconciliationService:
    def __init__(self, service_logger=None) -> None:
        self.logger = service_logger or logger

    def build_metadata(self, block_containers: BlocksContainer) -> ReconciliationMetadata:
        """
        Build reconciliation metadata from a BlocksContainer.

        Block groups are processed first, then blocks. Within each group the
        iteration order matches the container's list order, so the per-hash
        ID list is always ordered: [block_group_ids..., block_ids...] with
        each sub-group sorted by container index.

        Returns:
            ReconciliationMetadata with hash mappings
        """
        hash_to_block_ids: Dict[str, List[str]] = {}
        block_id_to_index: Dict[str, int] = {}

        for i, block_group in enumerate(block_containers.block_groups):
            if block_group.content_hash is None:
                block_group.content_hash = hashlib.sha256(json.dumps(block_group.data, sort_keys=True).encode('utf-8')).hexdigest() + ":" + hashlib.md5(json.dumps(block_group.data, sort_keys=True).encode('utf-8')).hexdigest()
            if block_group.content_hash:
                hash_to_block_ids.setdefault(block_group.content_hash, []).append(block_group.id)
                block_id_to_index[block_group.id] = i

        for i, block in enumerate(block_containers.blocks):
            if block.content_hash is None:
                block.content_hash = hashlib.sha256(json.dumps(block.data, sort_keys=True).encode('utf-8')).hexdigest() + ":" + hashlib.md5(json.dumps(block.data, sort_keys=True).encode('utf-8')).hexdigest()
            if block.content_hash:
                hash_to_block_ids.setdefault(block.content_hash, []).append(block.id)
                block_id_to_index[block.id] = i

        total_block_ids = sum(len(ids) for ids in hash_to_block_ids.values())
        self.logger.info(
            f"📊 Built reconciliation metadata: "
            f"{len(hash_to_block_ids)} unique hashes, "
            f"{total_block_ids} total block IDs (across hashes), "
            f"{len(block_id_to_index)} block index mappings"
        )

        return ReconciliationMetadata(
            hash_to_block_ids=hash_to_block_ids,
            block_id_to_index=block_id_to_index,
        )

    def compute_diff(
        self,
        old_metadata: ReconciliationMetadata,
        new_metadata: ReconciliationMetadata,
    ) -> Tuple[Set[str], Set[str], Dict[str, str]]:
        """
        Compute the diff between old and new metadata.

        For each content_hash present in both old and new, the block ID lists
        are matched positionally (since build_metadata always appends in
        container order, the positions are stable across versions):
          - Paired IDs are unchanged: new_id -> old_id  (preserve the Qdrant vector)
          - If new list is longer:  excess new IDs need fresh indexing
          - If old list is longer:  excess old IDs need deletion from Qdrant

        Args:
            old_metadata: Previous version's reconciliation metadata
            new_metadata: Current version's reconciliation metadata

        Returns:
            Tuple of:
            - blocks_to_index_ids:  new block IDs that need embedding + Qdrant upsert
            - block_ids_to_delete:  old block IDs to remove from Qdrant
            - unchanged_id_map:     new_block_id -> old_block_id for ID preservation
        """
        remaining_old_hashes: Dict[str, List[str]] = {
            h: list(ids) for h, ids in old_metadata.hash_to_block_ids.items()
        }
        blocks_to_index_ids: Set[str] = set()
        block_ids_to_delete: Set[str] = set()
        unchanged_id_map: Dict[str, str] = {}

        for content_hash, new_ids in new_metadata.hash_to_block_ids.items():
            if content_hash in remaining_old_hashes:
                old_ids = remaining_old_hashes.pop(content_hash)
                min_len = min(len(new_ids), len(old_ids))

                for i in range(min_len):
                    unchanged_id_map[new_ids[i]] = old_ids[i]

                # More new duplicates than old -> index the extras
                for i in range(min_len, len(new_ids)):
                    blocks_to_index_ids.add(new_ids[i])

                # More old duplicates than new -> delete the extras
                for i in range(min_len, len(old_ids)):
                    block_ids_to_delete.add(old_ids[i])
            else:
                # Entirely new content hash -> index all its blocks
                blocks_to_index_ids.update(new_ids)

        # Hashes that existed in old but are gone in new -> delete all their blocks
        for old_ids in remaining_old_hashes.values():
            block_ids_to_delete.update(old_ids)

        self.logger.info(
            f"📊 Reconciliation diff: "
            f"{len(blocks_to_index_ids)} blocks to index, "
            f"{len(block_ids_to_delete)} blocks to delete, "
            f"{len(unchanged_id_map)} unchanged (IDs preserved)"
        )

        return blocks_to_index_ids, block_ids_to_delete, unchanged_id_map

    def apply_preserved_ids(
        self,
        block_containers: BlocksContainer,
        unchanged_id_map: Dict[str, str],
    ) -> None:
        if not unchanged_id_map:
            return

        replaced = 0
        for block in block_containers.blocks:
            if block.id in unchanged_id_map:
                block.id = unchanged_id_map[block.id]
                replaced += 1

        for block_group in block_containers.block_groups:
            if block_group.id in unchanged_id_map:
                block_group.id = unchanged_id_map[block_group.id]
                replaced += 1

        self.logger.info(
            f"📊 Preserved {replaced} block/block_group IDs from previous version"
        )