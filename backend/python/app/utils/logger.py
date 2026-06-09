import logging
import os
import sys


class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors for WARNING (yellow) and ERROR (red) in console output."""

    YELLOW = "\033[33m"
    RED = "\033[31m"
    RESET = "\033[0m"

    COLORS = {
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
    }

    def format(self, record) -> str:
        formatted = super().format(record)
        color = self.COLORS.get(record.levelno)
        if color:
            return f"{color}{formatted}{self.RESET}"
        return formatted

# Ensure log directory exists when possible. In restricted environments (e.g. CI
# or root-owned worktrees), fall back to tmp to avoid import-time crashes.
log_dir = os.getenv("LOG_DIR", "logs")
try:
    os.makedirs(log_dir, exist_ok=True)
except OSError:
    log_dir = os.path.join("/tmp", "pipeshub-ai-logs")
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

# Suppress Neo4j notification warnings (missing labels, etc.)
data_store = os.getenv("DATA_STORE", "arangodb").lower()
if data_store == "neo4j":
    neo4j_notifications_logger = logging.getLogger("neo4j.notifications")
    neo4j_notifications_logger.setLevel(logging.ERROR)  # Only show errors, suppress warnings


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

        # Console handler with colored format
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ColoredFormatter(log_format))

        # Try file handler first, but keep logging functional if path is not writable.
        try:
            file_handler = logging.FileHandler(
                os.path.join(log_dir, f"{service_name}.log"),
                encoding="utf-8",  # Explicitly set encoding here too
            )
            file_handler.setFormatter(logging.Formatter(log_format))
            logger.addHandler(file_handler)
        except OSError:
            pass

        # Always keep console logging enabled.
        logger.addHandler(console_handler)
        logger.propagate = False

    return logger
