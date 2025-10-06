#!/usr/bin/env python3
"""
Test runner script for PipesHub AI integration tests.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple


def run_command(command: list[str], cwd: str | None = None) -> Tuple[bool, str, str]:
    """Run a command and return the result."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        return True, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout, e.stderr


def check_dependencies() -> bool:
    """Check if required dependencies are installed."""
    print("Checking dependencies...")
    
    # Check Python version
    if sys.version_info < (3, 11):
        print("Python 3.11+ is required")
        return False
    
    # Check if pytest is installed
    success, stdout, stderr = run_command("python -m pytest --version")
    if not success:
        print("pytest is not installed")
        print("Install with: pip install -r requirements-test.txt")
        return False
    
    # Check if Docker is available
    success, stdout, stderr = run_command("docker --version")
    if not success:
        print("Docker is not available - some tests may be skipped")
    
    print("Dependencies check completed")
    return True


def setup_test_environment() -> None:
    """Set up the test environment."""
    print("Setting up test environment...")
    
    # Create test directories
    test_dirs = ["tests/fixtures", "tests/utils", "test_results"]
    for dir_path in test_dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    # Set environment variables
    os.environ["PYTHONPATH"] = str(Path.cwd())
    
    print("Test environment setup completed")


def run_health_tests() -> bool:
    """Run health check tests."""
    print("Running health check tests")
    
    command = "python -m pytest tests/test_health_checks.py -v --tb=short"
    success, stdout, stderr = run_command(command)
    
    if success:
        print("Health check tests passed")
        return True
    else:
        print("Health check tests failed")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False


def run_indexing_tests() -> bool:
    """Run indexing tests."""
    print("Running indexing tests...")
    
    command = "python -m pytest tests/test_indexing.py -v --tb=short"
    success, stdout, stderr = run_command(command)
    
    if success:
        print("Indexing tests passed")
        return True
    else:
        print("Indexing tests failed")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False


def run_search_tests() -> bool:
    """Run search tests."""
    print("Running search tests...")
    
    command = "python -m pytest tests/test_search.py -v --tb=short"
    success, stdout, stderr = run_command(command)
    
    if success:
        print("Search tests passed")
        return True
    else:
        print("Search tests failed")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False


def run_integration_tests() -> bool:
    """Run integration tests."""
    print("🔗 Running integration tests...")
    
    command = "python -m pytest tests/test_api_integration.py -v --tb=short"
    success, stdout, stderr = run_command(command)
    
    if success:
        print("Integration tests passed")
        return True
    else:
        print("Integration tests failed")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False


def run_all_tests() -> bool:
    """Run all tests."""
    print("🧪 Running all tests...")
    
    command = "python -m pytest tests/ -v --tb=short"
    success, stdout, stderr = run_command(command)
    
    if success:
        print("All tests passed")
        return True
    else:
        print("Some tests failed")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False


def run_specific_tests(test_pattern: str) -> bool:
    """Run tests matching a specific pattern."""
    print(f"Running tests matching: {test_pattern}")
    
    command = f"python -m pytest tests/ -k {test_pattern} -v --tb=short"
    success, stdout, stderr = run_command(command)
    
    if success:
        print("Tests passed")
        return True
    else:
        print("Tests failed")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)
        return False


def main() -> None:
    """Main test runner function."""
    parser = argparse.ArgumentParser(description="PipesHub AI Integration Test Runner")
    parser.add_argument(
        "test_type",
        choices=["health", "indexing", "search", "integration", "all", "specific"],
        help="Type of tests to run"
    )
    parser.add_argument(
        "--pattern",
        help="Pattern for specific tests (used with 'specific' test type)"
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Skip dependency checks"
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Only set up the test environment"
    )
    
    args = parser.parse_args()
    
    print("PipesHub AI Integration Test Runner")
    print("=" * 50)
    
    # Check dependencies
    if not args.skip_deps:
        if not check_dependencies():
            sys.exit(1)
    
    # Setup test environment
    setup_test_environment()
    
    if args.setup_only:
        print("Test environment setup completed")
        return
    
    # Run tests based on type
    success = False
    
    if args.test_type == "health":
        success = run_health_tests()
    elif args.test_type == "indexing":
        success = run_indexing_tests()
    elif args.test_type == "search":
        success = run_search_tests()
    elif args.test_type == "integration":
        success = run_integration_tests()
    elif args.test_type == "all":
        success = run_all_tests()
    elif args.test_type == "specific":
        if not args.pattern:
            print("Pattern is required for specific tests")
            sys.exit(1)
        success = run_specific_tests(args.pattern)
    
    if success:
        print("\nAll tests completed successfully!")
        sys.exit(0)
    else:
        print("\nSome tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

