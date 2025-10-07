"""
Tool discovery system with strategy pattern for flexible discovery.
"""

import importlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List

from app.agents.tools.config import ToolDiscoveryConfig
from app.agents.tools.registry import _global_tools_registry


class ModuleImporter:
    """Handles dynamic module importing with error tracking"""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.imported_modules: List[str] = []
        self.failed_imports: List[str] = []

    def import_module(self, module_path: str) -> bool:
        """
        Import a module and track success/failure.

        Args:
            module_path: Full module path to import

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.debug(f"Importing module: {module_path}")
            importlib.import_module(module_path)
            self.imported_modules.append(module_path)
            return True
        except ImportError as e:
            self.logger.warning(f"Failed to import {module_path}: {e}")
            self.failed_imports.append(f"{module_path}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Error importing {module_path}: {e}")
            self.failed_imports.append(f"{module_path}: {e}")
            return False

    def import_modules(self, module_paths: List[str]) -> Dict[str, Any]:
        """
        Import multiple modules.
        Args:
            module_paths: List of module paths to import

        Returns:
            Dictionary with import results
        """
        for path in module_paths:
            self.import_module(path)

        return {
            "imported": self.imported_modules,
            "failed": self.failed_imports,
            "success_rate": (
                len(self.imported_modules) / len(module_paths)
                if module_paths else 0
            )
        }


class DiscoveryStrategy(ABC):
    """Abstract base class for tool discovery strategies"""

    @abstractmethod
    def discover(self, base_dir: Path, importer: ModuleImporter) -> List[str]:
        """
        Discover and return module paths to import.

        Args:
            base_dir: Base directory to search
            importer: Module importer instance

        Returns:
            List of module paths to import
        """
        pass


class SimpleAppDiscovery(DiscoveryStrategy):
    """Discovery strategy for simple apps with flat structure"""

    def __init__(self, app_name: str) -> None:
        self.app_name = app_name

    def discover(self, base_dir: Path, importer: ModuleImporter) -> List[str]:
        """Discover tools in a simple app structure"""
        app_dir = base_dir / self.app_name
        if not app_dir.exists():
            return []

        modules = []

        # Import main module if exists
        main_module = app_dir / f"{self.app_name}.py"
        if main_module.exists():
            modules.append(f"app.agents.actions.{self.app_name}.{self.app_name}")

        # Import other Python files
        for py_file in app_dir.glob("*.py"):
            if py_file.name not in ToolDiscoveryConfig.SKIP_FILES:
                module_name = py_file.stem
                modules.append(f"app.agents.actions.{self.app_name}.{module_name}")

        return modules


class NestedAppDiscovery(DiscoveryStrategy):
    """Discovery strategy for apps with nested structure (Google, Microsoft)"""

    def __init__(self, app_name: str, subdirs: List[str]) -> None:
        self.app_name = app_name
        self.subdirs = subdirs

    def discover(self, base_dir: Path, importer: ModuleImporter) -> List[str]:
        """Discover tools in a nested app structure"""
        app_dir = base_dir / self.app_name
        if not app_dir.exists():
            return []

        modules = []
        for subdir in self.subdirs:
            subdir_path = app_dir / subdir
            if subdir_path.exists():
                main_file = subdir_path / f"{subdir}.py"
                if main_file.exists():
                    modules.append(
                        f"app.agents.actions.{self.app_name}.{subdir}.{subdir}"
                    )

        return modules


class ToolsDiscovery:
    """Enhanced discovery class with strategy pattern"""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.importer = ModuleImporter(logger)
        self.strategies: Dict[str, DiscoveryStrategy] = {}
        self._initialize_strategies()

    def _initialize_strategies(self) -> None:
        """Initialize discovery strategies for each app"""
        for app_name, config in ToolDiscoveryConfig.APP_CONFIGS.items():
            if config.subdirectories:
                self.strategies[app_name] = NestedAppDiscovery(
                    app_name, config.subdirectories
                )
            else:
                self.strategies[app_name] = SimpleAppDiscovery(app_name)

    def discover_all_tools(self) -> Dict[str, Any]:
        """
        Discover and import all tools.

        Returns:
            Dictionary with discovery results
        """
        self.logger.info("Starting tools discovery process")

        actions_dir = Path(__file__).parent.parent / "actions"
        if not actions_dir.exists():
            self.logger.error(f"Actions directory not found: {actions_dir}")
            return self._get_discovery_results()

        # Discover tools for each configured app
        for app_name, config in ToolDiscoveryConfig.APP_CONFIGS.items():
            if not config.enabled:
                self.logger.info(f"Skipping disabled app: {app_name}")
                continue

            self._discover_app(app_name, actions_dir)

        self._log_results()
        return self._get_discovery_results()

    def _discover_app(self, app_name: str, actions_dir: Path) -> None:
        """Discover tools for a specific app"""
        self.logger.info(f"Processing app: {app_name}")

        strategy = self.strategies.get(app_name)
        if not strategy:
            self.logger.warning(f"No discovery strategy for app: {app_name}")
            return

        modules = strategy.discover(actions_dir, self.importer)
        for module in modules:
            self.importer.import_module(module)

    def _log_results(self) -> None:
        """Log discovery results"""
        registered_tools = _global_tools_registry.list_tools()

        self.logger.info("Tools discovery completed!")
        self.logger.info(f"Total tools registered: {len(registered_tools)}")
        self.logger.info(f"Modules imported: {len(self.importer.imported_modules)}")

        if self.importer.failed_imports:
            self.logger.warning(
                f"Failed imports: {len(self.importer.failed_imports)}"
            )
            for failure in self.importer.failed_imports[:5]:  # Show first 5
                self.logger.warning(f"  - {failure}")

    def _get_discovery_results(self) -> Dict[str, Any]:
        """Get discovery results"""
        registered_tools = _global_tools_registry.list_tools()
        total_attempts = (
            len(self.importer.imported_modules) +
            len(self.importer.failed_imports)
        )

        return {
            "imported_modules": self.importer.imported_modules,
            "failed_imports": self.importer.failed_imports,
            "registered_tools": registered_tools,
            "total_tools": len(registered_tools),
            "success_rate": (
                len(self.importer.imported_modules) / total_attempts
                if total_attempts > 0 else 0
            )
        }


def discover_tools(logger: logging.Logger) -> Dict[str, Any]:
    """
    Convenience function to discover all tools.

    Args:
        logger: Logger instance
    Returns:
        Dictionary with discovery results
    """
    discovery = ToolsDiscovery(logger)
    return discovery.discover_all_tools()
