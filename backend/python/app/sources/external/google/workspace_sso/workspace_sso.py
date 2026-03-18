from typing import Any, Optional

from app.sources.client.google.google import GoogleClient


class GoogleWorkspaceSSODataSource:
    """
    Google Workspace SSO connector for SSO, security, and access management operations.
    Uses Google SDK client internally for all operations.
    This class wraps Google Admin SDK Directory API methods related to SSO/security
    concerns including domains, domain aliases, roles, role assignments, privileges,
    tokens, verification codes, 2-step verification, ASPs, and customer management.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Admin SDK Directory API client.
        Args:
            client: Google Admin SDK Directory API client from build('admin', 'directory_v1', credentials=credentials)
        """
        super().__init__()
        self.client = client

    # ==================== Domains ====================

    async def domains_list(
        self,
        customer: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Lists the domains of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domains

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer

        request = self.client.domains().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def domains_get(
        self,
        customer: str,
        domainName: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a domain of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domains/{domainName}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            domainName (str, required): Name of domain to be retrieved

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['domainName'] = domainName

        request = self.client.domains().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def domains_insert(
        self,
        customer: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a domain of the customer.

        HTTP POST admin/directory/v1/customer/{customer}/domains

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.domains().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.domains().insert(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def domains_delete(
        self,
        customer: str,
        domainName: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a domain of the customer.

        HTTP DELETE admin/directory/v1/customer/{customer}/domains/{domainName}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            domainName (str, required): Name of domain to be deleted

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['domainName'] = domainName

        request = self.client.domains().delete(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Domain Aliases ====================

    async def domain_aliases_list(
        self,
        customer: str,
        parentDomainName: Optional[str] = None
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Lists the domain aliases of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domainaliases

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            parentDomainName (str, optional): Name of the parent domain for which domain aliases are to be fetched.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        if parentDomainName is not None:
            kwargs['parentDomainName'] = parentDomainName

        request = self.client.domainAliases().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def domain_aliases_get(
        self,
        customer: str,
        domainAliasName: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a domain alias of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domainaliases/{domainAliasName}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            domainAliasName (str, required): Name of domain alias to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['domainAliasName'] = domainAliasName

        request = self.client.domainAliases().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def domain_aliases_insert(
        self,
        customer: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a domain alias of the customer.

        HTTP POST admin/directory/v1/customer/{customer}/domainaliases

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.domainAliases().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.domainAliases().insert(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def domain_aliases_delete(
        self,
        customer: str,
        domainAliasName: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a domain Alias of the customer.

        HTTP DELETE admin/directory/v1/customer/{customer}/domainaliases/{domainAliasName}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            domainAliasName (str, required): Name of domain alias to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['domainAliasName'] = domainAliasName

        request = self.client.domainAliases().delete(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Roles ====================

    async def roles_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of all the roles in a domain.

        HTTP GET admin/directory/v1/customer/{customer}/roles

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            maxResults (int, optional): Maximum number of results to return.
            pageToken (str, optional): Token to specify the next page in the list.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.roles().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def roles_get(
        self,
        customer: str,
        roleId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a role.

        HTTP GET admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['roleId'] = roleId

        request = self.client.roles().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def roles_insert(
        self,
        customer: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Creates a role.

        HTTP POST admin/directory/v1/customer/{customer}/roles

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.roles().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roles().insert(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def roles_update(
        self,
        customer: str,
        roleId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Updates a role.

        HTTP PUT admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['roleId'] = roleId

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.roles().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roles().update(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def roles_patch(
        self,
        customer: str,
        roleId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Patches a role.

        HTTP PATCH admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['roleId'] = roleId

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.roles().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roles().patch(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def roles_delete(
        self,
        customer: str,
        roleId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a role.

        HTTP DELETE admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['roleId'] = roleId

        request = self.client.roles().delete(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Role Assignments ====================

    async def role_assignments_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        roleId: Optional[str] = None,
        userKey: Optional[str] = None,
        includeIndirectRoleAssignments: Optional[bool] = None  # noqa: FBT001
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of all roleAssignments.

        HTTP GET admin/directory/v1/customer/{customer}/roleassignments

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            maxResults (int, optional): Maximum number of results to return.
            pageToken (str, optional): Token to specify the next page in the list.
            roleId (str, optional): Immutable ID of a role. If included in the request, returns only role assignments containing this role ID.
            userKey (str, optional): The primary email address, alias email address, or unique user or group ID. If included in the request, returns role assignments only for this user or group.
            includeIndirectRoleAssignments (bool, optional): When set to `true`, fetches indirect role assignments (i.e. role assignment via a group) as well as direct ones. Defaults to `false`. You must specify `user_key` or the indirect role assignments will not be included.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if roleId is not None:
            kwargs['roleId'] = roleId
        if userKey is not None:
            kwargs['userKey'] = userKey
        if includeIndirectRoleAssignments is not None:
            kwargs['includeIndirectRoleAssignments'] = includeIndirectRoleAssignments

        request = self.client.roleAssignments().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def role_assignments_get(
        self,
        customer: str,
        roleAssignmentId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a role assignment.

        HTTP GET admin/directory/v1/customer/{customer}/roleassignments/{roleAssignmentId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            roleAssignmentId (str, required): Immutable ID of the role assignment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['roleAssignmentId'] = roleAssignmentId

        request = self.client.roleAssignments().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def role_assignments_insert(
        self,
        customer: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Creates a role assignment.

        HTTP POST admin/directory/v1/customer/{customer}/roleassignments

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.roleAssignments().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roleAssignments().insert(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def role_assignments_delete(
        self,
        customer: str,
        roleAssignmentId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a role assignment.

        HTTP DELETE admin/directory/v1/customer/{customer}/roleassignments/{roleAssignmentId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleAssignmentId (str, required): Immutable ID of the role assignment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer
        kwargs['roleAssignmentId'] = roleAssignmentId

        request = self.client.roleAssignments().delete(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Privileges ====================

    async def privileges_list(
        self,
        customer: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of all privileges for a customer.

        HTTP GET admin/directory/v1/customer/{customer}/roles/ALL/privileges

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customer'] = customer

        request = self.client.privileges().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Tokens / OAuth ====================

    async def tokens_list(
        self,
        userKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Returns the set of tokens specified user has issued to 3rd party applications.

        HTTP GET admin/directory/v1/users/{userKey}/tokens

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey

        request = self.client.tokens().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def tokens_get(
        self,
        userKey: str,
        clientId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Gets information about an access token issued by a user.

        HTTP GET admin/directory/v1/users/{userKey}/tokens/{clientId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            clientId (str, required): The Client ID of the application the token is issued to.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey
        kwargs['clientId'] = clientId

        request = self.client.tokens().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def tokens_delete(
        self,
        userKey: str,
        clientId: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Deletes all access tokens issued by a user for an application.

        HTTP DELETE admin/directory/v1/users/{userKey}/tokens/{clientId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            clientId (str, required): The Client ID of the application the token is issued to.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey
        kwargs['clientId'] = clientId

        request = self.client.tokens().delete(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Verification Codes ====================

    async def verification_codes_list(
        self,
        userKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Returns the current set of valid backup verification codes for the specified user.

        HTTP GET admin/directory/v1/users/{userKey}/verificationCodes

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey

        request = self.client.verificationCodes().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def verification_codes_generate(
        self,
        userKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Generates new backup verification codes for the user.

        HTTP POST admin/directory/v1/users/{userKey}/verificationCodes/generate

        Args:
            userKey (str, required): Email or immutable ID of the user

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.verificationCodes().generate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.verificationCodes().generate(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def verification_codes_invalidate(
        self,
        userKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Invalidates the current backup verification codes for the user.

        HTTP POST admin/directory/v1/users/{userKey}/verificationCodes/invalidate

        Args:
            userKey (str, required): Email or immutable ID of the user

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.verificationCodes().invalidate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.verificationCodes().invalidate(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== 2-Step Verification ====================

    async def two_step_verification_turn_off(
        self,
        userKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Turns off 2-Step Verification for user.

        HTTP POST admin/directory/v1/users/{userKey}/twoStepVerification/turnOff

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.twoStepVerification().turnOff(**kwargs, body=body) # type: ignore
        else:
            request = self.client.twoStepVerification().turnOff(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== ASPs (Application-Specific Passwords) ====================

    async def asps_list(
        self,
        userKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Lists the ASPs issued by a user.

        HTTP GET admin/directory/v1/users/{userKey}/asps

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey

        request = self.client.asps().list(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def asps_get(
        self,
        userKey: str,
        codeId: int
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Gets information about an ASP issued by a user.

        HTTP GET admin/directory/v1/users/{userKey}/asps/{codeId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            codeId (int, required): The unique ID of the ASP.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey
        kwargs['codeId'] = codeId

        request = self.client.asps().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def asps_delete(
        self,
        userKey: str,
        codeId: int
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Deletes an ASP issued by a user.

        HTTP DELETE admin/directory/v1/users/{userKey}/asps/{codeId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            codeId (int, required): The unique ID of the ASP to be deleted.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['userKey'] = userKey
        kwargs['codeId'] = codeId

        request = self.client.asps().delete(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    # ==================== Customers ====================

    async def customers_get(
        self,
        customerKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a customer.

        HTTP GET admin/directory/v1/customers/{customerKey}

        Args:
            customerKey (str, required): Id of the customer to be retrieved

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customerKey'] = customerKey

        request = self.client.customers().get(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def customers_update(
        self,
        customerKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Updates a customer.

        HTTP PUT admin/directory/v1/customers/{customerKey}

        Args:
            customerKey (str, required): Id of the customer to be updated

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customerKey'] = customerKey

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.customers().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers().update(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def customers_patch(
        self,
        customerKey: str
    ) -> dict[str, Any]:
        """Google Admin SDK Directory API: Patches a customer.

        HTTP PATCH admin/directory/v1/customers/{customerKey}

        Args:
            customerKey (str, required): Id of the customer to be updated

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        kwargs['customerKey'] = customerKey

        # Handle request body if needed
        if 'body' in kwargs:
            body: Any = kwargs.pop('body')  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
            request = self.client.customers().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers().patch(**kwargs) # type: ignore
        return request.execute()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
