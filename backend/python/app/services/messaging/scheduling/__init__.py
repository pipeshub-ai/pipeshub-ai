"""Consumer-side fair scheduling (Deficit Round Robin) for indexing consumers.

See ``interface.py`` for the public contract; ``drr_scheduler.py`` for the
pure (hierarchical) algorithm; ``key_extractors.py`` for the default
fairness-key strategy;
``offset_tracker.py`` for the Kafka commit-watermark helper the Kafka
consumer needs to dispatch out of offset order safely.
"""
from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairnessKey,
    FairnessKeyExtractor,
    FairSchedulerConfig,
    WeightProvider,
)
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor
from app.services.messaging.scheduling.offset_tracker import PartitionOffsetTracker

__all__ = [
    "DRRScheduler",
    "EnqueueResult",
    "FairnessKey",
    "FairnessKeyExtractor",
    "FairSchedulerConfig",
    "WeightProvider",
    "CompositeKeyExtractor",
    "PartitionOffsetTracker",
]
