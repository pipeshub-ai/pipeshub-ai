import logging
import logging.handlers
import os
import sys

LOG_DIR = os.getenv("LOG_DIR", os.path.join(os.getcwd(), "logs"))
LOG_FILE_MAX_BYTES = 20 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 10
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"

_log_dir_available = False
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    _log_dir_available = True
except OSError:
    print(f'WARNING: Failed to create log directory "{LOG_DIR}", falling back to console-only logging', file=sys.stderr)


class ColoredFormatter(logging.Formatter):

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
    format=LOG_FORMAT,
    encoding="utf-8",
)

# Suppress Neo4j notification warnings (missing labels, etc.)
data_store = os.getenv("DATA_STORE", "arangodb").lower()
if data_store == "neo4j":
    neo4j_notifications_logger = logging.getLogger("neo4j.notifications")
    neo4j_notifications_logger.setLevel(logging.ERROR)


def create_logger(service_name: str) -> logging.Logger:
    logging_level = os.getenv("LOG_LEVEL", "info").lower()
    logger = logging.getLogger(service_name)
    if logging_level == "debug":
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not logger.handlers:
        if _log_dir_available:
            file_handler = logging.handlers.RotatingFileHandler(
                os.path.join(LOG_DIR, f"{service_name}.log"),
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        if sys.stdout.isatty():
            console_handler.setFormatter(ColoredFormatter(LOG_FORMAT))
        else:
            console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

        logger.addHandler(console_handler)
        logger.propagate = False

    return logger
