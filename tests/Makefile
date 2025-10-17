# Makefile for PipesHub Integration Tests
# Provides convenient commands for running tests

.PHONY: help install test test-verbose test-coverage test-integration test-auth test-parallel clean setup

# Default target
help:
	@echo "PipesHub Integration Tests"
	@echo ""
	@echo "Available targets:"
	@echo "  make setup            - Set up test environment"
	@echo "  make install          - Install test dependencies"
	@echo "  make test             - Run all tests"
	@echo "  make test-verbose     - Run tests with verbose output"
	@echo "  make test-coverage    - Run tests with coverage report"
	@echo "  make test-integration - Run only integration tests"
	@echo "  make test-auth        - Run only authentication tests"
	@echo "  make test-parallel    - Run tests in parallel"
	@echo "  make test-fast        - Run tests (skip slow tests)"
	@echo "  make test-report      - Generate HTML test report"
	@echo "  make clean            - Clean test artifacts"
	@echo "  make lint             - Run linter on tests"

# Setup test environment
setup:
	@echo "Setting up test environment..."
	@if [ ! -f .env ]; then cp env.template .env; echo "Created .env file"; fi
	@mkdir -p data logs reports
	@echo "Setup complete!"

# Install dependencies
install:
	@echo "Installing test dependencies..."
	pip install -r requirements.txt
	@echo "Dependencies installed!"

# Run all tests
test:
	pytest integration/ -v

# Run tests with verbose output
test-verbose:
	pytest integration/ -vv

# Run tests with coverage
test-coverage:
	pytest integration/ --cov=tests --cov-report=html --cov-report=term-missing
	@echo "Coverage report generated in htmlcov/index.html"

# Run only integration tests
test-integration:
	pytest -m integration -v

# Run only authentication tests
test-auth:
	pytest -m auth -v

# Run tests in parallel
test-parallel:
	pytest integration/ -n auto -v

# Run fast tests (skip slow tests)
test-fast:
	pytest -m "not slow" -v

# Run tests and generate HTML report
test-report:
	pytest integration/ --html=reports/test_report.html --self-contained-html
	@echo "Test report generated in reports/test_report.html"

# Run specific test file
test-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make test-file FILE=integration/test_example.py"; \
	else \
		pytest $(FILE) -v; \
	fi

# Run specific test
test-one:
	@if [ -z "$(TEST)" ]; then \
		echo "Usage: make test-one TEST=integration/test_example.py::test_example_get_request"; \
	else \
		pytest $(TEST) -vv; \
	fi

# Clean test artifacts
clean:
	@echo "Cleaning test artifacts..."
	rm -rf .pytest_cache
	rm -rf __pycache__
	rm -rf reports/*
	rm -rf htmlcov
	rm -rf .coverage
	rm -f coverage.xml
	rm -rf logs/*.log
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "Cleanup complete!"

# Lint tests
lint:
	@echo "Running linter..."
	ruff check .
	@echo "Linting complete!"

# Format tests
format:
	@echo "Formatting code..."
	ruff format .
	@echo "Formatting complete!"

# Watch mode (requires pytest-watch)
watch:
	@echo "Starting watch mode..."
	ptw integration/ -- -v

# Debug mode
debug:
	pytest integration/ -vv -s --tb=long

# List all tests
list:
	pytest --collect-only -q

# Show available markers
markers:
	pytest --markers

