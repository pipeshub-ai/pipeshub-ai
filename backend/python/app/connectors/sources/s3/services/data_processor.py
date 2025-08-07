import logging

from app.connectors.core.base.data_processor.data_processor import BaseDataProcessor


class S3DataProcessor(BaseDataProcessor):
    """Data processor for S3"""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(logger)
