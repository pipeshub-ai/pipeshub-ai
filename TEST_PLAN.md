# PipesHub AI Integration Test Plan

## Overview
This document outlines the integration testing strategy for PipesHub AI, a workplace AI platform with microservices architecture. The focus is on P0 (Priority 0) workflows that are critical for basic functionality.

## System Architecture
- **Frontend**: React/Next.js (port 3001)
- **Node.js Backend**: API server (port 3000)
- **Python Microservices**:
  - Connectors (port 8088) - Data source connections
  - Indexing (port 8091) - Document processing and indexing
  - Query (port 8000) - Search and retrieval
  - Docling (port 8092) - Document conversion

## P0 Test Categories

### 1. Health Check Tests (P0)
**Objective**: Verify all services are running and responding correctly

**Test Cases**:
- Node.js backend health check (`/api/v1/health`)
- Python services health checks (`/health`)
  - Query service health
  - Indexing service health
  - Connector service health
  - Docling service health
- Database connectivity checks (MongoDB, ArangoDB, Redis)
- Service dependency validation

**Success Criteria**:
- All services return HTTP 200 with "healthy" status
- Database connections are established
- Service dependencies are accessible

### 2. Document Indexing Workflow (P0)
**Objective**: Test the complete document ingestion and indexing pipeline

**Test Cases**:
- Upload a test document via API
- Verify document is processed by indexing service
- Confirm document appears in search results
- Validate document metadata is stored correctly

**Success Criteria**:
- Document is successfully uploaded
- Document is indexed and searchable
- Metadata is correctly stored in databases

### 3. Search and Query Workflow (P0)
**Objective**: Test semantic search functionality

**Test Cases**:
- Perform basic text search
- Test search with filters
- Verify search results contain expected content
- Test query transformation and expansion

**Success Criteria**:
- Search returns relevant results
- Filters work correctly
- Query processing functions properly

### 4. API Integration Tests (P0)
**Objective**: Test API endpoints and service communication

**Test Cases**:
- Test authentication endpoints
- Verify service-to-service communication
- Test error handling and response codes
- Validate API response formats

**Success Criteria**:
- APIs return expected responses
- Service communication is reliable
- Error handling is appropriate

## Test Environment Setup

### Minimal Infrastructure Requirements
**Docker Containers** (Required for P0):
- Redis (port 6379)
- MongoDB (port 27017)
- ArangoDB (port 8529)
- Qdrant (port 6333)

**Optional for P0**:
- ETCD (port 2379)
- Kafka + Zookeeper (ports 9092, 2181)

### Service Dependencies
1. **Node.js Backend** requires: Redis, MongoDB, ArangoDB
2. **Query Service** requires: ArangoDB, Qdrant, Connector service
3. **Indexing Service** requires: ArangoDB, Qdrant, Connector service
4. **Connector Service** requires: ArangoDB, Redis

## Test Data Strategy

### Test Documents
- Simple text files (.txt)
- PDF documents (basic, no OCR required)
- Markdown files
- JSON documents

### Test Queries
- Simple keyword searches
- Phrase searches
- Filtered searches by document type
- Complex queries with multiple terms

## Test Framework

### Technology Stack
- **Language**: Python 3.11+
- **Testing Framework**: pytest
- **HTTP Client**: httpx (async)
- **Test Data**: pytest fixtures
- **Mocking**: pytest-mock (for external dependencies)

### Test Structure
```
tests/
├── conftest.py              # Test configuration and fixtures
├── test_health_checks.py    # Health check tests
├── test_indexing.py         # Document indexing tests
├── test_search.py           # Search functionality tests
├── test_api_integration.py  # API integration tests
├── fixtures/                # Test data and fixtures
│   ├── test_documents/      # Sample documents
│   └── test_configs.py      # Test configurations
└── utils/                   # Test utilities
    ├── docker_helpers.py    # Docker container management
    └── api_helpers.py       # API testing utilities
```

## Test Execution Strategy

### Phase 1: Infrastructure Setup
1. Start required Docker containers
2. Verify container health
3. Start backend services
4. Verify service health

### Phase 2: Basic Functionality
1. Run health check tests
2. Test document upload and indexing
3. Test basic search functionality
4. Verify API responses

### Phase 3: Integration Testing
1. Test end-to-end workflows
2. Test error scenarios
3. Test service communication
4. Validate data consistency

## Success Metrics

### P0 Success Criteria
- All health checks pass
- Document indexing works end-to-end
- Search returns relevant results
- APIs respond correctly
- No critical errors in logs

### Test Coverage Goals
- Health check endpoints: 100%
- Core indexing workflow: 80%
- Search functionality: 80%
- API endpoints: 70%

## Risk Mitigation

### Known Limitations
- OCR functionality not tested (requires Tesseract)
- Complex document processing not covered
- Performance testing not included
- Load testing not included

### Fallback Strategies
- Mock external dependencies if services unavailable
- Use test data instead of real documents
- Skip tests that require unavailable services
- Provide clear error messages for test failures

## Future Enhancements (P1+)

### P1 Test Categories
- Authentication and authorization tests
- User management workflows
- Knowledge base management
- Advanced search features

### P2 Test Categories
- OCR document processing
- Real-time synchronization
- Performance and load testing
- Security testing

## Maintenance

### Test Data Management
- Regular cleanup of test data
- Version control of test documents
- Update test configurations as needed

### Test Environment
- Automated setup scripts
- Environment validation
- Service dependency management

---

**Note**: This test plan focuses on P0 functionality to establish a solid foundation for integration testing. Additional test categories can be added as the system matures and more complex workflows are implemented.

