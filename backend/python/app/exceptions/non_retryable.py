class NonRetryableError(Exception):
    """The caller must not retry or fall back."""
