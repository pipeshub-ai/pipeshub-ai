# PipesHub AI Integration Testing

This directory contains integration tests for the PipesHub AI platform. The tests are designed to verify the functionality of the microservices architecture and ensure proper communication between components.

## Overview

The test suite covers the following P0 (Priority 0) workflows:
- **Health Checks**: Verify all services are running and responding
- **Document Indexing**: Test document upload and processing pipeline
- **Search Functionality**: Test semantic search and query processing
- **API Integration**: Test service-to-service communication

## Test Architecture

```
tests/
├── conftest.py              # Test configuration and fixtures
├── test_health_checks.py    # Health check tests
├── test_indexing.py         # Document indexing tests
├── test_search.py           # Search functionality tests
├── test_api_integration.py  # API integration tests
├── fixtures/                # Test data and configurations
│   ├── test_documents/      # Sample documents
│   └── test_configs.py      # Test configurations
└── utils/                   # Test utilities
    ├── docker_helpers.py    # Docker container management
    └── api_helpers.py       # API testing utilities
```

## Prerequisites

### System Requirements
- Python 3.11+
- Docker Desktop
- Node.js v22.15.0 (for running services)
- Git

### Dependencies
Install test dependencies:
```bash
pip install -r requirements-test.txt
```

## Quick Start

### 1. Start Required Services

#### Option A: Using Docker Compose (Recommended)
```bash
# Start minimal infrastructure
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
docker run -d --name mongodb --restart always -p 27017:27017 -e MONGO_INITDB_ROOT_USERNAME=admin -e MONGO_INITDB_ROOT_PASSWORD=password mongo:8.0.6
docker run -d --name arangodb --restart always -p 8529:8529 -e ARANGO_ROOT_PASSWORD=test_password arangodb:3.12.4
docker run -d --name qdrant --restart always -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=test_qdrant_key qdrant/qdrant:v1.13.6
```

#### Option B: Using Docker Compose
```bash
cd deployment/docker-compose
docker compose -f docker-compose.dev.yml up -d
```

### 2. Start Backend Services

#### Node.js Backend
```bash
cd backend/nodejs/apps
cp ../../env.template .env
npm install
npm run dev
```

#### Python Services
```bash
cd backend/python
cp ../env.template .env
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements-test.txt
python -m app.query_main &
python -m app.indexing_main &
python -m app.connectors_main &
```

### 3. Run Tests

#### Run All Tests
```bash
python run_tests.py all
```

#### Run Specific Test Categories
```bash
# Health checks only
python run_tests.py health

# Document indexing tests
python run_tests.py indexing

# Search functionality tests
python run_tests.py search

# API integration tests
python run_tests.py integration
```

#### Run Tests with Pytest Directly
```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_health_checks.py -v

# Tests matching pattern
pytest tests/ -k "health" -v

# With coverage
pytest tests/ --cov=backend --cov-report=html
```

### Strict Mode (service availability enforcement)

By default, tests may skip when dependent services are unavailable (P0 flexibility). Enable strict mode to FAIL if services are down:

```bash
# Unix/macOS
export TEST_STRICT_SERVICES=1
pytest -q

# Windows PowerShell
$env:TEST_STRICT_SERVICES = "1"
pytest -q

# Windows cmd
set TEST_STRICT_SERVICES=1
pytest -q
```

## Test Categories

### Health Check Tests (`test_health_checks.py`)
- Verify all services are running
- Check database connectivity
- Validate service dependencies
- Test response times

### Indexing Tests (`test_indexing.py`)
- Document upload and processing
- Metadata validation
- Indexing pipeline verification
- Error handling

### Search Tests (`test_search.py`)
- Basic search functionality
- Search with filters
- Query validation
- Response format verification

### Integration Tests (`test_api_integration.py`)
- Service-to-service communication
- API response consistency
- Error handling across services
- Performance testing

## Configuration

### Environment Variables
The tests use the following environment variables:
- `NODEJS_BACKEND_URL`: http://localhost:3000
- `QUERY_SERVICE_URL`: http://localhost:8000
- `INDEXING_SERVICE_URL`: http://localhost:8091
- `CONNECTOR_SERVICE_URL`: http://localhost:8088
- `DOCLING_SERVICE_URL`: http://localhost:8092
- `TEST_STRICT_SERVICES`: set to `1` to fail tests when services are unavailable; default is non-strict (skips allowed)

### Test Configuration
Modify `tests/fixtures/test_configs.py` to adjust:
- Service URLs and ports
- Timeout values
- Test data
- Performance thresholds

## Test Data

### Sample Documents
Located in `tests/fixtures/test_documents/`:
- `sample.txt`: Basic text document
- Additional documents can be added for testing

### Test Configurations
Located in `tests/fixtures/test_configs.py`:
- Service configurations
- Database settings
- Test data definitions
- Performance limits

## Troubleshooting

### Common Issues

#### Services Not Starting
```bash
# Check if ports are available
netstat -tulpn | grep :3000
netstat -tulpn | grep :8000
netstat -tulpn | grep :8091
netstat -tulpn | grep :8088

# Check Docker containers
docker ps
docker logs <container_name>
```

#### Test Failures
```bash
# Run tests with verbose output
pytest tests/ -v -s

# Run specific test with debugging
pytest tests/test_health_checks.py::TestHealthChecks::test_nodejs_backend_health -v -s

# Check test logs
tail -f test_results.log
```

#### Database Connection Issues
```bash
# Check MongoDB
docker exec -it pipeshub-test-mongodb mongosh --username admin --password password

# Check ArangoDB
curl http://localhost:8529/_api/version

# Check Redis
docker exec -it pipeshub-test-redis redis-cli ping

# Check Qdrant
curl http://localhost:6333/health
```

### Debug Mode
Run tests with debug logging:
```bash
pytest tests/ -v -s --log-cli-level=DEBUG
```

## Continuous Integration

### GitHub Actions
The tests can be integrated into CI/CD pipelines:

```yaml
name: Integration Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements-test.txt
      - name: Start services
        run: |
          # Start Docker containers
          docker run -d --name redis -p 6379:6379 redis:bookworm
          # ... other containers
      - name: Run tests
        run: python run_tests.py all
```

## Contributing

### Adding New Tests
1. Create test file in appropriate directory
2. Follow naming convention: `test_*.py`
3. Use appropriate markers: `@pytest.mark.health`, `@pytest.mark.integration`
4. Add fixtures to `conftest.py` if needed
5. Update documentation

### Test Guidelines
- Use descriptive test names
- Include docstrings explaining test purpose
- Use fixtures for common setup/teardown
- Mock external dependencies when appropriate
- Include both positive and negative test cases
- Test error conditions and edge cases

## Performance Testing

### Load Testing
```bash
# Run performance tests
pytest tests/ -m performance -v

# Run load tests
pytest tests/test_api_integration.py::TestAPIPerformance -v
```

### Memory Testing
```bash
# Run memory tests
pytest tests/ -m memory -v
```

## Reporting

### Test Reports
- HTML report: `pytest tests/ --html=report.html`
- JSON report: `pytest tests/ --json-report --json-report-file=report.json`
- Coverage report: `pytest tests/ --cov=backend --cov-report=html`

### Logs
- Test results: `test_results.log`
- Service logs: `service_logs.log`
- Error logs: `error_logs.log`

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review test logs
3. Check service health
4. Open an issue in the repository
5. Join the Discord channel: https://discord.com/invite/K5RskzJBm2

