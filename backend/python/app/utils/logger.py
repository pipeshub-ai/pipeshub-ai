import asyncio
import logging
import os
import sys
from collections.abc import Callable

from app.utils.request_context import NO_CONTEXT, current_display_id, get_context

# ``%(trace)s`` expands to ``[req:<id> thr:<thread> task:<task>] `` only when a
# context is in flight, so startup/background lines stay clean.
LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "%(trace)s[%(filename)s:%(lineno)d] - %(message)s"
)


def _make_record_factory(
    base_factory: Callable[..., logging.LogRecord],
) -> Callable[..., logging.LogRecord]:
    # base_factory is captured in the closure, not a module global, so reloading
    # this module cannot rebind it to ourselves and cause infinite recursion.
    def _record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)  # type: ignore[arg-type]

        if get_context() is None:
            record.request_id = ""
            record.task = NO_CONTEXT
            record.trace = ""
            return record

        try:
            task = asyncio.current_task()
            task_name = task.get_name() if task is not None else NO_CONTEXT
        except RuntimeError:
            task_name = NO_CONTEXT

        display_id = current_display_id()
        record.request_id = display_id
        record.task = task_name
        record.trace = f"[req:{display_id} thr:{record.threadName} task:{task_name}] "
        return record

    _record_factory._pipeshub_trace = True  # type: ignore[attr-defined]
    _record_factory._pipeshub_base = base_factory  # type: ignore[attr-defined]
    return _record_factory


# On reload, reuse the original base our installed factory already captured
# rather than wrapping the installed factory (which would recurse).
_existing_factory = logging.getLogRecordFactory()
if getattr(_existing_factory, "_pipeshub_trace", False):
    _base_record_factory = getattr(
        _existing_factory, "_pipeshub_base", logging.LogRecord
    )
else:
    _base_record_factory = _existing_factory

_record_factory = _make_record_factory(_base_record_factory)
if not getattr(_existing_factory, "_pipeshub_trace", False):
    logging.setLogRecordFactory(_record_factory)


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


class HealthCheckFilter(logging.Filter):
    """Suppress successful uvicorn access log entries for /health* endpoints.

    Failed health checks (non-2xx) are still logged so genuine service problems
    remain visible. Only 2xx responses are suppressed since those are routine
    process_monitor polling noise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn access log args: (client_addr, method, path, http_version, status_code)
        if isinstance(record.args, (tuple, list)) and len(record.args) >= 5:
            try:
                path = str(record.args[2])
                status_code = int(record.args[4])
                return not (path.startswith("/health") and 200 <= status_code < 300)
            except (ValueError, TypeError):
                pass
        # Fallback for any non-standard formatting
        msg = record.getMessage()
        return not ("/health" in msg and '" 2' in msg)


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
    format=LOG_FORMAT,
    encoding="utf-8",
)

# Suppress Neo4j notification warnings (missing labels, etc.)
data_store = os.getenv("DATA_STORE", "arangodb").lower()
if data_store == "neo4j":
    neo4j_notifications_logger = logging.getLogger("neo4j.notifications")
    neo4j_notifications_logger.setLevel(logging.ERROR)  # Only show errors, suppress warnings

# Suppress /health* endpoint noise from uvicorn access logs (process_monitor polling)
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())


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
        # Enhanced format: request id + thread/task + filename and line number
        log_format = LOG_FORMAT

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
