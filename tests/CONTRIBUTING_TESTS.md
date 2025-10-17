# Contributing to Integration Tests

Thank you for contributing to PipesHub integration tests! This guide will help you write high-quality tests that follow our standards.

## 🎯 Test Writing Guidelines

### Test Structure

Follow the Arrange-Act-Assert (AAA) pattern:

```python
def test_create_user(http_client):
    # ARRANGE: Set up test data
    user_data = TestDataFactory.user()
    
    # ACT: Perform the action
    response = http_client.post("/api/users", json=user_data)
    
    # ASSERT: Verify the results
    assert_response(response) \
        .assert_status(201) \
        .assert_json_contains({"username": user_data["username"]})
```

### Naming Conventions

**Test Files**: `test_<feature>.py`
- ✅ `test_users.py`
- ✅ `test_authentication.py`
- ❌ `users_test.py`

**Test Functions**: `test_<what>_<condition>_<expected>`
- ✅ `test_create_user_with_valid_data_returns_201()`
- ✅ `test_get_user_not_found_returns_404()`
- ❌ `test_user()`

**Test Classes**: `Test<Feature>`
- ✅ `TestUserAPI`
- ✅ `TestAuthentication`

### Docstrings

Every test should have a docstring explaining what it tests:

```python
def test_create_user_with_duplicate_email(http_client):
    """
    Test that creating a user with an existing email returns 400.
    
    This verifies the uniqueness constraint on email addresses.
    """
    # Test implementation
```

### Markers

Use appropriate markers to categorize tests:

```python
@pytest.mark.integration  # All integration tests
@pytest.mark.auth         # Authentication-related
@pytest.mark.slow         # Tests that take >5 seconds
@pytest.mark.external     # Tests calling external services
@pytest.mark.database     # Tests requiring database
@pytest.mark.smoke        # Critical functionality tests
```

### Test Independence

Each test must be independent and not rely on other tests:

❌ **Bad**:
```python
def test_create_user(http_client):
    http_client.post("/api/users", json={"id": 1, "name": "Test"})

def test_update_user(http_client):
    # This assumes test_create_user ran first!
    http_client.put("/api/users/1", json={"name": "Updated"})
```

✅ **Good**:
```python
def test_update_user(http_client):
    # Setup within the test
    user = http_client.post("/api/users", json={"name": "Test"}).json()
    
    # Test
    response = http_client.put(f"/api/users/{user['id']}", json={"name": "Updated"})
    assert_response(response).assert_status(200)
    
    # Cleanup
    http_client.delete(f"/api/users/{user['id']}")
```

### Use Fixtures for Setup/Teardown

Create fixtures for reusable setup:

```python
@pytest.fixture
def test_user(http_client):
    """Create a test user and clean up after."""
    user_data = TestDataFactory.user()
    response = http_client.post("/api/users", json=user_data)
    user = response.json()
    
    yield user
    
    # Cleanup
    http_client.delete(f"/api/users/{user['id']}")


def test_get_user(http_client, test_user):
    """Test fetching a user."""
    response = http_client.get(f"/api/users/{test_user['id']}")
    assert_response(response).assert_status(200)
```

### Error Cases

Always test both success and failure scenarios:

```python
def test_create_user_success(http_client):
    """Test successful user creation."""
    user_data = TestDataFactory.user()
    response = http_client.post("/api/users", json=user_data)
    assert_response(response).assert_status(201)


def test_create_user_invalid_email(http_client):
    """Test user creation with invalid email fails."""
    user_data = TestDataFactory.user(email="invalid")
    response = http_client.post("/api/users", json=user_data)
    assert_response(response).assert_status(400)


def test_create_user_duplicate_email(http_client, test_user):
    """Test user creation with duplicate email fails."""
    user_data = TestDataFactory.user(email=test_user["email"])
    response = http_client.post("/api/users", json=user_data)
    assert_response(response).assert_status(409)
```

### Data Generation

Use TestDataFactory instead of hardcoding test data:

❌ **Bad**:
```python
def test_create_user(http_client):
    response = http_client.post("/api/users", json={
        "username": "testuser123",
        "email": "test@example.com",
        "password": "password123"
    })
```

✅ **Good**:
```python
def test_create_user(http_client):
    user_data = TestDataFactory.user()
    response = http_client.post("/api/users", json=user_data)
```

### Assertions

Use the fluent assertion API for readability:

```python
# Chain multiple assertions
assert_response(response) \
    .assert_status(200) \
    .assert_content_type("application/json") \
    .assert_json_schema(["id", "username", "email"]) \
    .assert_json_contains({"active": True})
```

## 📋 Checklist for New Tests

Before submitting, ensure your test:

