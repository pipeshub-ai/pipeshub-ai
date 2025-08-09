import logging
import os
import sys

# Ensure log directory exists
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Force UTF-8 for stdout/stderr in Windows
if sys.platform == "win32":
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Configure base logging settings
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    encoding="utf-8",
)


def create_logger(service_name: str) -> logging.Logger:
    """
    Create a logger for a specific service with file and console handlers
    """
    # Create logger
    logging_level = os.getenv("LOG_LEVEL", "info").lower()
    logger = logging.getLogger(service_name)
    if logging_level == "debug":
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Prevent DUPLICATE handlers
    if not logger.handlers:
        # Enhanced format with filename and line number
        log_format = "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"

        # File handler with enhanced format
        file_handler = logging.FileHandler(
            os.path.join(log_dir, f"{service_name}.log"),
            encoding="utf-8",  # Explicitly set encoding here too
        )
        file_handler.setFormatter(logging.Formatter(log_format))

        # Console handler with enhanced format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter(log_format))

        # Add handlers
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.propagate = False

    return logger
