# Organization Management Integration Tests

This directory contains integration tests for the PipesHub organization management API.

## Overview

The integration tests validate the complete flow of organization creation including:
1. API endpoint calls to create organizations
2. MongoDB persistence validation
3. Kafka event publishing and processing
4. ArangoDB vertex creation
5. Data validation across all systems

## Prerequisites

Before running these tests, ensure you have the following services running:

### Required Services
- **Node.js Backend API** (port 3000)
- **MongoDB** (port 27017)
- **ArangoDB** (port 8529)
- **Kafka** (port 9092)
- **Redis** (port 6379)
- **Qdrant** (port 6333, optional)

### Environment Setup

1. Install Python dependencies:
```bash
cd tests
pip install -r requirements.txt
```

2. Configure environment variables:
```bash
cp env.template .env
# Edit .env with your local configuration
```

## Running the Tests

### Run all organization tests:
```bash
pytest integration/backend/nodejs/apps/user_org_management/
```

### Run a specific test:
```bash
pytest integration/backend/nodejs/apps/user_org_management/test_org_creation.py::test_create_business_org
```

### Run with verbose output:
```bash
pytest integration/backend/nodejs/apps/user_org_management/ -v
```

### Run with coverage:
```bash
pytest integration/backend/nodejs/apps/user_org_management/ --cov --cov-report=html
```

## Test Structure

### Database Interfaces (`tests/utils/db_interfaces.py`)
- `MongoInterface`: MongoDB interactions
- `ArangoInterface`: ArangoDB vertex queries
- `QdrantInterface`: Qdrant collection management
- `RedisInterface`: Redis key-value operations

### Test Cases

#### 1. `test_create_business_org`
Validates business organization creation:
- POST request to create org
- MongoDB entry creation
- ArangoDB vertex creation (via Kafka processing)
- Data integrity validation

#### 2. `test_create_individual_org`
Validates individual organization creation:
- POST request to create org
- MongoDB entry creation

#### 3. `test_create_org_with_missing_fields`
Validates API returns 400 for missing required fields

#### 4. `test_create_org_with_invalid_email`
Validates email format validation

#### 5. `test_create_org_password_validation`
Validates password strength requirements

## Test Data

Test data is generated using the Faker library to ensure uniqueness across test runs. The test data includes:

### Business Organization:
- `accountType`: "business"
- `registeredName`: Company name (required)
- `shortName`: Company suffix
- `contactEmail`: Valid email
- `adminFullName`: Admin name
- `password`: Strong password with special characters
- `permanentAddress`: Complete address details

### Individual Organization:
- `accountType`: "individual"
- `contactEmail`: Valid email
- `adminFullName`: Admin name
- `password`: Strong password with special characters

## Validation Points

Each test validates:

1. **API Response**: Status code and response structure
2. **MongoDB**: Document existence and field values
3. **ArangoDB**: Vertex creation and properties (with 2s delay for Kafka processing)
4. **Data Consistency**: Field values match across systems

## Notes

- The tests include a 2-second delay to allow Kafka to process events and create ArangoDB vertices
- MongoDB ObjectId comparison may require special handling
- Authentication is not currently tested (GET endpoint requires auth token)
- Test data is automatically cleaned up after each test

## Troubleshooting

### MongoDB Connection Issues
- Ensure MongoDB is running on port 27017
- Check authentication credentials in `.env`

### ArangoDB Connection Issues
- Ensure ArangoDB is running on port 8529
- Verify database `es` exists
- Check username and password in `.env`

### Kafka Processing Delays
- Increase the `time.sleep(2)` delay if Kafka processing is slow
- Check Kafka logs for consumer group issues

### Test Failures
- Ensure all required services are running
- Check network connectivity to localhost ports
- Review test logs for detailed error messages

## Contributing

When adding new tests:
1. Follow the existing test structure
2. Use fixtures for database connections
3. Clean up test data in fixtures
4. Add descriptive docstrings
5. Use Faker for generating test data

