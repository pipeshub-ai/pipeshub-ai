"""
Tools Discovery Class
Ensures all tools are imported and registered in the tool registry during application startup.
"""

import importlib
import logging
from pathlib import Path
from typing import Any, Dict, List

from app.agents.tools.registry import _global_tools_registry


class ToolsDiscovery:
    """Discovery class to ensure all tools are imported and registered"""

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the discovery class"""
        self.logger = logger
        self.imported_modules: List[str] = []
        self.failed_imports: List[str] = []
        self.registered_tools: List[str] = []

    def discover_all_tools(self) -> Dict[str, Any]:
        """
        Import all available tool modules to ensure tools are registered
        Returns:
            Dict containing discovery results
        """
        self.logger.info("Starting tools discovery process")

        # Get the actions directory path
        actions_dir = Path(__file__).parent.parent / "actions"

        if not actions_dir.exists():
            self.logger.error(f"Actions directory not found: {actions_dir}")
            return self._get_discovery_results()

        # Discover and import all action modules
        self._discover_and_import_actions(actions_dir)

        # Log results
        self._log_discovery_results()

        return self._get_discovery_results()

    def _discover_and_import_actions(self, actions_dir: Path) -> None:
        """Discover and import all action modules"""
        self.logger.info(f"Discovering actions in: {actions_dir}")

        # Get all subdirectories (each represents an app)
        app_dirs = [d for d in actions_dir.iterdir() if d.is_dir() and not d.name.startswith('__')]

        for app_dir in app_dirs:
            app_name = app_dir.name
            self.logger.info(f"Processing app: {app_name}")

            # Import the main app module if it exists
            main_module_path = app_dir / f"{app_name}.py"
            if main_module_path.exists():
                self._import_module(f"app.agents.actions.{app_name}.{app_name}")

            # Handle special cases for nested structures
            if app_name == "google":
                self._import_google_tools(app_dir)
            else:
                # Import all Python files in the app directory
                self._import_python_files(app_dir, f"app.agents.actions.{app_name}")

    def _import_google_tools(self, google_dir: Path) -> None:
        """Import Google tools which have a nested structure"""
        google_subdirs = ["gmail", "google_calendar", "google_drive", "auth", "enterprise"]

        for subdir in google_subdirs:
            subdir_path = google_dir / subdir
            if subdir_path.exists():
                main_file = subdir_path / f"{subdir}.py"
                if main_file.exists():
                    module_path = f"app.agents.actions.google.{subdir}.{subdir}"
                    self._import_module(module_path)

    def _import_python_files(self, app_dir: Path, base_module_path: str) -> None:
        """Import all Python files in an app directory"""
        for py_file in app_dir.glob("*.py"):
            if py_file.name.startswith("__") or py_file.name == "config.py":
                continue

            module_name = py_file.stem
            module_path = f"{base_module_path}.{module_name}"
            self._import_module(module_path)

    def _import_module(self, module_path: str) -> None:
        """Import a specific module and handle any errors"""
        try:
            self.logger.debug(f"Importing module: {module_path}")
            importlib.import_module(module_path)
            self.imported_modules.append(module_path)
            self.logger.info(f"Successfully imported: {module_path}")
        except ImportError as e:
            self.logger.warning(f"Failed to import {module_path}: {e}")
            self.failed_imports.append(f"{module_path}: {e}")
        except Exception as e:
            self.logger.error(f"Error importing {module_path}: {e}")
            self.failed_imports.append(f"{module_path}: {e}")

    def _log_discovery_results(self) -> None:
        """Log the results of the discovery process"""
        # Get registered tools
        self.registered_tools = _global_tools_registry.list_tools()

        if self.failed_imports:
            self.logger.info("Failed imports:")
            for failure in self.failed_imports:
                self.logger.info(f"      - {failure}")

        self.logger.info("Tools discovery completed!")

    def _get_discovery_results(self) -> Dict[str, Any]:
        """Get the discovery results as a dictionary"""
        return {
            "imported_modules": self.imported_modules,
            "failed_imports": self.failed_imports,
            "registered_tools": self.registered_tools,
            "total_tools": len(self.registered_tools),
            "success_rate": len(self.imported_modules) / (len(self.imported_modules) + len(self.failed_imports)) if (len(self.imported_modules) + len(self.failed_imports)) > 0 else 0
        }

    def get_registered_tools(self) -> List[str]:
        """Get list of all registered tools"""
        return _global_tools_registry.list_tools()

    def get_tool_count(self) -> int:
        """Get the total number of registered tools"""
        return len(_global_tools_registry.list_tools())

    def is_tool_registered(self, app_name: str, tool_name: str) -> bool:
        """Check if a specific tool is registered"""
        return _global_tools_registry.get_tool(app_name, tool_name) is not None


def discover_tools(logger: logging.Logger) -> Dict[str, Any]:
    """
    Convenience function to discover all tools
    Args:
        logger: Optional logger instance
    Returns:
        Dict containing warmup results
    """
    discovery = ToolsDiscovery(logger)
    return discovery.discover_all_tools()
