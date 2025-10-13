"""
Tools Startup Integration
Simple integration script to warmup tools during application startup.
"""

import logging

from app.agents.tools.discovery import ToolsDiscovery
from app.agents.tools.registry import _global_tools_registry
from app.utils.logger import create_logger


def startup_tools_warmup(
    logger: logging.Logger
) -> dict:
    """
    Startup function to warmup all tools
    Args:
        logger: Optional logger instance. If not provided, creates a default one.
        log_level: Logging level for the default logger if one is created.
    Returns:
        Dict containing warmup results with keys:
        - total_tools: Number of tools registered
        - success_rate: Success rate of imports
        - imported_modules: List of successfully imported modules
        - failed_imports: List of failed imports
        - registered_tools: List of registered tool names
    """
    logger.info("Starting tools discovery during application startup...")

    try:
        # Create discovery instance and run
        discovery = ToolsDiscovery(logger)
        results = discovery.discover_all_tools()

        # Log startup summary
        logger.info(f"Tools startup completed: {results['total_tools']} tools registered")

        if results['failed_imports']:
            logger.warning(f"{len(results['failed_imports'])} imports failed during startup")

        return results

    except Exception as e:
        logger.error(f"Error during tools startup: {e}")
        # Return empty results on error
        return {
            "total_tools": 0,
            "success_rate": 0.0,
            "imported_modules": [],
            "failed_imports": [str(e)],
            "registered_tools": []
        }


def verify_tools_ready() -> bool:
    """
    Verify that tools are ready for use
    Returns:
        True if tools are available, False otherwise
    """
    try:
        tool_count = len(_global_tools_registry.list_tools())
        return tool_count > 0
    except Exception:
        return False


def get_startup_summary() -> dict:
    """
    Get a summary of the current tools state
    Returns:
        Dict with current tools information
    """
    try:
        tools = _global_tools_registry.list_tools()
        return {
            "total_tools": len(tools),
            "available_tools": tools,
            "is_ready": len(tools) > 0
        }
    except Exception:
        return {
            "total_tools": 0,
            "available_tools": [],
            "is_ready": False
        }


# Convenience function for quick startup
def quick_startup() -> dict:
    """
    Quick startup with default settings
    Returns:
        Warmup results
    """
    return startup_tools_warmup(create_logger("tools_startup"))
