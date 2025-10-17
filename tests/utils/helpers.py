"""
Test helper utilities.

This module provides helper functions for common test operations like
retries, polling, data validation, and more.
"""

import asyncio
import time
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

T = TypeVar('T')

def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator to retry a function on failure.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier for delay
        exceptions: Tuple of exceptions to catch
        
    Example:
        @retry(max_attempts=3, delay=1.0)
        def flaky_api_call():
            response = requests.get("http://api.example.com")
            response.raise_for_status()
            return response
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay: float = delay
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        raise last_exception
            
        return wrapper
    return decorator


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator to retry an async function on failure.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier for delay
        exceptions: Tuple of exceptions to catch
        
    Example:
        @async_retry(max_attempts=3, delay=1.0)
        async def flaky_async_call():
            async with httpx.AsyncClient() as client:
                response = await client.get("http://api.example.com")
                response.raise_for_status()
                return response
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay: float = delay
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        raise last_exception
            
        return wrapper
    return decorator


def wait_for_condition(
    condition: Callable[[], bool],
    timeout: float = 30.0,
    interval: float = 1.0,
    error_message: str = "Condition not met within timeout"
) -> bool:
    """
    Wait for a condition to become true.
    
    Args:
        condition: Callable that returns True when condition is met
        timeout: Maximum time to wait in seconds
        interval: Check interval in seconds
        error_message: Error message if timeout is reached
        
    Returns:
        True if condition was met
        
    Raises:
        TimeoutError: If condition is not met within timeout
        
    Example:
        def is_server_ready():
            try:
                response = requests.get("http://localhost:8000/health")
                return response.status_code == 200
            except:
                return False
        
        wait_for_condition(is_server_ready, timeout=60)
    """
    start_time: float = time.time()
    
    while time.time() - start_time < timeout:
        if condition():
            return True
        time.sleep(interval)
    
    raise TimeoutError(error_message)


async def async_wait_for_condition(
    condition: Callable[[], bool],
    timeout: float = 30.0,
    interval: float = 1.0,
    error_message: str = "Condition not met within timeout"
) -> bool:
    """
    Async version of wait_for_condition.
    
    Args:
        condition: Callable that returns True when condition is met
        timeout: Maximum time to wait in seconds
        interval: Check interval in seconds
        error_message: Error message if timeout is reached
        
    Returns:
        True if condition was met
        
    Raises:
        TimeoutError: If condition is not met within timeout
    """
    start_time: float = time.time()
    
    while time.time() - start_time < timeout:
        if condition():
            return True
        await asyncio.sleep(interval)
    
    raise TimeoutError(error_message)


def poll_until_status(
    get_status_func: Callable[[], str],
    expected_status: str,
    timeout: float = 60.0,
    interval: float = 2.0
) -> bool:
    """
    Poll until a status reaches expected value.
    
    Args:
        get_status_func: Function that returns current status
        expected_status: Expected status value
        timeout: Maximum time to wait
        interval: Polling interval
        
    Returns:
        True if expected status was reached
        
    Example:
        def get_job_status():
            response = client.get(f"/api/jobs/{job_id}")
            return response.json()["status"]
        
        poll_until_status(get_job_status, "completed", timeout=120)
    """
    def condition():
        return get_status_func() == expected_status
    
    return wait_for_condition(
        condition,
        timeout=timeout,
        interval=interval,
        error_message=f"Status did not reach '{expected_status}' within {timeout}s"
    )


