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
    1. hash_to_block_id: content_hash -> block_id (to detect unchanged content)
    2. block_id_to_index: block_id -> index (int) to locate blocks in the blob for O(1) access
    """

    def __init__(
        self,
        hash_to_block_id: Optional[Dict[str, str]] = None,
        block_id_to_index: Optional[Dict[str, int]] = None,
    ) -> None:
        self.hash_to_block_id: Dict[str, str] = hash_to_block_id or {}
        self.block_id_to_index: Dict[str, int] = block_id_to_index or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hash_to_block_id": self.hash_to_block_id,
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
            hash_to_block_id=data.get("hash_to_block_id", {}),
            block_id_to_index=block_id_to_index,
        )


class ReconciliationService:
    def __init__(self, service_logger=None) -> None:
        self.logger = service_logger or logger

    def build_metadata(self, block_containers: BlocksContainer) -> ReconciliationMetadata:
        """
        Build reconciliation metadata from a BlocksContainer.
        Returns:
            ReconciliationMetadata with hash mappings
        """
        hash_to_block_id: Dict[str, str] = {}
        block_id_to_index: Dict[str, int] = {}

        # Process block groups (e.g., schema/DDL) with enumerated index matching blob order
        for i, block_group in enumerate(block_containers.block_groups):
            if(block_group.content_hash is None):
                #Hash only the data field
                block_group.content_hash = hashlib.sha256(json.dumps(block_group.data, sort_keys=True).encode('utf-8')).hexdigest() + ":" + hashlib.md5(json.dumps(block_group.data, sort_keys=True).encode('utf-8')).hexdigest()
            if block_group.content_hash:
                hash_to_block_id[block_group.content_hash] = block_group.id
                block_id_to_index[block_group.id] = i

        # Process individual blocks (e.g., table rows) with enumerated index matching blob order
        for i, block in enumerate(block_containers.blocks):
            if(block.content_hash is None):
                #Hash only the data field
                block.content_hash = hashlib.sha256(json.dumps(block.data, sort_keys=True).encode('utf-8')).hexdigest() + ":" + hashlib.md5(json.dumps(block.data, sort_keys=True).encode('utf-8')).hexdigest()
            if block.content_hash:
                hash_to_block_id[block.content_hash] = block.id
                block_id_to_index[block.id] = i

        self.logger.info(
            f"📊 Built reconciliation metadata: "
            f"{len(hash_to_block_id)} hashes, "
            f"{len(block_id_to_index)} block mappings"
        )

        return ReconciliationMetadata(
            hash_to_block_id=hash_to_block_id,
            block_id_to_index=block_id_to_index,
        )

    def compute_diff(
        self,
        old_metadata: ReconciliationMetadata,
        new_metadata: ReconciliationMetadata,
    ) -> Tuple[Set[str], Set[str]]:
        """
        Compute the diff between old and new metadata to determine
        which blocks need to be indexed and which need to be deleted.

        Algorithm:
        1. Iterate through new hashes:
           - If hash exists in old metadata → unchanged, skip (remove from old dict copy)
           - If hash doesn't exist → new/changed block, needs indexing
        2. Remaining entries in old dict → deleted blocks, needs deletion from qdrant

        Args:
            old_metadata: Previous version's reconciliation metadata
            new_metadata: Current version's reconciliation metadata

        Returns:
            Tuple of:
            - blocks_to_index_ids: Set of block_ids that need to be indexed (new/changed)
            - block_ids_to_delete: Set of block_ids that need to be deleted (removed)
        """
        # Copy old hash map so we can remove matched entries
        remaining_old_hashes = dict(old_metadata.hash_to_block_id)
        blocks_to_index_ids: Set[str] = set()

        for content_hash, new_block_id in new_metadata.hash_to_block_id.items():
            if content_hash in remaining_old_hashes:
                # Hash exists in old metadata → content unchanged, skip
                del remaining_old_hashes[content_hash]
            else:
                # Hash not in old metadata → new or changed content, need to index
                blocks_to_index_ids.add(new_block_id)

        # Remaining entries in old hash map → blocks that were deleted/changed
        # These need to be removed from qdrant by their old block_ids
        block_ids_to_delete: Set[str] = set(remaining_old_hashes.values())

        self.logger.info(
            f"📊 Reconciliation diff: "
            f"{len(blocks_to_index_ids)} blocks to index, "
            f"{len(block_ids_to_delete)} blocks to delete, "
            f"{len(new_metadata.hash_to_block_id) - len(blocks_to_index_ids)} unchanged"
        )

        return blocks_to_index_ids, block_ids_to_delete
