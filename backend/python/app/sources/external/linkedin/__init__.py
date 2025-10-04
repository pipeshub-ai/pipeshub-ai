"""
LinkedIn Data Source Module

This module provides integration with LinkedIn's REST API v2 for various business use cases
including Sales, Marketing, HR, and Business Intelligence.

Main components:
- LinkedInClient: OAuth 2.0 authenticated client
- LinkedInDataSource: High-level data source with method wrappers
- LinkedInResponse: Standardized response format

Example:
    from app.sources.client.linkedin.linkedin import LinkedInClient, LinkedInOAuth2Config
    from app.sources.external.linkedin import LinkedInDataSource

    client = LinkedInClient.build_with_config(
        LinkedInOAuth2Config(access_token="YOUR_TOKEN")
    )
    datasource = LinkedInDataSource(client)

    response = await datasource.get_profile()
    if response.success:
        print(response.data)
"""

from app.sources.external.linkedin.linkedin import LinkedInDataSource

__all__ = ['LinkedInDataSource']
__version__ = '1.0.0'
