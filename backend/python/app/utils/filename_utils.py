def sanitize_filename_for_content_disposition(
    filename: str,
    fallback: str = "file"
) -> str:
    """
    Sanitize a filename for use in HTTP Content-Disposition headers.

    The Content-Disposition header requires latin-1 encoding per RFC 2616.
    This function converts filenames to latin-1, ignoring non-compatible characters.

    Args:
        filename: The original filename to sanitize
        fallback: Fallback filename if sanitization results in empty string

    Returns:
        A latin-1 compatible filename safe for Content-Disposition headers
    """
    return filename.encode('latin-1', 'ignore').decode('latin-1') or fallback
