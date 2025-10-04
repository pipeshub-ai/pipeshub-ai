"""
LinkedIn Client Module

OAuth 2.0 authenticated client for LinkedIn REST API.
"""

from app.sources.client.linkedin.linkedin import (
    LinkedInClient,
    LinkedInOAuth2Config,
    LinkedInRESTClientViaOAuth2,
    LinkedInResponse,
)

__all__ = [
    'LinkedInClient',
    'LinkedInOAuth2Config',
    'LinkedInRESTClientViaOAuth2',
    'LinkedInResponse',
]
