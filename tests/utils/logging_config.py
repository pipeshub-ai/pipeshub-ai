"""
Logging configuration for integration tests.

This module sets up logging for tests with appropriate formatters and handlers.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from tests.config.settings import get_settings


def setup_test_logging(
    log_level: Optional[str] = None,
    log_to_file: bool = True,
    log_file: Optional[Path] = None
) -> logging.Logger:
    """
    Set up logging for integration tests.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_file: Whether to log to file
        log_file: Path to log file
        
    Example:
        setup_test_logging(log_level="DEBUG", log_to_file=True)
    """
    settings = get_settings()
    
    # Use settings if not provided
    if log_level is None:
        log_level = settings.logging.level
    if log_file is None:
        log_file = settings.logging.log_file
    
    # Create logs directory
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    logger: logging.Logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler: logging.StreamHandler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_formatter: logging.Formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler
    if log_to_file:
        file_handler: logging.FileHandler = logging.FileHandler(log_file, mode='a')
        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_formatter: logging.Formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    # Suppress noisy loggers
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    return logger


def get_test_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific test module.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured logger instance
        
    Example:
        logger = get_test_logger(__name__)
        logger.info("Test started")
    """
    return logging.getLogger(name)

