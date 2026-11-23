"""Re-queue the records refused as "file type not supported" that the indexer can read now."""
from app.migrations.file_type_requeue.migration import run_file_type_requeue

__all__ = ["run_file_type_requeue"]