def deep_compare(obj1: Any, obj2: Any, ignore_keys: Optional[List[str]] = None) -> bool:
    """
    Deep comparison of two objects, optionally ignoring certain keys.
    
    Args:
        obj1: First object
        obj2: Second object
        ignore_keys: List of keys to ignore in comparison
        
    Returns:
        True if objects are equal
        
    Example:
        result = deep_compare(
            {"id": 1, "name": "test", "created_at": "2024-01-01"},
            {"id": 1, "name": "test", "created_at": "2024-01-02"},
            ignore_keys=["created_at"]
        )
        # Returns True
    """
    ignore_keys = ignore_keys or []
    
    if type(obj1) != type(obj2):
        return False
    
    if isinstance(obj1, dict):
        keys1: set = set(obj1.keys()) - set(ignore_keys)
        keys2: set = set(obj2.keys()) - set(ignore_keys)
        
        if keys1 != keys2:
            return False
        
        return all(
            deep_compare(obj1[key], obj2[key], ignore_keys)
            for key in keys1
        )
    
    elif isinstance(obj1, (list, tuple)):
        if len(obj1) != len(obj2):
            return False
        
        return all(
            deep_compare(item1, item2, ignore_keys)
            for item1, item2 in zip(obj1, obj2)
        )
    
    else:
        return obj1 == obj2


def validate_response_schema(response_data: Dict, required_fields: List[str]) -> bool:
    """
    Validate that response contains all required fields.
    
    Args:
        response_data: Response data dictionary
        required_fields: List of required field names
        
    Returns:
        True if all required fields are present
        
    Raises:
        AssertionError: If any required field is missing
        
    Example:
        validate_response_schema(
            response.json(),
            ["id", "name", "email", "created_at"]
        )
    """
    missing_fields: List[str] = [field for field in required_fields if field not in response_data]
    
    if missing_fields:
        raise AssertionError(f"Missing required fields: {missing_fields}")
    
    return True


def extract_ids(items: List[Dict], id_key: str = "id") -> List[Any]:
    """
    Extract IDs from list of items.
    
    Args:
        items: List of dictionaries
        id_key: Key name for ID field
        
    Returns:
        List of IDs
        
    Example:
        users = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        ids = extract_ids(users)  # [1, 2]
    """
    return [item.get(id_key) for item in items if id_key in item]


def filter_items(
    items: List[Dict],
    **filters
) -> List[Dict]:
    """
    Filter list of items by field values.
    
    Args:
        items: List of dictionaries
        **filters: Field name and value pairs to filter by
        
    Returns:
        Filtered list of items
        
    Example:
        users = [
            {"name": "Alice", "role": "admin"},
            {"name": "Bob", "role": "user"},
            {"name": "Charlie", "role": "admin"},
        ]
        admins = filter_items(users, role="admin")
    """
    def matches_filters(item):
        return all(item.get(key) == value for key, value in filters.items())
    
    return [item for item in items if matches_filters(item)]


def sanitize_for_comparison(data: Dict, fields_to_remove: Optional[List[str]] = None) -> Dict:
    """
    Remove dynamic fields from data for comparison.
    
    Args:
        data: Data dictionary
        fields_to_remove: List of field names to remove
        
    Returns:
        Sanitized dictionary
        
    Example:
        response_data = {
            "id": 123,
            "name": "Test",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z"
        }
        clean_data = sanitize_for_comparison(
            response_data,
            ["created_at", "updated_at"]
        )
    """
    if fields_to_remove is None:
        fields_to_remove = ["created_at", "updated_at", "modified_at", "timestamp"]
    
    return {k: v for k, v in data.items() if k not in fields_to_remove}


def measure_response_time(func: Callable) -> tuple:
    """
    Measure function execution time.
    
    Args:
        func: Function to measure
        
    Returns:
        Tuple of (result, execution_time_seconds)
        
    Example:
        result, elapsed = measure_response_time(lambda: client.get("/api/users"))
        assert elapsed < 1.0, "Response took too long"
    """
    start_time: float = time.time()
    result: Any = func()
    elapsed_time: float = time.time() - start_time
    
    return result, elapsed_time


async def async_measure_response_time(func: Callable) -> tuple:
    """
    Measure async function execution time.
    
    Args:
        func: Async function to measure
        
    Returns:
        Tuple of (result, execution_time_seconds)
    """
    start_time: float = time.time()
    result: Any = await func()
    elapsed_time: float = time.time() - start_time
    
    return result, elapsed_time

