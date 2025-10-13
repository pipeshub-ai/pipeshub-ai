"""LinkedIn DataSource - Official SDK Integration

This module provides comprehensive LinkedIn Business API access using the official
linkedin-api-client SDK. It follows the project pattern (similar to Slack/GitHub)
by wrapping the SDK rather than implementing custom HTTP logic.

Architecture:
- Uses official RestliClient from linkedin-api-client
- Returns native SDK response objects (not custom wrappers)
- Supports all Rest.li methods: GET, BATCH_GET, FINDER, CREATE, UPDATE, DELETE, ACTION
- Type hints use 'object' instead of 'Any' (per project standards)
- Simplified response handling via response.entity, response.json()
Reference: https://github.com/linkedin-developers/linkedin-api-python-client
"""

import logging
from typing import Dict, List, Optional

from app.sources.client.linkedin.linkedin import LinkedInClient

# Set up logger
logger = logging.getLogger(__name__)

class LinkedInDataSource:
    """LinkedIn Business APIs datasource using official SDK

    Provides access to 60+ LinkedIn Business API endpoints across:
    - Profile & Identity APIs
    - Posts & Shares APIs
    - Media & Assets APIs
    - Company & Organizations APIs
    - Advertising APIs
    - Analytics & Insights APIs

    All methods return native SDK response objects with properties like:
    - response.entity: The entity data (dict)
    - response.status_code: HTTP status code
    - response.elements: List of entities (for finders/collections)
    - response.paging: Paging metadata (for collections)

    Example:
        >>> client = LinkedInClient(access_token="TOKEN", version_string="202406")
        >>> ds = LinkedInDataSource(client)
        >>> response = ds.get_profile()
        >>> profile_data = response.entity
        >>> print(profile_data['id'], profile_data['firstName'])
    """

    def __init__(self, client: LinkedInClient) -> None:
        """Initialize datasource with LinkedIn client

        Args:
            client: LinkedInClient instance (wraps official SDK)
        """
        self.client = client
        self._restli_client = client.get_client()

    # ========================================================================
    # PROFILE & IDENTITY APIs (7 methods)
    # ========================================================================

    def get_userinfo(self) -> object:
        """Get current user info using OpenID Connect

        LinkedIn API: GET /userinfo
        Required scopes: openid, profile, email

        This is the NEW OpenID Connect endpoint that works with current LinkedIn tokens.
        Use this instead of get_profile() for modern authentication.

        Returns:
            GetResponse with .entity containing: name, email, sub (user ID), picture

        Example:
            >>> response = ds.get_userinfo()
            >>> user_data = response.entity
            >>> print(user_data['name'], user_data['email'])
            >>> user_id = user_data['sub']  # Use this as person ID
        """
        return self._restli_client.get(
            resource_path="/userinfo",
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_profile(self, query_params: Optional[Dict[str, object]] = None) -> object:
        """Get current authenticated member's profile (LEGACY - use get_userinfo instead)

        LinkedIn API: GET /me
        Required scopes: r_liteprofile (NO LONGER AVAILABLE)

        ⚠️  WARNING: This endpoint requires the old r_liteprofile scope which is
        no longer available with OpenID Connect tokens. Use get_userinfo() instead.

        Args:
            query_params: Optional query parameters (e.g., {"fields": "id,firstName"})

        Returns:
            GetResponse with .entity property containing profile data

        Example:
            >>> # DEPRECATED - Use get_userinfo() instead
            >>> response = ds.get_profile()
            >>> print(response.entity['id'])
        """
        return self._restli_client.get(
            resource_path="/me",
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def get_profile_with_decoration(
        self,
        projection: str = "(id,firstName,lastName,profilePicture(displayImage~:playableStreams))"
    ) -> object:
        """Get profile with image decoration

        LinkedIn API: GET /me with decoration
        Required scopes: r_liteprofile

        Args:
            projection: Field projection including decorations

        Returns:
            GetResponse with decorated profile data

        Example:
            >>> response = ds.get_profile_with_decoration()
            >>> image_data = response.entity.get('profilePicture', {}).get('displayImage~')
        """
        return self._restli_client.get(
            resource_path="/me",
            access_token=self.client.access_token,
            query_params={"projection": projection},
            version_string=self.client.version_string
        )

    def get_profile_by_id(
        self,
        person_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get profile by person ID

        LinkedIn API: GET /people/{id}
        Required scopes: r_liteprofile

        Args:
            person_id: LinkedIn person URN or ID
            query_params: Optional query parameters

        Returns:
            GetResponse with profile data

        Example:
            >>> response = ds.get_profile_by_id("AbCdEfG")
        """
        return self._restli_client.get(
            resource_path="/people/{id}",
            path_keys={"id": person_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def get_connections(
        self,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get member's connections

        LinkedIn API: GET /connections
        Required scopes: r_network

        Args:
            query_params: Optional query parameters (paging, fields, etc.)

        Returns:
            CollectionResponse with .elements containing connections list

        Example:
            >>> response = ds.get_connections(
            ...     query_params={"start": 0, "count": 50}
            ... )
            >>> for connection in response.elements:
            ...     print(connection['id'])
        """
        return self._restli_client.get_all(
            resource_path="/connections",
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def search_people(
        self,
        keywords: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Search for people by keywords

        LinkedIn API: FINDER /people?q=keywords
        Required scopes: r_liteprofile

        Args:
            keywords: Search keywords
            query_params: Additional search parameters

        Returns:
            CollectionResponse with .elements containing search results

        Example:
            >>> response = ds.search_people(
            ...     keywords="software engineer",
            ...     query_params={"start": 0, "count": 25}
            ... )
        """
        final_params = {"keywords": keywords}
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/people",
            finder_name="keywords",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def get_organization_acls(
        self,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get organization ACLs (access control lists)

        LinkedIn API: GET /organizationAcls
        Required scopes: rw_organization_admin

        Args:
            query_params: Optional query parameters

        Returns:
            CollectionResponse with organization ACL data

        Example:
            >>> response = ds.get_organization_acls()
            >>> for acl in response.elements:
            ...     print(acl['organization'], acl['role'])
        """
        return self._restli_client.get_all(
            resource_path="/organizationAcls",
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    # ========================================================================
    # POSTS & SHARES APIs (10 methods)
    # ========================================================================

    def create_post(
        self,
        author: str,
        commentary: str,
        visibility: str = "PUBLIC",
        lifecycle_state: str = "PUBLISHED",
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Create a new post (using newer /posts API)

        LinkedIn API: CREATE /posts
        Required scopes: w_member_social

        Args:
            author: Author URN (e.g., "urn:li:person:AbCdEfG")
            commentary: Post text content
            visibility: Post visibility ("PUBLIC", "CONNECTIONS", etc.)
            lifecycle_state: "PUBLISHED" or "DRAFT"
            query_params: Additional parameters

        Returns:
            CreateResponse with .entity_id of created post

        Example:
            >>> response = ds.create_post(
            ...     author="urn:li:person:AbCdEfG",
            ...     commentary="Hello LinkedIn!",
            ...     visibility="PUBLIC"
            ... )
            >>> post_id = response.entity_id
        """
        entity = {
            "author": author,
            "lifecycleState": lifecycle_state,
            "visibility": visibility,
            "commentary": commentary,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": []
            }
        }

        return self._restli_client.create(
            resource_path="/posts",
            entity=entity,
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def create_ugc_post(
        self,
        author: str,
        text: str,
        visibility: str = "PUBLIC",
        lifecycle_state: str = "PUBLISHED"
    ) -> object:
        """Create UGC post (legacy /ugcPosts API)

        LinkedIn API: CREATE /ugcPosts
        Required scopes: w_member_social

        Args:
            author: Author URN
            text: Post text
            visibility: Visibility setting
            lifecycle_state: "PUBLISHED" or "DRAFT"

        Returns:
            CreateResponse with .entity_id of created UGC post

        Example:
            >>> response = ds.create_ugc_post(
            ...     author="urn:li:person:AbCdEfG",
            ...     text="Legacy post format"
            ... )
        """
        entity = {
            "author": author,
            "lifecycleState": lifecycle_state,
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE"
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": visibility
            }
        }

        return self._restli_client.create(
            resource_path="/ugcPosts",
            entity=entity,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_post(
        self,
        post_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get post by ID

        LinkedIn API: GET /posts/{id}
        Required scopes: r_member_social

        Args:
            post_id: Post URN or ID
            query_params: Optional query parameters

        Returns:
            GetResponse with post data

        Example:
            >>> response = ds.get_post("urn:li:share:123456")
            >>> print(response.entity['commentary'])
        """
        return self._restli_client.get(
            resource_path="/posts/{id}",
            path_keys={"id": post_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def update_post(
        self,
        post_id: str,
        patch_data: Dict[str, object]
    ) -> object:
        """Update post with patch data

        LinkedIn API: PARTIAL_UPDATE /posts/{id}
        Required scopes: w_member_social

        Args:
            post_id: Post URN or ID
            patch_data: Patch operations (e.g., {"$set": {"commentary": "New text"}})

        Returns:
            UpdateResponse

        Example:
            >>> response = ds.update_post(
            ...     post_id="urn:li:share:123456",
            ...     patch_data={"$set": {"commentary": "Updated text"}}
            ... )
        """
        return self._restli_client.partial_update(
            resource_path="/posts/{id}",
            path_keys={"id": post_id},
            patch_set_object=patch_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def delete_post(self, post_id: str) -> object:
        """Delete post

        LinkedIn API: DELETE /posts/{id}
        Required scopes: w_member_social

        Args:
            post_id: Post URN or ID

        Returns:
            DeleteResponse with status

        Example:
            >>> response = ds.delete_post("urn:li:share:123456")
            >>> print(response.status_code)
        """
        return self._restli_client.delete(
            resource_path="/posts/{id}",
            path_keys={"id": post_id},
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_share_statistics(
        self,
        share_urn: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get statistics for a share/post

        LinkedIn API: GET /organizationalEntityShareStatistics
        Required scopes: r_organization_social

        Args:
            share_urn: Share URN
            query_params: Additional parameters (e.g., time range)

        Returns:
            GetResponse with share statistics

        Example:
            >>> response = ds.get_share_statistics("urn:li:share:123456")
            >>> stats = response.entity
            >>> print(stats.get('likeCount'), stats.get('commentCount'))
        """
        final_params = {"q": "organizationalEntity", "organizationalEntity": share_urn}
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/organizationalEntityShareStatistics",
            finder_name="organizationalEntity",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def create_comment(
        self,
        post_urn: str,
        comment_text: str
    ) -> object:
        """Create comment on a post

        LinkedIn API: CREATE /socialActions/{postUrn}/comments
        Required scopes: w_member_social

        Args:
            post_urn: Post URN to comment on
            comment_text: Comment text content

        Returns:
            CreateResponse with created comment ID

        Example:
            >>> response = ds.create_comment(
            ...     post_urn="urn:li:share:123456",
            ...     comment_text="Great post!"
            ... )
        """
        entity = {
            "message": {"text": comment_text}
        }

        return self._restli_client.create(
            resource_path="/socialActions/{postUrn}/comments",
            path_keys={"postUrn": post_urn},
            entity=entity,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_comments(
        self,
        post_urn: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get comments on a post

        LinkedIn API: GET /socialActions/{postUrn}/comments
        Required scopes: r_member_social

        Args:
            post_urn: Post URN
            query_params: Optional parameters (paging, etc.)

        Returns:
            CollectionResponse with comments

        Example:
            >>> response = ds.get_comments("urn:li:share:123456")
            >>> for comment in response.elements:
            ...     print(comment['message']['text'])
        """
        return self._restli_client.get_all(
            resource_path="/socialActions/{postUrn}/comments",
            path_keys={"postUrn": post_urn},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def like_post(self, post_urn: str) -> object:
        """Like a post

        LinkedIn API: ACTION /socialActions/{postUrn}/likes
        Required scopes: w_member_social

        Args:
            post_urn: Post URN to like

        Returns:
            ActionResponse

        Example:
            >>> response = ds.like_post("urn:li:share:123456")
        """
        return self._restli_client.action(
            resource_path="/socialActions/{postUrn}/likes",
            path_keys={"postUrn": post_urn},
            action_name="like",
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def unlike_post(self, post_urn: str, like_id: str) -> object:
        """Unlike a post (delete like)

        LinkedIn API: DELETE /socialActions/{postUrn}/likes/{likeId}
        Required scopes: w_member_social

        Args:
            post_urn: Post URN
            like_id: Like ID to delete

        Returns:
            DeleteResponse

        Example:
            >>> response = ds.unlike_post(
            ...     post_urn="urn:li:share:123456",
            ...     like_id="like123"
            ... )
        """
        return self._restli_client.delete(
            resource_path="/socialActions/{postUrn}/likes/{likeId}",
            path_keys={"postUrn": post_urn, "likeId": like_id},
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    # ========================================================================
    # COMPANY & ORGANIZATIONS APIs (7 methods)
    # ========================================================================

    def get_organizations(
        self,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get organizations (all accessible)

        LinkedIn API: GET /organizations
        Required scopes: r_organization_social

        Args:
            query_params: Optional query parameters

        Returns:
            CollectionResponse with organizations

        Example:
            >>> response = ds.get_organizations()
            >>> for org in response.elements:
            ...     print(org['id'], org['localizedName'])
        """
        return self._restli_client.get_all(
            resource_path="/organizations",
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def get_organization(
        self,
        org_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get organization by ID

        LinkedIn API: GET /organizations/{id}
        Required scopes: r_organization_social

        Args:
            org_id: Organization ID or URN
            query_params: Optional query parameters

        Returns:
            GetResponse with organization data

        Example:
            >>> response = ds.get_organization("123456")
            >>> org = response.entity
            >>> print(org['localizedName'], org['websiteUrl'])
        """
        return self._restli_client.get(
            resource_path="/organizations/{id}",
            path_keys={"id": org_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def update_organization(
        self,
        org_id: str,
        patch_data: Dict[str, object]
    ) -> object:
        """Update organization with patch data

        LinkedIn API: PARTIAL_UPDATE /organizations/{id}
        Required scopes: rw_organization_admin

        Args:
            org_id: Organization ID
            patch_data: Patch operations

        Returns:
            UpdateResponse

        Example:
            >>> response = ds.update_organization(
            ...     org_id="123456",
            ...     patch_data={"$set": {"localizedDescription": "New description"}}
            ... )
        """
        return self._restli_client.partial_update(
            resource_path="/organizations/{id}",
            path_keys={"id": org_id},
            patch_set_object=patch_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def search_organizations(
        self,
        keywords: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Search organizations by keywords

        LinkedIn API: FINDER /organizations?q=search
        Required scopes: r_organization_social

        Args:
            keywords: Search keywords
            query_params: Additional search parameters

        Returns:
            CollectionResponse with search results

        Example:
            >>> response = ds.search_organizations(
            ...     keywords="tech company",
            ...     query_params={"start": 0, "count": 25}
            ... )
        """
        final_params = {"search": {"keywords": {"values": [keywords]}}}
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/organizations",
            finder_name="search",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def get_organization_follower_statistics(
        self,
        org_urn: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get follower statistics for an organization

        LinkedIn API: GET /organizationalEntityFollowerStatistics
        Required scopes: r_organization_social

        Args:
            org_urn: Organization URN
            query_params: Additional parameters (time range, etc.)

        Returns:
            GetResponse with follower statistics

        Example:
            >>> response = ds.get_organization_follower_statistics(
            ...     org_urn="urn:li:organization:123456"
            ... )
            >>> stats = response.entity
            >>> print(stats.get('followerCountsByCountry'))
        """
        return self.get_follower_statistics(org_urn=org_urn, query_params=query_params)

    def get_organization_page_statistics(
        self,
        org_urn: str,
        time_ranges: List[Dict[str, object]],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get page statistics for an organization

        LinkedIn API: FINDER /organizationPageStatistics
        Required scopes: r_organization_social

        Args:
            org_urn: Organization URN
            time_ranges: List of time range dicts with start/end timestamps
            query_params: Additional parameters

        Returns:
            CollectionResponse with page statistics

        Example:
            >>> response = ds.get_organization_page_statistics(
            ...     org_urn="urn:li:organization:123456",
            ...     time_ranges=[{
            ...         "start": 1609459200000,
            ...         "end": 1612137600000
            ...     }]
            ... )
        """
        return self.get_page_statistics(
            org_urn=org_urn, time_ranges=time_ranges, query_params=query_params
        )

    def get_organization_brands(
        self,
        org_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get brands associated with an organization

        LinkedIn API: GET /organizations/{id}/brands
        Required scopes: r_organization_social

        Args:
            org_id: Organization ID
            query_params: Optional query parameters

        Returns:
            CollectionResponse with brands

        Example:
            >>> response = ds.get_organization_brands("123456")
            >>> for brand in response.elements:
            ...     print(brand['id'], brand['localizedName'])
        """
        return self._restli_client.get_all(
            resource_path="/organizations/{id}/brands",
            path_keys={"id": org_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    # ========================================================================
    # ADVERTISING APIs (15 methods)
    # ========================================================================

    def search_ad_accounts(
        self,
        search_params: Dict[str, object],
        paging_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Search ad accounts by criteria

        LinkedIn API: FINDER /adAccounts?q=search
        Required scopes: r_ads, rw_ads

        Args:
            search_params: Search criteria (e.g., {"reference": {"values": ["urn:li:organization:123"]}, "test": True})
            paging_params: Optional paging (start, count)

        Returns:
            CollectionResponse with ad accounts

        Example:
            >>> response = ds.search_ad_accounts(
            ...     search_params={
            ...         "reference": {"values": ["urn:li:organization:123"]},
            ...         "status": {"values": ["ACTIVE"]}
            ...     },
            ...     paging_params={"start": 0, "count": 10}
            ... )
            >>> for account in response.elements:
            ...     print(account['id'], account['name'])
        """
        final_params = {"search": search_params}
        if paging_params:
            final_params.update(paging_params)

        return self._restli_client.finder(
            resource_path="/adAccounts",
            finder_name="search",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def create_ad_account(self, account_data: Dict[str, object]) -> object:
        """Create a new ad account

        LinkedIn API: CREATE /adAccounts
        Required scopes: rw_ads

        Args:
            account_data: Ad account data (name, reference, type, status, test, etc.)

        Returns:
            CreateResponse with created account ID

        Example:
            >>> response = ds.create_ad_account({
            ...     "name": "Test Ad Account",
            ...     "reference": "urn:li:organization:123",
            ...     "type": "BUSINESS",
            ...     "status": "DRAFT",
            ...     "test": True
            ... })
            >>> account_id = response.entity_id
        """
        return self._restli_client.create(
            resource_path="/adAccounts",
            entity=account_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_ad_account(
        self,
        account_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get ad account by ID

        LinkedIn API: GET /adAccounts/{id}
        Required scopes: r_ads

        Args:
            account_id: Ad account ID
            query_params: Optional query parameters

        Returns:
            GetResponse with ad account data

        Example:
            >>> response = ds.get_ad_account("123456")
            >>> account = response.entity
            >>> print(account['name'], account['status'])
        """
        return self._restli_client.get(
            resource_path="/adAccounts/{id}",
            path_keys={"id": account_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def update_ad_account(
        self,
        account_id: str,
        patch_data: Dict[str, object]
    ) -> object:
        """Update ad account

        LinkedIn API: PARTIAL_UPDATE /adAccounts/{id}
        Required scopes: rw_ads

        Args:
            account_id: Ad account ID
            patch_data: Patch operations

        Returns:
            UpdateResponse

        Example:
            >>> response = ds.update_ad_account(
            ...     account_id="123456",
            ...     patch_data={"$set": {"name": "Updated Name"}}
            ... )
        """
        return self._restli_client.partial_update(
            resource_path="/adAccounts/{id}",
            path_keys={"id": account_id},
            patch_set_object=patch_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def delete_ad_account(self, account_id: str) -> object:
        """Delete ad account

        LinkedIn API: DELETE /adAccounts/{id}
        Required scopes: rw_ads

        Args:
            account_id: Ad account ID to delete

        Returns:
            DeleteResponse

        Example:
            >>> response = ds.delete_ad_account("123456")
        """
        return self._restli_client.delete(
            resource_path="/adAccounts/{id}",
            path_keys={"id": account_id},
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def search_campaigns(
        self,
        account_urn: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Search campaigns by account

        LinkedIn API: FINDER /adCampaigns?q=account
        Required scopes: r_ads

        Args:
            account_urn: Ad account URN
            query_params: Additional search parameters

        Returns:
            CollectionResponse with campaigns

        Example:
            >>> response = ds.search_campaigns(
            ...     account_urn="urn:li:sponsoredAccount:123456"
            ... )
        """
        final_params = {"q": "account", "account": account_urn}
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/adCampaigns",
            finder_name="account",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def create_campaign(self, campaign_data: Dict[str, object]) -> object:
        """Create ad campaign

        LinkedIn API: CREATE /adCampaigns
        Required scopes: rw_ads

        Args:
            campaign_data: Campaign data (account, name, type, status, etc.)

        Returns:
            CreateResponse with campaign ID

        Example:
            >>> response = ds.create_campaign({
            ...     "account": "urn:li:sponsoredAccount:123456",
            ...     "name": "My Campaign",
            ...     "type": "TEXT_AD",
            ...     "status": "DRAFT"
            ... })
        """
        return self._restli_client.create(
            resource_path="/adCampaigns",
            entity=campaign_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_campaign(
        self,
        campaign_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get campaign by ID

        LinkedIn API: GET /adCampaigns/{id}
        Required scopes: r_ads

        Args:
            campaign_id: Campaign ID
            query_params: Optional query parameters

        Returns:
            GetResponse with campaign data

        Example:
            >>> response = ds.get_campaign("987654")
        """
        return self._restli_client.get(
            resource_path="/adCampaigns/{id}",
            path_keys={"id": campaign_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def batch_get_campaigns(
        self,
        campaign_ids: List[str],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Batch get multiple campaigns

        LinkedIn API: BATCH_GET /adCampaigns
        Required scopes: r_ads

        Args:
            campaign_ids: List of campaign IDs
            query_params: Optional query parameters

        Returns:
            BatchGetResponse with results dict

        Example:
            >>> response = ds.batch_get_campaigns(["123", "456", "789"])
            >>> for campaign_id, result in response.results.items():
            ...     print(campaign_id, result.get('name'))
        """
        return self._restli_client.batch_get(
            resource_path="/adCampaigns",
            ids=campaign_ids,
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def update_campaign(
        self,
        campaign_id: str,
        patch_data: Dict[str, object]
    ) -> object:
        """Update campaign

        LinkedIn API: PARTIAL_UPDATE /adCampaigns/{id}
        Required scopes: rw_ads

        Args:
            campaign_id: Campaign ID
            patch_data: Patch operations

        Returns:
            UpdateResponse

        Example:
            >>> response = ds.update_campaign(
            ...     campaign_id="987654",
            ...     patch_data={"$set": {"status": "ACTIVE"}}
            ... )
        """
        return self._restli_client.partial_update(
            resource_path="/adCampaigns/{id}",
            path_keys={"id": campaign_id},
            patch_set_object=patch_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def delete_campaign(self, campaign_id: str) -> object:
        """Delete campaign

        LinkedIn API: DELETE /adCampaigns/{id}
        Required scopes: rw_ads

        Args:
            campaign_id: Campaign ID to delete

        Returns:
            DeleteResponse

        Example:
            >>> response = ds.delete_campaign("987654")
        """
        return self._restli_client.delete(
            resource_path="/adCampaigns/{id}",
            path_keys={"id": campaign_id},
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def search_campaign_groups(
        self,
        account_urn: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Search campaign groups by account

        LinkedIn API: FINDER /adCampaignGroups?q=account
        Required scopes: r_ads

        Args:
            account_urn: Ad account URN
            query_params: Additional parameters

        Returns:
            CollectionResponse with campaign groups

        Example:
            >>> response = ds.search_campaign_groups(
            ...     account_urn="urn:li:sponsoredAccount:123456"
            ... )
        """
        final_params = {"q": "account", "account": account_urn}
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/adCampaignGroups",
            finder_name="account",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def create_campaign_group(self, group_data: Dict[str, object]) -> object:
        """Create campaign group

        LinkedIn API: CREATE /adCampaignGroups
        Required scopes: rw_ads

        Args:
            group_data: Campaign group data

        Returns:
            CreateResponse with group ID

        Example:
            >>> response = ds.create_campaign_group({
            ...     "account": "urn:li:sponsoredAccount:123456",
            ...     "name": "My Campaign Group",
            ...     "status": "DRAFT"
            ... })
        """
        return self._restli_client.create(
            resource_path="/adCampaignGroups",
            entity=group_data,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def batch_get_campaign_groups(
        self,
        group_ids: List[str],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Batch get campaign groups (demonstrates query tunneling)

        LinkedIn API: BATCH_GET /adCampaignGroups
        Required scopes: r_ads

        Note: For large lists of IDs, the SDK automatically performs query
        tunneling (converts GET to POST with body).

        Args:
            group_ids: List of campaign group IDs
            query_params: Optional query parameters

        Returns:
            BatchGetResponse with results

        Example:
            >>> # Large batch automatically uses query tunneling
            >>> ids = [str(i) for i in range(1000000000, 1000000400)]
            >>> response = ds.batch_get_campaign_groups(ids)
        """
        return self._restli_client.batch_get(
            resource_path="/adCampaignGroups",
            ids=group_ids,
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def get_ad_analytics(
        self,
        finder_params: Dict[str, object],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get ad analytics data

        LinkedIn API: FINDER /adAnalytics
        Required scopes: r_ads

        Args:
            finder_params: Finder parameters (accounts, campaigns, dateRange, fields, etc.)
            query_params: Additional query parameters

        Returns:
            CollectionResponse with analytics data

        Example:
            >>> response = ds.get_ad_analytics(
            ...     finder_params={
            ...         "accounts": ["urn:li:sponsoredAccount:123456"],
            ...         "dateRange": {
            ...             "start": {"year": 2024, "month": 1, "day": 1},
            ...             "end": {"year": 2024, "month": 12, "day": 31}
            ...         },
            ...         "fields": ["impressions", "clicks", "costInLocalCurrency"]
            ...     }
            ... )
        """
        final_params = finder_params.copy()
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/adAnalytics",
            finder_name="analytics",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    # ========================================================================
    # MEDIA & ASSETS APIs (4 methods)
    # ========================================================================

    def register_upload(
        self,
        owner: str,
        recipes: List[str],
        service_relationships: Optional[List[Dict[str, object]]] = None
    ) -> object:
        """Register an upload to get upload URL

        LinkedIn API: ACTION /assets?action=registerUpload
        Required scopes: w_member_social, w_organization_social

        Args:
            owner: Owner URN (person or organization)
            recipes: List of recipe URNs (e.g., ["urn:li:digitalmediaRecipe:feedshare-image"])
            service_relationships: Optional service relationships

        Returns:
            ActionResponse with upload instructions

        Example:
            >>> response = ds.register_upload(
            ...     owner="urn:li:person:AbCdEfG",
            ...     recipes=["urn:li:digitalmediaRecipe:feedshare-image"]
            ... )
            >>> upload_url = response.value['uploadMechanism']['...']['uploadUrl']
            >>> asset_urn = response.value['asset']
        """
        action_params = {
            "registerUploadRequest": {
                "owner": owner,
                "recipes": recipes
            }
        }
        if service_relationships:
            action_params["registerUploadRequest"]["serviceRelationships"] = service_relationships

        return self._restli_client.action(
            resource_path="/assets",
            action_name="registerUpload",
            action_params=action_params,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def register_video_upload(
        self,
        owner: str,
        file_size: int,
        recipes: Optional[List[str]] = None
    ) -> object:
        """Register video upload

        LinkedIn API: ACTION /videos?action=initializeUpload
        Required scopes: w_member_social, w_organization_social

        Args:
            owner: Owner URN
            file_size: Video file size in bytes
            recipes: Optional recipe URNs

        Returns:
            ActionResponse with video upload instructions

        Example:
            >>> response = ds.register_video_upload(
            ...     owner="urn:li:person:AbCdEfG",
            ...     file_size=5242880,  # 5MB
            ...     recipes=["urn:li:digitalmediaRecipe:feedshare-video"]
            ... )
        """
        action_params = {
            "initializeUploadRequest": {
                "owner": owner,
                "fileSizeBytes": file_size
            }
        }
        if recipes:
            action_params["initializeUploadRequest"]["recipes"] = recipes

        return self._restli_client.action(
            resource_path="/videos",
            action_name="initializeUpload",
            action_params=action_params,
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    def get_asset(
        self,
        asset_id: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get asset by ID

        LinkedIn API: GET /assets/{id}
        Required scopes: r_member_social, r_organization_social

        Args:
            asset_id: Asset URN or ID
            query_params: Optional query parameters

        Returns:
            GetResponse with asset metadata

        Example:
            >>> response = ds.get_asset("urn:li:digitalmediaAsset:ABC123")
            >>> asset = response.entity
            >>> print(asset['status'], asset['mediaTypeFamily'])
        """
        return self._restli_client.get(
            resource_path="/assets/{id}",
            path_keys={"id": asset_id},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def delete_asset(self, asset_id: str) -> object:
        """Delete asset

        LinkedIn API: DELETE /assets/{id}
        Required scopes: w_member_social, w_organization_social

        Args:
            asset_id: Asset URN or ID to delete

        Returns:
            DeleteResponse

        Example:
            >>> response = ds.delete_asset("urn:li:digitalmediaAsset:ABC123")
        """
        return self._restli_client.delete(
            resource_path="/assets/{id}",
            path_keys={"id": asset_id},
            access_token=self.client.access_token,
            version_string=self.client.version_string
        )

    # Note: For actual file upload to S3/Azure, use standard HTTP libraries
    # The SDK provides the upload URL via register_upload()
    # Then use requests/aiohttp to PUT the binary data to that URL

    # ========================================================================
    # ANALYTICS & INSIGHTS APIs (5 methods)
    # ========================================================================

    def get_follower_statistics(
        self,
        org_urn: str,
        time_ranges: Optional[List[Dict[str, object]]] = None,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get detailed follower statistics

        LinkedIn API: FINDER /organizationalEntityFollowerStatistics
        Required scopes: r_organization_social

        Args:
            org_urn: Organization URN
            time_ranges: Optional time ranges for statistics
            query_params: Additional parameters

        Returns:
            CollectionResponse with follower statistics

        Example:
            >>> response = ds.get_follower_statistics(
            ...     org_urn="urn:li:organization:123456",
            ...     time_ranges=[{
            ...         "start": 1609459200000,
            ...         "end": 1612137600000
            ...     }]
            ... )
        """
        final_params = {"q": "organizationalEntity", "organizationalEntity": org_urn}
        if time_ranges:
            final_params["timeIntervals"] = time_ranges
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/organizationalEntityFollowerStatistics",
            finder_name="organizationalEntity",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def batch_get_share_statistics(
        self,
        share_urns: List[str],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Batch get share statistics for multiple shares

        LinkedIn API: BATCH_GET /organizationalEntityShareStatistics
        Required scopes: r_organization_social

        Args:
            share_urns: List of share URNs
            query_params: Optional query parameters

        Returns:
            BatchGetResponse with statistics for each share

        Example:
            >>> response = ds.batch_get_share_statistics([
            ...     "urn:li:share:123456",
            ...     "urn:li:share:789012"
            ... ])
            >>> for urn, stats in response.results.items():
            ...     print(f"{urn}: {stats.get('likeCount')} likes")
        """
        return self._restli_client.batch_get(
            resource_path="/organizationalEntityShareStatistics",
            ids=share_urns,
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )

    def get_page_statistics(
        self,
        org_urn: str,
        time_ranges: List[Dict[str, object]],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get page statistics for organization

        LinkedIn API: FINDER /organizationPageStatistics
        Required scopes: r_organization_social

        Args:
            org_urn: Organization URN
            time_ranges: Time ranges with start/end timestamps
            query_params: Additional parameters

        Returns:
            CollectionResponse with page statistics

        Example:
            >>> response = ds.get_page_statistics(
            ...     org_urn="urn:li:organization:123456",
            ...     time_ranges=[{
            ...         "start": 1609459200000,
            ...         "end": 1612137600000
            ...     }]
            ... )
            >>> stats = response.elements[0]
            >>> print(stats.get('views'), stats.get('clicks'))
        """
        final_params = {
            "q": "organization",
            "organization": org_urn,
            "timeIntervals": time_ranges
        }
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/organizationPageStatistics",
            finder_name="organization",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def get_visitor_analytics(
        self,
        org_urn: str,
        time_ranges: List[Dict[str, object]],
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get visitor analytics for organization page

        LinkedIn API: FINDER /organizationPageStatistics with visitor metrics
        Required scopes: r_organization_social

        Args:
            org_urn: Organization URN
            time_ranges: Time ranges for analytics
            query_params: Additional parameters (e.g., visitor metrics)

        Returns:
            CollectionResponse with visitor analytics

        Example:
            >>> response = ds.get_visitor_analytics(
            ...     org_urn="urn:li:organization:123456",
            ...     time_ranges=[{"start": 1609459200000, "end": 1612137600000}],
            ...     query_params={"metrics": ["VISITOR_DEMOGRAPHICS"]}
            ... )
        """
        final_params = {
            "q": "organization",
            "organization": org_urn,
            "timeIntervals": time_ranges
        }
        if query_params:
            final_params.update(query_params)

        return self._restli_client.finder(
            resource_path="/organizationPageStatistics",
            finder_name="organization",
            access_token=self.client.access_token,
            query_params=final_params,
            version_string=self.client.version_string
        )

    def get_engagement_metrics(
        self,
        entity_urn: str,
        query_params: Optional[Dict[str, object]] = None
    ) -> object:
        """Get engagement metrics for an entity (post, article, etc.)

        LinkedIn API: GET /socialMetadata/{entityUrn}
        Required scopes: r_member_social, r_organization_social

        Args:
            entity_urn: Entity URN
            query_params: Optional query parameters

        Returns:
            GetResponse with engagement metrics

        Example:
            >>> response = ds.get_engagement_metrics(
            ...     entity_urn="urn:li:share:123456"
            ... )
            >>> metrics = response.entity
            >>> print(metrics.get('likes'), metrics.get('comments'))
        """
        return self._restli_client.get(
            resource_path="/socialMetadata/{entityUrn}",
            path_keys={"entityUrn": entity_urn},
            access_token=self.client.access_token,
            query_params=query_params,
            version_string=self.client.version_string
        )