- [ ] Has a descriptive name
- [ ] Has a docstring explaining what it tests
- [ ] Is independent (doesn't rely on other tests)
- [ ] Uses appropriate markers
- [ ] Uses fixtures for setup/teardown
- [ ] Uses TestDataFactory for data generation
- [ ] Tests both success and failure cases
- [ ] Cleans up test data
- [ ] Follows AAA pattern
- [ ] Has proper assertions
- [ ] Runs successfully in isolation
- [ ] Runs successfully with other tests

## 🏗️ Adding New Test Modules

When adding a new test module:

1. **Create the test file** in `tests/integration/`:
   ```bash
   touch tests/integration/test_new_feature.py
   ```

2. **Add module docstring**:
   ```python
   """
   Integration tests for the New Feature API.
   
   Tests cover:
   - Feature creation
   - Feature retrieval
   - Feature updates
   - Feature deletion
   - Error handling
   """
   ```

3. **Import required utilities**:
   ```python
   import pytest
   from tests.utils.assertions import assert_response
   from tests.utils.test_data_factory import TestDataFactory
   ```

4. **Add common fixtures** if needed:
   ```python
   @pytest.fixture
   def test_feature(http_client):
       """Create test feature and clean up."""
       # Implementation
       pass
   ```

5. **Write tests** following the guidelines above

6. **Run your tests**:
   ```bash
   pytest tests/integration/test_new_feature.py -v
   ```

## 🔧 Adding New Fixtures

When adding reusable fixtures:

1. **Choose the right location**:
   - Global fixtures → `tests/conftest.py`
   - HTTP fixtures → `tests/fixtures/http_fixtures.py`
   - Auth fixtures → `tests/fixtures/auth_fixtures.py`
   - Database fixtures → `tests/fixtures/database_fixtures.py`

2. **Document the fixture**:
   ```python
   @pytest.fixture(scope="function")
   def my_fixture(http_client):
       """
       Brief description of the fixture.
       
       Args:
           http_client: HTTP client fixture
           
       Yields:
           Description of what is yielded
           
       Example:
           def test_example(my_fixture):
               # Use fixture
               pass
       """
       # Setup
       resource = create_resource()
       
       yield resource
       
       # Teardown
       cleanup_resource(resource)
   ```

3. **Choose appropriate scope**:
   - `function` - New instance for each test (default)
   - `class` - One instance per test class
   - `module` - One instance per test module
   - `session` - One instance for entire test session

## 🛠️ Adding New Utilities

When adding new utility functions:

1. **Choose the right module**:
   - HTTP client utilities → `tests/utils/http_client.py`
   - Assertions → `tests/utils/assertions.py`
   - Test data → `tests/utils/test_data_factory.py`
   - General helpers → `tests/utils/helpers.py`

2. **Document thoroughly**:
   ```python
   def my_helper_function(param1: str, param2: int) -> bool:
       """
       Brief description of what the function does.
       
       Args:
           param1: Description of param1
           param2: Description of param2
           
       Returns:
           Description of return value
           
       Raises:
           ValueError: When param1 is invalid
           
       Example:
           result = my_helper_function("test", 42)
           assert result is True
       """
       # Implementation
   ```

3. **Add tests** for the utility:
   ```python
   # In tests/utils/test_helpers.py
   def test_my_helper_function():
       """Test my_helper_function with valid input."""
       result = my_helper_function("test", 42)
       assert result is True
   ```

## 🎨 Code Style

Follow these style guidelines:

1. **Use type hints**:
   ```python
   def create_user(username: str, email: str) -> Dict[str, Any]:
       pass
   ```

2. **Follow Python naming conventions**:
   - `snake_case` for functions and variables
   - `PascalCase` for classes
   - `UPPER_CASE` for constants

3. **Keep functions focused**:
   - One test should test one thing
   - Functions should be small and focused

4. **Use descriptive variable names**:
   ```python
   # Good
   user_response = http_client.get("/api/users/123")
   
   # Bad
   r = http_client.get("/api/users/123")
   ```

## 🔍 Code Review Checklist

When reviewing test code:

- [ ] Tests are independent
- [ ] Tests have descriptive names and docstrings
- [ ] Appropriate markers are used
- [ ] Test data is generated, not hardcoded
- [ ] Both success and failure cases are tested
- [ ] Cleanup is handled properly
- [ ] Code follows style guidelines
- [ ] No magic numbers or strings
- [ ] Tests run successfully

## 📝 Documentation

Update documentation when:

- Adding new fixtures → Update relevant fixture file docstrings
- Adding new utilities → Update utility module docstrings
- Adding new patterns → Update README.md
- Adding new markers → Update pytest.ini markers section

## 🐛 Debugging Tips

### Test Failures

1. **Run with verbose output**:
   ```bash
   pytest tests/integration/test_failing.py -vv -s
   ```

2. **Use pdb for debugging**:
   ```python
   def test_something(http_client):
       import pdb; pdb.set_trace()
       response = http_client.get("/api/endpoint")
   ```

3. **Check logs**:
   ```bash
   cat tests/logs/test.log
   ```

### Flaky Tests

If a test is occasionally failing:

1. **Add retry decorator**:
   ```python
   from tests.utils.helpers import retry
   
   @retry(max_attempts=3, delay=1.0)
   def test_flaky_endpoint(http_client):
       pass
   ```

2. **Investigate timing issues**:
   ```python
   from tests.utils.helpers import wait_for_condition
   
   wait_for_condition(lambda: is_ready(), timeout=30)
   ```

## 🤝 Getting Help

- Check existing tests for examples
- Read the full documentation in README.md
- Look at test_example.py for patterns
- Ask questions in code reviews

Thank you for contributing to PipesHub tests! 🚀

