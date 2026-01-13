import logging
import os
import time
from typing import Optional


class SemaphoreLogger:
    """Specialized logger for semaphore debug events.

    Writes to a dedicated log file to track parsing and indexing semaphore behavior.
    """

    _logger: Optional[logging.Logger] = None
    _enabled: bool = True

    @classmethod
    def _get_logger(cls) -> logging.Logger:
        """Get or create the semaphore logger instance."""
        if cls._logger is None:
            # Check if debug logging is enabled
            enabled = os.getenv('SEMAPHORE_DEBUG_ENABLED', 'true').lower() == 'true'
            cls._enabled = enabled

            # Create logger
            cls._logger = logging.getLogger('semaphore_debug')
            cls._logger.setLevel(logging.DEBUG)
            cls._logger.propagate = False

            # Prevent duplicate handlers
            if not cls._logger.handlers:
                # Ensure log directory exists
                log_dir = "logs"
                os.makedirs(log_dir, exist_ok=True)

                # Create file handler
                log_file = os.path.join(log_dir, "semaphore_debug.log")
                file_handler = logging.FileHandler(log_file, encoding="utf-8")

                # Format: [TIMESTAMP] [SEMAPHORE] [MESSAGE_ID] [ACTION] - Details
                formatter = logging.Formatter(
                    "%(asctime)s.%(msecs)03d [SEMAPHORE] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"
                )
                file_handler.setFormatter(formatter)
                cls._logger.addHandler(file_handler)

        return cls._logger

    @classmethod
    def log_semaphore_acquire_attempt(
        cls,
        sem_type: str,
        message_id: str,
        parsing_available: int,
        parsing_max: int,
        indexing_available: int,
        indexing_max: int
    ) -> None:
        """Log semaphore acquire attempt."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        logger.debug(
            f"[{message_id}] [ACQUIRE_ATTEMPT] {sem_type} - "
            f"parsing={parsing_available}/{parsing_max}, "
            f"indexing={indexing_available}/{indexing_max}"
        )

    @classmethod
    def log_semaphore_acquired(
        cls,
        message_id: str,
        parsing_available: int,
        parsing_max: int,
        indexing_available: int,
        indexing_max: int,
        wait_time_ms: float = 0.0
    ) -> None:
        """Log successful semaphore acquisition."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        wait_str = f", wait_time={wait_time_ms:.2f}ms" if wait_time_ms > 0 else ""
        logger.debug(
            f"[{message_id}] [ACQUIRED] parsing={parsing_available}/{parsing_max}, "
            f"indexing={indexing_available}/{indexing_max}{wait_str}"
        )

    @classmethod
    def log_semaphore_release(
        cls,
        sem_type: str,
        message_id: str,
        available: int,
        max_slots: int,
        duration: Optional[float] = None,
        reason: Optional[str] = None
    ) -> None:
        """Log semaphore release."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        duration_str = f", duration={duration:.2f}s" if duration is not None else ""
        reason_str = f", reason={reason}" if reason else ""
        logger.debug(
            f"[{message_id}] [RELEASE] {sem_type}, available={available}/{max_slots}{duration_str}{reason_str}"
        )

    @classmethod
    def log_phase_transition(
        cls,
        message_id: str,
        phase: str,
        record_id: Optional[str] = None,
        duration: Optional[float] = None
    ) -> None:
        """Log phase transition (parsing_complete or indexing_complete)."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        record_str = f", record_id={record_id}" if record_id else ""
        duration_str = f", duration={duration:.2f}s" if duration is not None else ""
        logger.debug(
            f"[{message_id}] [PHASE_COMPLETE] {phase}{record_str}{duration_str}"
        )

    @classmethod
    def log_semaphore_state(
        cls,
        parsing_available: int,
        parsing_max: int,
        indexing_available: int,
        indexing_max: int,
        active_tasks: int,
        message_id: Optional[str] = None
    ) -> None:
        """Log current semaphore state snapshot."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        msg_id_str = f"[{message_id}] " if message_id else ""
        logger.debug(
            f"{msg_id_str}[STATE] parsing={parsing_available}/{parsing_max}, "
            f"indexing={indexing_available}/{indexing_max}, active_tasks={active_tasks}"
        )

    @classmethod
    def log_message_start(cls, message_id: str, topic: str, partition: int, offset: int) -> None:
        """Log when message processing starts."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        logger.debug(
            f"[{message_id}] [MESSAGE_START] topic={topic}, partition={partition}, offset={offset}"
        )

    @classmethod
    def log_message_error(cls, message_id: str, error: str) -> None:
        """Log message processing error."""
        if not cls._enabled:
            return
        logger = cls._get_logger()
        logger.error(f"[{message_id}] [ERROR] {error}")


# Convenience function to get a timestamp for duration tracking
def get_timestamp() -> float:
    """Get current timestamp in seconds for duration calculations."""
    return time.time()
