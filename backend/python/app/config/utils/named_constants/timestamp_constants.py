"""
Timestamp-related constants used across the application.
This module contains constants for timeout values, validation windows,
and other time-based configurations to ensure consistency across
different components.
"""


SLACK_TIMESTAMP_TOLERANCE_SECONDS = 300
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 300
DEFAULT_SYNC_INTERVAL_SECONDS = 300
SYNC_STUCK_THRESHOLD_MS = 3600000
SYNC_LAG_WARNING_MINUTES = 60
SYNC_ERROR_RATE_THRESHOLD = 0.1
