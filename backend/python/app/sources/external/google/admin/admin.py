from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleAdminDataSource:
    """
    Auto-generated Google Admin SDK Directory API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Admin SDK Directory API directory_v1 methods and provides
    a consistent interface while using the official Google SDK.
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
        self.client = client

    async def chromeosdevices_action(
        self,
        customerId: str,
        resourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Use [BatchChangeChromeOsDeviceStatus](https://developers.google.com/workspace/admin/directory/reference/rest/v1/customer.devices.chromeos/batchChangeStatus) instead. Takes an action that affects a Chrome OS Device. This includes deprovisioning, disabling, and re-enabling devices. *Warning:* * Deprovisioning a device will stop device policy syncing and remove device-level printers. After a device is deprovisioned, it must be wiped before it can be re-enrolled. * Lost or stolen devices should use the disable action. * Re-enabling a disabled device will consume a device license. If you do not have sufficient licenses available when completing the re-enable action, you will receive an error. For more information about deprovisioning and disabling devices, visit the [help center](https://support.google.com/chrome/a/answer/3523633).

        HTTP POST admin/directory/v1/customer/{customerId}/devices/chromeos/{resourceId}/action

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            resourceId (str, required): The unique ID of the device. The `resourceId`s are returned in the response from the [chromeosdevices.list](https://developers.google.com/workspace/admin/directory/v1/reference/chromeosdevices/list) method.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if resourceId is not None:
            kwargs['resourceId'] = resourceId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.chromeosdevices().action(**kwargs, body=body) # type: ignore
        else:
            request = self.client.chromeosdevices().action(**kwargs) # type: ignore
        return request.execute()

    async def chromeosdevices_get(
        self,
        customerId: str,
        deviceId: str,
        projection: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a Chrome OS device's properties.

        HTTP GET admin/directory/v1/customer/{customerId}/devices/chromeos/{deviceId}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            deviceId (str, required): The unique ID of the device. The `deviceId`s are returned in the response from the [chromeosdevices.list](https://developers.google.com/workspace/admin/directory/v1/reference/chromeosdevices/list) method.
            projection (str, optional): Determines whether the response contains the full list of properties or only a subset.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if deviceId is not None:
            kwargs['deviceId'] = deviceId
        if projection is not None:
            kwargs['projection'] = projection

        request = self.client.chromeosdevices().get(**kwargs) # type: ignore
        return request.execute()

    async def chromeosdevices_list(
        self,
        customerId: str,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        orgUnitPath: Optional[str] = None,
        pageToken: Optional[str] = None,
        projection: Optional[str] = None,
        query: Optional[str] = None,
        sortOrder: Optional[str] = None,
        includeChildOrgunits: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of Chrome OS devices within an account.

        HTTP GET admin/directory/v1/customer/{customerId}/devices/chromeos

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            maxResults (int, optional): Maximum number of results to return. Value should not exceed 300.
            orderBy (str, optional): Device property to use for sorting results.
            orgUnitPath (str, optional): The full path of the organizational unit (minus the leading `/`) or its unique ID.
            pageToken (str, optional): The `pageToken` query parameter is used to request the next page of query results. The follow-on request's `pageToken` query parameter is the `nextPageToken` from your previous response.
            projection (str, optional): Determines whether the response contains the full list of properties or only a subset.
            query (str, optional): Search string in the format given at https://developers.google.com/workspace/admin/directory/v1/list-query-operators
            sortOrder (str, optional): Whether to return results in ascending or descending order. Must be used with the `orderBy` parameter.
            includeChildOrgunits (bool, optional): Return devices from all child orgunits, as well as the specified org unit. If this is set to true, 'orgUnitPath' must be provided.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if projection is not None:
            kwargs['projection'] = projection
        if query is not None:
            kwargs['query'] = query
        if sortOrder is not None:
            kwargs['sortOrder'] = sortOrder
        if includeChildOrgunits is not None:
            kwargs['includeChildOrgunits'] = includeChildOrgunits

        request = self.client.chromeosdevices().list(**kwargs) # type: ignore
        return request.execute()

    async def chromeosdevices_move_devices_to_ou(
        self,
        customerId: str,
        orgUnitPath: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Moves or inserts multiple Chrome OS devices to an organizational unit. You can move up to 50 devices at once.

        HTTP POST admin/directory/v1/customer/{customerId}/devices/chromeos/moveDevicesToOu

        Args:
            customerId (str, required): Immutable. ID of the Google Workspace account
            orgUnitPath (str, required): Full path of the target organizational unit or its ID

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.chromeosdevices().moveDevicesToOu(**kwargs, body=body) # type: ignore
        else:
            request = self.client.chromeosdevices().moveDevicesToOu(**kwargs) # type: ignore
        return request.execute()

    async def chromeosdevices_patch(
        self,
        customerId: str,
        deviceId: str,
        projection: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a device's updatable properties, such as `annotatedUser`, `annotatedLocation`, `notes`, `orgUnitPath`, or `annotatedAssetId`. This method supports [patch semantics](https://developers.google.com/workspace/admin/directory/v1/guides/performance#patch).

        HTTP PATCH admin/directory/v1/customer/{customerId}/devices/chromeos/{deviceId}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            deviceId (str, required): The unique ID of the device. The `deviceId`s are returned in the response from the [chromeosdevices.list](https://developers.google.com/workspace/admin/v1/reference/chromeosdevices/list) method.
            projection (str, optional): Determines whether the response contains the full list of properties or only a subset.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if deviceId is not None:
            kwargs['deviceId'] = deviceId
        if projection is not None:
            kwargs['projection'] = projection

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.chromeosdevices().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.chromeosdevices().patch(**kwargs) # type: ignore
        return request.execute()

    async def chromeosdevices_update(
        self,
        customerId: str,
        deviceId: str,
        projection: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a device's updatable properties, such as `annotatedUser`, `annotatedLocation`, `notes`, `orgUnitPath`, or `annotatedAssetId`.

        HTTP PUT admin/directory/v1/customer/{customerId}/devices/chromeos/{deviceId}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            deviceId (str, required): The unique ID of the device. The `deviceId`s are returned in the response from the [chromeosdevices.list](https://developers.google.com/workspace/admin/v1/reference/chromeosdevices/list) method.
            projection (str, optional): Determines whether the response contains the full list of properties or only a subset.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if deviceId is not None:
            kwargs['deviceId'] = deviceId
        if projection is not None:
            kwargs['projection'] = projection

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.chromeosdevices().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.chromeosdevices().update(**kwargs) # type: ignore
        return request.execute()

    async def customer_devices_chromeos_issue_command(
        self,
        customerId: str,
        deviceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Issues a command for the device to execute.

        HTTP POST admin/directory/v1/customer/{customerId}/devices/chromeos/{deviceId}:issueCommand

        Args:
            customerId (str, required): Immutable. ID of the Google Workspace account.
            deviceId (str, required): Immutable. ID of Chrome OS Device.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if deviceId is not None:
            kwargs['deviceId'] = deviceId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customer_devices_chromeos().issueCommand(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customer_devices_chromeos().issueCommand(**kwargs) # type: ignore
        return request.execute()

    async def customer_devices_chromeos_batch_change_status(
        self,
        customerId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Changes the status of a batch of ChromeOS devices. For more information about changing a ChromeOS device state [Repair, repurpose, or retire ChromeOS devices](https://support.google.com/chrome/a/answer/3523633).

        HTTP POST admin/directory/v1/customer/{customerId}/devices/chromeos:batchChangeStatus

        Args:
            customerId (str, required): Required. Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customer_devices_chromeos().batchChangeStatus(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customer_devices_chromeos().batchChangeStatus(**kwargs) # type: ignore
        return request.execute()

    async def customer_devices_chromeos_commands_get(
        self,
        customerId: str,
        deviceId: str,
        commandId: int
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Gets command data a specific command issued to the device.

        HTTP GET admin/directory/v1/customer/{customerId}/devices/chromeos/{deviceId}/commands/{commandId}

        Args:
            customerId (str, required): Immutable. ID of the Google Workspace account.
            deviceId (str, required): Immutable. ID of Chrome OS Device.
            commandId (int, required): Immutable. ID of Chrome OS Device Command.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if deviceId is not None:
            kwargs['deviceId'] = deviceId
        if commandId is not None:
            kwargs['commandId'] = commandId

        request = self.client.customer_devices_chromeos_commands().get(**kwargs) # type: ignore
        return request.execute()

    async def asps_delete(
        self,
        userKey: str,
        codeId: int
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes an ASP issued by a user.

        HTTP DELETE admin/directory/v1/users/{userKey}/asps/{codeId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            codeId (int, required): The unique ID of the ASP to be deleted.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if codeId is not None:
            kwargs['codeId'] = codeId

        request = self.client.asps().delete(**kwargs) # type: ignore
        return request.execute()

    async def asps_get(
        self,
        userKey: str,
        codeId: int
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Gets information about an ASP issued by a user.

        HTTP GET admin/directory/v1/users/{userKey}/asps/{codeId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            codeId (int, required): The unique ID of the ASP.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if codeId is not None:
            kwargs['codeId'] = codeId

        request = self.client.asps().get(**kwargs) # type: ignore
        return request.execute()

    async def asps_list(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists the ASPs issued by a user.

        HTTP GET admin/directory/v1/users/{userKey}/asps

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.asps().list(**kwargs) # type: ignore
        return request.execute()

    async def channels_stop(self) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Stops watching resources through this channel.

        HTTP POST admin/directory_v1/channels/stop

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.channels().stop(**kwargs, body=body) # type: ignore
        else:
            request = self.client.channels().stop(**kwargs) # type: ignore
        return request.execute()

    async def customers_get(
        self,
        customerKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a customer.

        HTTP GET admin/directory/v1/customers/{customerKey}

        Args:
            customerKey (str, required): Id of the customer to be retrieved

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerKey is not None:
            kwargs['customerKey'] = customerKey

        request = self.client.customers().get(**kwargs) # type: ignore
        return request.execute()

    async def customers_update(
        self,
        customerKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a customer.

        HTTP PUT admin/directory/v1/customers/{customerKey}

        Args:
            customerKey (str, required): Id of the customer to be updated

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerKey is not None:
            kwargs['customerKey'] = customerKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers().update(**kwargs) # type: ignore
        return request.execute()

    async def customers_patch(
        self,
        customerKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Patches a customer.

        HTTP PATCH admin/directory/v1/customers/{customerKey}

        Args:
            customerKey (str, required): Id of the customer to be updated

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerKey is not None:
            kwargs['customerKey'] = customerKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers().patch(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_list_printer_models(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists the supported printer models.

        HTTP GET admin/directory/v1/{+parent}/chrome/printers:listPrinterModels

        Args:
            parent (str, required): Required. The name of the customer who owns this collection of printers. Format: customers/{customer_id}
            pageSize (int, optional): The maximum number of objects to return. The service may return fewer than this value.
            pageToken (str, optional): A page token, received from a previous call.
            filter (str, optional): Filer to list only models by a given manufacturer in format: "manufacturer:Brother". Search syntax is shared between this api and Admin Console printers pages.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if filter is not None:
            kwargs['filter'] = filter

        request = self.client.customers_chrome_printers().listPrinterModels(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        orgUnitId: Optional[str] = None,
        filter: Optional[str] = None,
        orderBy: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: List printers configs.

        HTTP GET admin/directory/v1/{+parent}/chrome/printers

        Args:
            parent (str, required): Required. The name of the customer who owns this collection of printers. Format: customers/{customer_id}
            pageSize (int, optional): The maximum number of objects to return. The service may return fewer than this value.
            pageToken (str, optional): A page token, received from a previous call.
            orgUnitId (str, optional): Organization Unit that we want to list the printers for. When org_unit is not present in the request then all printers of the customer are returned (or filtered). When org_unit is present in the request then only printers available to this OU will be returned (owned or inherited). You may see if printer is owned or inherited for this OU by looking at Printer.org_unit_id.
            filter (str, optional): Search query. Search syntax is shared between this api and Admin Console printers pages.
            orderBy (str, optional): The order to sort results by. Must be one of display_name, description, make_and_model, or create_time. Default order is ascending, but descending order can be returned by appending "desc" to the order_by field. For instance, "description desc" will return the printers sorted by description in descending order.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if orgUnitId is not None:
            kwargs['orgUnitId'] = orgUnitId
        if filter is not None:
            kwargs['filter'] = filter
        if orderBy is not None:
            kwargs['orderBy'] = orderBy

        request = self.client.customers_chrome_printers().list(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Returns a `Printer` resource (printer's config).

        HTTP GET admin/directory/v1/{+name}

        Args:
            name (str, required): Required. The name of the printer to retrieve. Format: customers/{customer_id}/chrome/printers/{printer_id}

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.customers_chrome_printers().get(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_create(
        self,
        parent: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a printer under given Organization Unit.

        HTTP POST admin/directory/v1/{+parent}/chrome/printers

        Args:
            parent (str, required): Required. The name of the customer. Format: customers/{customer_id}

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printers().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printers().create(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_batch_create_printers(
        self,
        parent: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates printers under given Organization Unit.

        HTTP POST admin/directory/v1/{+parent}/chrome/printers:batchCreatePrinters

        Args:
            parent (str, required): Required. The name of the customer. Format: customers/{customer_id}

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printers().batchCreatePrinters(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printers().batchCreatePrinters(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_patch(
        self,
        name: str,
        updateMask: Optional[str] = None,
        clearMask: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a `Printer` resource.

        HTTP PATCH admin/directory/v1/{+name}

        Args:
            name (str, required): Identifier. The resource name of the Printer object, in the format customers/{customer-id}/printers/{printer-id} (During printer creation leave empty)
            updateMask (str, optional): The list of fields to be updated. Note, some of the fields are read only and cannot be updated. Values for not specified fields will be patched.
            clearMask (str, optional): The list of fields to be cleared. Note, some of the fields are read only and cannot be updated. Values for not specified fields will be patched.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name
        if updateMask is not None:
            kwargs['updateMask'] = updateMask
        if clearMask is not None:
            kwargs['clearMask'] = clearMask

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printers().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printers().patch(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_delete(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a `Printer`.

        HTTP DELETE admin/directory/v1/{+name}

        Args:
            name (str, required): Required. The name of the printer to be updated. Format: customers/{customer_id}/chrome/printers/{printer_id}

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.customers_chrome_printers().delete(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_printers_batch_delete_printers(
        self,
        parent: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes printers in batch.

        HTTP POST admin/directory/v1/{+parent}/chrome/printers:batchDeletePrinters

        Args:
            parent (str, required): Required. The name of the customer. Format: customers/{customer_id}

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printers().batchDeletePrinters(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printers().batchDeletePrinters(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        orgUnitId: Optional[str] = None,
        filter: Optional[str] = None,
        orderBy: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists print server configurations.

        HTTP GET admin/directory/v1/{+parent}/chrome/printServers

        Args:
            parent (str, required): Required. The [unique ID](https://developers.google.com/workspace/admin/directory/reference/rest/v1/customers) of the customer's Google Workspace account. Format: `customers/{id}`
            pageSize (int, optional): The maximum number of objects to return (default `100`, max `100`). The service might return fewer than this value.
            pageToken (str, optional): A generated token to paginate results (the `next_page_token` from a previous call).
            orgUnitId (str, optional): If `org_unit_id` is present in the request, only print servers owned or inherited by the organizational unit (OU) are returned. If the `PrintServer` resource's `org_unit_id` matches the one in the request, the OU owns the server. If `org_unit_id` is not specified in the request, all print servers are returned or filtered against.
            filter (str, optional): Search query in [Common Expression Language syntax](https://github.com/google/cel-spec). Supported filters are `display_name`, `description`, and `uri`. Example: `printServer.displayName=='marketing-queue'`.
            orderBy (str, optional): Sort order for results. Supported values are `display_name`, `description`, or `create_time`. Default order is ascending, but descending order can be returned by appending "desc" to the `order_by` field. For instance, `orderBy=='description desc'` returns the print servers sorted by description in descending order.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if orgUnitId is not None:
            kwargs['orgUnitId'] = orgUnitId
        if filter is not None:
            kwargs['filter'] = filter
        if orderBy is not None:
            kwargs['orderBy'] = orderBy

        request = self.client.customers_chrome_printServers().list(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Returns a print server's configuration.

        HTTP GET admin/directory/v1/{+name}

        Args:
            name (str, required): Required. The [unique ID](https://developers.google.com/workspace/admin/directory/reference/rest/v1/customers) of the customer's Google Workspace account. Format: `customers/{id}`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.customers_chrome_printServers().get(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_create(
        self,
        parent: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a print server.

        HTTP POST admin/directory/v1/{+parent}/chrome/printServers

        Args:
            parent (str, required): Required. The [unique ID](https://developers.google.com/workspace/admin/directory/reference/rest/v1/customers) of the customer's Google Workspace account. Format: `customers/{id}`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printServers().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printServers().create(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_batch_create_print_servers(
        self,
        parent: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates multiple print servers.

        HTTP POST admin/directory/v1/{+parent}/chrome/printServers:batchCreatePrintServers

        Args:
            parent (str, required): Required. The [unique ID](https://developers.google.com/workspace/admin/directory/reference/rest/v1/customers) of the customer's Google Workspace account. Format: `customers/{id}`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printServers().batchCreatePrintServers(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printServers().batchCreatePrintServers(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_patch(
        self,
        name: str,
        updateMask: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a print server's configuration.

        HTTP PATCH admin/directory/v1/{+name}

        Args:
            name (str, required): Identifier. Resource name of the print server. Leave empty when creating. Format: `customers/{customer.id}/printServers/{print_server.id}`
            updateMask (str, optional): The list of fields to update. Some fields are read-only and cannot be updated. Values for unspecified fields are patched.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name
        if updateMask is not None:
            kwargs['updateMask'] = updateMask

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printServers().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printServers().patch(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_delete(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a print server.

        HTTP DELETE admin/directory/v1/{+name}

        Args:
            name (str, required): Required. The name of the print server to be deleted. Format: `customers/{customer.id}/chrome/printServers/{print_server.id}`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.customers_chrome_printServers().delete(**kwargs) # type: ignore
        return request.execute()

    async def customers_chrome_print_servers_batch_delete_print_servers(
        self,
        parent: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes multiple print servers.

        HTTP POST admin/directory/v1/{+parent}/chrome/printServers:batchDeletePrintServers

        Args:
            parent (str, required): Required. The [unique ID](https://developers.google.com/workspace/admin/directory/reference/rest/v1/customers) of the customer's Google Workspace account. Format: `customers/{customer.id}`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.customers_chrome_printServers().batchDeletePrintServers(**kwargs, body=body) # type: ignore
        else:
            request = self.client.customers_chrome_printServers().batchDeletePrintServers(**kwargs) # type: ignore
        return request.execute()

    async def domain_aliases_delete(
        self,
        customer: str,
        domainAliasName: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a domain Alias of the customer.

        HTTP DELETE admin/directory/v1/customer/{customer}/domainaliases/{domainAliasName}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            domainAliasName (str, required): Name of domain alias to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if domainAliasName is not None:
            kwargs['domainAliasName'] = domainAliasName

        request = self.client.domainAliases().delete(**kwargs) # type: ignore
        return request.execute()

    async def domain_aliases_get(
        self,
        customer: str,
        domainAliasName: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a domain alias of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domainaliases/{domainAliasName}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            domainAliasName (str, required): Name of domain alias to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if domainAliasName is not None:
            kwargs['domainAliasName'] = domainAliasName

        request = self.client.domainAliases().get(**kwargs) # type: ignore
        return request.execute()

    async def domain_aliases_insert(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a domain alias of the customer.

        HTTP POST admin/directory/v1/customer/{customer}/domainaliases

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.domainAliases().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.domainAliases().insert(**kwargs) # type: ignore
        return request.execute()

    async def domain_aliases_list(
        self,
        customer: str,
        parentDomainName: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists the domain aliases of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domainaliases

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            parentDomainName (str, optional): Name of the parent domain for which domain aliases are to be fetched.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if parentDomainName is not None:
            kwargs['parentDomainName'] = parentDomainName

        request = self.client.domainAliases().list(**kwargs) # type: ignore
        return request.execute()

    async def domains_delete(
        self,
        customer: str,
        domainName: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a domain of the customer.

        HTTP DELETE admin/directory/v1/customer/{customer}/domains/{domainName}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            domainName (str, required): Name of domain to be deleted

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if domainName is not None:
            kwargs['domainName'] = domainName

        request = self.client.domains().delete(**kwargs) # type: ignore
        return request.execute()

    async def domains_get(
        self,
        customer: str,
        domainName: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a domain of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domains/{domainName}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            domainName (str, required): Name of domain to be retrieved

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if domainName is not None:
            kwargs['domainName'] = domainName

        request = self.client.domains().get(**kwargs) # type: ignore
        return request.execute()

    async def domains_insert(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a domain of the customer.

        HTTP POST admin/directory/v1/customer/{customer}/domains

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.domains().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.domains().insert(**kwargs) # type: ignore
        return request.execute()

    async def domains_list(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists the domains of the customer.

        HTTP GET admin/directory/v1/customer/{customer}/domains

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        request = self.client.domains().list(**kwargs) # type: ignore
        return request.execute()

    async def groups_delete(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a group.

        HTTP DELETE admin/directory/v1/groups/{groupKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        request = self.client.groups().delete(**kwargs) # type: ignore
        return request.execute()

    async def groups_get(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a group's properties.

        HTTP GET admin/directory/v1/groups/{groupKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        request = self.client.groups().get(**kwargs) # type: ignore
        return request.execute()

    async def groups_insert(self) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a group.

        HTTP POST admin/directory/v1/groups

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.groups().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.groups().insert(**kwargs) # type: ignore
        return request.execute()

    async def groups_list(
        self,
        customer: Optional[str] = None,
        domain: Optional[str] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        query: Optional[str] = None,
        sortOrder: Optional[str] = None,
        userKey: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves all groups of a domain or of a user given a userKey (paginated).

        HTTP GET admin/directory/v1/groups

        Args:
            customer (str, optional): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            domain (str, optional): The domain name. Use this field to get groups from only one domain. To return all domains for a customer account, use the `customer` query parameter instead.
            maxResults (int, optional): Maximum number of results to return. Max allowed value is 200.
            orderBy (str, optional): Column to use for sorting results
            pageToken (str, optional): Token to specify next page in the list
            query (str, optional): Query string search. Should be of the form "". Complete documentation is at https: //developers.google.com/admin-sdk/directory/v1/guides/search-groups
            sortOrder (str, optional): Whether to return results in ascending or descending order. Only of use when orderBy is also used
            userKey (str, optional): Email or immutable ID of the user if only those groups are to be listed, the given user is a member of. If it's an ID, it should match with the ID of the user object. Cannot be used with the `customer` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if domain is not None:
            kwargs['domain'] = domain
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if query is not None:
            kwargs['query'] = query
        if sortOrder is not None:
            kwargs['sortOrder'] = sortOrder
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.groups().list(**kwargs) # type: ignore
        return request.execute()

    async def groups_update(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a group's properties.

        HTTP PUT admin/directory/v1/groups/{groupKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.groups().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.groups().update(**kwargs) # type: ignore
        return request.execute()

    async def groups_patch(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a group's properties. This method supports [patch semantics](https://developers.google.com/workspace/admin/directory/v1/guides/performance#patch).

        HTTP PATCH admin/directory/v1/groups/{groupKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.groups().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.groups().patch(**kwargs) # type: ignore
        return request.execute()

    async def groups_aliases_delete(
        self,
        groupKey: str,
        alias: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Removes an alias.

        HTTP DELETE admin/directory/v1/groups/{groupKey}/aliases/{alias}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            alias (str, required): The alias to be removed

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if alias is not None:
            kwargs['alias'] = alias

        request = self.client.groups_aliases().delete(**kwargs) # type: ignore
        return request.execute()

    async def groups_aliases_insert(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Adds an alias for the group.

        HTTP POST admin/directory/v1/groups/{groupKey}/aliases

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.groups_aliases().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.groups_aliases().insert(**kwargs) # type: ignore
        return request.execute()

    async def groups_aliases_list(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists all aliases for a group.

        HTTP GET admin/directory/v1/groups/{groupKey}/aliases

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        request = self.client.groups_aliases().list(**kwargs) # type: ignore
        return request.execute()

    async def members_delete(
        self,
        groupKey: str,
        memberKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Removes a member from a group.

        HTTP DELETE admin/directory/v1/groups/{groupKey}/members/{memberKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            memberKey (str, required): Identifies the group member in the API request. A group member can be a user or another group. The value can be the member's (group or user) primary email address, alias, or unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if memberKey is not None:
            kwargs['memberKey'] = memberKey

        request = self.client.members().delete(**kwargs) # type: ignore
        return request.execute()

    async def members_get(
        self,
        groupKey: str,
        memberKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a group member's properties.

        HTTP GET admin/directory/v1/groups/{groupKey}/members/{memberKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            memberKey (str, required): Identifies the group member in the API request. A group member can be a user or another group. The value can be the member's (group or user) primary email address, alias, or unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if memberKey is not None:
            kwargs['memberKey'] = memberKey

        request = self.client.members().get(**kwargs) # type: ignore
        return request.execute()

    async def members_has_member(
        self,
        groupKey: str,
        memberKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Checks whether the given user is a member of the group. Membership can be direct or nested, but if nested, the `memberKey` and `groupKey` must be entities in the same domain or an `Invalid input` error is returned. To check for nested memberships that include entities outside of the group's domain, use the [`checkTransitiveMembership()`](https://cloud.google.com/identity/docs/reference/rest/v1/groups.memberships/checkTransitiveMembership) method in the Cloud Identity Groups API.

        HTTP GET admin/directory/v1/groups/{groupKey}/hasMember/{memberKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            memberKey (str, required): Identifies the user member in the API request. The value can be the user's primary email address, alias, or unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if memberKey is not None:
            kwargs['memberKey'] = memberKey

        request = self.client.members().hasMember(**kwargs) # type: ignore
        return request.execute()

    async def members_insert(
        self,
        groupKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Adds a user to the specified group.

        HTTP POST admin/directory/v1/groups/{groupKey}/members

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.members().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.members().insert(**kwargs) # type: ignore
        return request.execute()

    async def members_list(
        self,
        groupKey: str,
        includeDerivedMembership: Optional[bool] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        roles: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of all members in a group. This method times out after 60 minutes. For more information, see [Troubleshoot error codes](https://developers.google.com/workspace/admin/directory/v1/guides/troubleshoot-error-codes).

        HTTP GET admin/directory/v1/groups/{groupKey}/members

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            includeDerivedMembership (bool, optional): Whether to list indirect memberships. Default: false.
            maxResults (int, optional): Maximum number of results to return. Max allowed value is 200.
            pageToken (str, optional): Token to specify next page in the list.
            roles (str, optional): The `roles` query parameter allows you to retrieve group members by role. Allowed values are `OWNER`, `MANAGER`, and `MEMBER`.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if includeDerivedMembership is not None:
            kwargs['includeDerivedMembership'] = includeDerivedMembership
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if roles is not None:
            kwargs['roles'] = roles

        request = self.client.members().list(**kwargs) # type: ignore
        return request.execute()

    async def members_update(
        self,
        groupKey: str,
        memberKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates the membership of a user in the specified group.

        HTTP PUT admin/directory/v1/groups/{groupKey}/members/{memberKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            memberKey (str, required): Identifies the group member in the API request. A group member can be a user or another group. The value can be the member's (group or user) primary email address, alias, or unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if memberKey is not None:
            kwargs['memberKey'] = memberKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.members().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.members().update(**kwargs) # type: ignore
        return request.execute()

    async def members_patch(
        self,
        groupKey: str,
        memberKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates the membership properties of a user in the specified group. This method supports [patch semantics](https://developers.google.com/workspace/admin/directory/v1/guides/performance#patch).

        HTTP PATCH admin/directory/v1/groups/{groupKey}/members/{memberKey}

        Args:
            groupKey (str, required): Identifies the group in the API request. The value can be the group's email address, group alias, or the unique group ID.
            memberKey (str, required): Identifies the group member in the API request. A group member can be a user or another group. The value can be the member's (group or user) primary email address, alias, or unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if groupKey is not None:
            kwargs['groupKey'] = groupKey
        if memberKey is not None:
            kwargs['memberKey'] = memberKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.members().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.members().patch(**kwargs) # type: ignore
        return request.execute()

    async def mobiledevices_action(
        self,
        customerId: str,
        resourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Takes an action that affects a mobile device. For example, remotely wiping a device.

        HTTP POST admin/directory/v1/customer/{customerId}/devices/mobile/{resourceId}/action

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            resourceId (str, required): The unique ID the API service uses to identify the mobile device.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if resourceId is not None:
            kwargs['resourceId'] = resourceId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.mobiledevices().action(**kwargs, body=body) # type: ignore
        else:
            request = self.client.mobiledevices().action(**kwargs) # type: ignore
        return request.execute()

    async def mobiledevices_delete(
        self,
        customerId: str,
        resourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Removes a mobile device.

        HTTP DELETE admin/directory/v1/customer/{customerId}/devices/mobile/{resourceId}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            resourceId (str, required): The unique ID the API service uses to identify the mobile device.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if resourceId is not None:
            kwargs['resourceId'] = resourceId

        request = self.client.mobiledevices().delete(**kwargs) # type: ignore
        return request.execute()

    async def mobiledevices_get(
        self,
        customerId: str,
        resourceId: str,
        projection: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a mobile device's properties.

        HTTP GET admin/directory/v1/customer/{customerId}/devices/mobile/{resourceId}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            resourceId (str, required): The unique ID the API service uses to identify the mobile device.
            projection (str, optional): Restrict information returned to a set of selected fields.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if resourceId is not None:
            kwargs['resourceId'] = resourceId
        if projection is not None:
            kwargs['projection'] = projection

        request = self.client.mobiledevices().get(**kwargs) # type: ignore
        return request.execute()

    async def mobiledevices_list(
        self,
        customerId: str,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        projection: Optional[str] = None,
        query: Optional[str] = None,
        sortOrder: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of all user-owned mobile devices for an account. To retrieve a list that includes company-owned devices, use the Cloud Identity [Devices API](https://cloud.google.com/identity/docs/concepts/overview-devices) instead. This method times out after 60 minutes. For more information, see [Troubleshoot error codes](https://developers.google.com/workspace/admin/directory/v1/guides/troubleshoot-error-codes).

        HTTP GET admin/directory/v1/customer/{customerId}/devices/mobile

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            maxResults (int, optional): Maximum number of results to return. Max allowed value is 100.
            orderBy (str, optional): Device property to use for sorting results.
            pageToken (str, optional): Token to specify next page in the list
            projection (str, optional): Restrict information returned to a set of selected fields.
            query (str, optional): Search string in the format given at https://developers.google.com/workspace/admin/directory/v1/search-operators
            sortOrder (str, optional): Whether to return results in ascending or descending order. Must be used with the `orderBy` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if projection is not None:
            kwargs['projection'] = projection
        if query is not None:
            kwargs['query'] = query
        if sortOrder is not None:
            kwargs['sortOrder'] = sortOrder

        request = self.client.mobiledevices().list(**kwargs) # type: ignore
        return request.execute()

    async def orgunits_delete(
        self,
        customerId: str,
        orgUnitPath: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Removes an organizational unit.

        HTTP DELETE admin/directory/v1/customer/{customerId}/orgunits/{+orgUnitPath}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            orgUnitPath (str, required): The full path of the organizational unit (minus the leading `/`) or its unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath

        request = self.client.orgunits().delete(**kwargs) # type: ignore
        return request.execute()

    async def orgunits_get(
        self,
        customerId: str,
        orgUnitPath: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves an organizational unit.

        HTTP GET admin/directory/v1/customer/{customerId}/orgunits/{+orgUnitPath}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            orgUnitPath (str, required): The full path of the organizational unit (minus the leading `/`) or its unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath

        request = self.client.orgunits().get(**kwargs) # type: ignore
        return request.execute()

    async def orgunits_insert(
        self,
        customerId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Adds an organizational unit.

        HTTP POST admin/directory/v1/customer/{customerId}/orgunits

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.orgunits().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.orgunits().insert(**kwargs) # type: ignore
        return request.execute()

    async def orgunits_list(
        self,
        customerId: str,
        orgUnitPath: Optional[str] = None,
        type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a list of all organizational units for an account.

        HTTP GET admin/directory/v1/customer/{customerId}/orgunits

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            orgUnitPath (str, optional): The full path to the organizational unit or its unique ID. Returns the children of the specified organizational unit.
            type (str, optional): Whether to return all sub-organizations or just immediate children.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath
        if type is not None:
            kwargs['type'] = type

        request = self.client.orgunits().list(**kwargs) # type: ignore
        return request.execute()

    async def orgunits_update(
        self,
        customerId: str,
        orgUnitPath: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates an organizational unit.

        HTTP PUT admin/directory/v1/customer/{customerId}/orgunits/{+orgUnitPath}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            orgUnitPath (str, required): The full path of the organizational unit (minus the leading `/`) or its unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.orgunits().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.orgunits().update(**kwargs) # type: ignore
        return request.execute()

    async def orgunits_patch(
        self,
        customerId: str,
        orgUnitPath: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates an organizational unit. This method supports [patch semantics](https://developers.google.com/workspace/admin/directory/v1/guides/performance#patch)

        HTTP PATCH admin/directory/v1/customer/{customerId}/orgunits/{+orgUnitPath}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users resource](https://developers.google.com/workspace/admin/directory/v1/reference/users).
            orgUnitPath (str, required): The full path of the organizational unit (minus the leading `/`) or its unique ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if orgUnitPath is not None:
            kwargs['orgUnitPath'] = orgUnitPath

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.orgunits().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.orgunits().patch(**kwargs) # type: ignore
        return request.execute()

    async def privileges_list(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of all privileges for a customer.

        HTTP GET admin/directory/v1/customer/{customer}/roles/ALL/privileges

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        request = self.client.privileges().list(**kwargs) # type: ignore
        return request.execute()

    async def role_assignments_delete(
        self,
        customer: str,
        roleAssignmentId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a role assignment.

        HTTP DELETE admin/directory/v1/customer/{customer}/roleassignments/{roleAssignmentId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleAssignmentId (str, required): Immutable ID of the role assignment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if roleAssignmentId is not None:
            kwargs['roleAssignmentId'] = roleAssignmentId

        request = self.client.roleAssignments().delete(**kwargs) # type: ignore
        return request.execute()

    async def role_assignments_get(
        self,
        customer: str,
        roleAssignmentId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a role assignment.

        HTTP GET admin/directory/v1/customer/{customer}/roleassignments/{roleAssignmentId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            roleAssignmentId (str, required): Immutable ID of the role assignment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if roleAssignmentId is not None:
            kwargs['roleAssignmentId'] = roleAssignmentId

        request = self.client.roleAssignments().get(**kwargs) # type: ignore
        return request.execute()

    async def role_assignments_insert(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a role assignment.

        HTTP POST admin/directory/v1/customer/{customer}/roleassignments

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.roleAssignments().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roleAssignments().insert(**kwargs) # type: ignore
        return request.execute()

    async def role_assignments_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        roleId: Optional[str] = None,
        userKey: Optional[str] = None,
        includeIndirectRoleAssignments: Optional[bool] = None
    ) -> Dict[str, Any]:
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
        if customer is not None:
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
        return request.execute()

    async def resources_buildings_delete(
        self,
        customer: str,
        buildingId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a building.

        HTTP DELETE admin/directory/v1/customer/{customer}/resources/buildings/{buildingId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            buildingId (str, required): The id of the building to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if buildingId is not None:
            kwargs['buildingId'] = buildingId

        request = self.client.resources_buildings().delete(**kwargs) # type: ignore
        return request.execute()

    async def resources_buildings_get(
        self,
        customer: str,
        buildingId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a building.

        HTTP GET admin/directory/v1/customer/{customer}/resources/buildings/{buildingId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            buildingId (str, required): The unique ID of the building to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if buildingId is not None:
            kwargs['buildingId'] = buildingId

        request = self.client.resources_buildings().get(**kwargs) # type: ignore
        return request.execute()

    async def resources_buildings_insert(
        self,
        customer: str,
        coordinatesSource: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a building.

        HTTP POST admin/directory/v1/customer/{customer}/resources/buildings

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            coordinatesSource (str, optional): Source from which Building.coordinates are derived.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if coordinatesSource is not None:
            kwargs['coordinatesSource'] = coordinatesSource

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_buildings().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_buildings().insert(**kwargs) # type: ignore
        return request.execute()

    async def resources_buildings_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a list of buildings for an account.

        HTTP GET admin/directory/v1/customer/{customer}/resources/buildings

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            maxResults (int, optional): Maximum number of results to return.
            pageToken (str, optional): Token to specify the next page in the list.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.resources_buildings().list(**kwargs) # type: ignore
        return request.execute()

    async def resources_buildings_update(
        self,
        customer: str,
        buildingId: str,
        coordinatesSource: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a building.

        HTTP PUT admin/directory/v1/customer/{customer}/resources/buildings/{buildingId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            buildingId (str, required): The id of the building to update.
            coordinatesSource (str, optional): Source from which Building.coordinates are derived.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if buildingId is not None:
            kwargs['buildingId'] = buildingId
        if coordinatesSource is not None:
            kwargs['coordinatesSource'] = coordinatesSource

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_buildings().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_buildings().update(**kwargs) # type: ignore
        return request.execute()

    async def resources_buildings_patch(
        self,
        customer: str,
        buildingId: str,
        coordinatesSource: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Patches a building.

        HTTP PATCH admin/directory/v1/customer/{customer}/resources/buildings/{buildingId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            buildingId (str, required): The id of the building to update.
            coordinatesSource (str, optional): Source from which Building.coordinates are derived.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if buildingId is not None:
            kwargs['buildingId'] = buildingId
        if coordinatesSource is not None:
            kwargs['coordinatesSource'] = coordinatesSource

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_buildings().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_buildings().patch(**kwargs) # type: ignore
        return request.execute()

    async def resources_calendars_delete(
        self,
        customer: str,
        calendarResourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a calendar resource.

        HTTP DELETE admin/directory/v1/customer/{customer}/resources/calendars/{calendarResourceId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            calendarResourceId (str, required): The unique ID of the calendar resource to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if calendarResourceId is not None:
            kwargs['calendarResourceId'] = calendarResourceId

        request = self.client.resources_calendars().delete(**kwargs) # type: ignore
        return request.execute()

    async def resources_calendars_get(
        self,
        customer: str,
        calendarResourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a calendar resource.

        HTTP GET admin/directory/v1/customer/{customer}/resources/calendars/{calendarResourceId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            calendarResourceId (str, required): The unique ID of the calendar resource to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if calendarResourceId is not None:
            kwargs['calendarResourceId'] = calendarResourceId

        request = self.client.resources_calendars().get(**kwargs) # type: ignore
        return request.execute()

    async def resources_calendars_insert(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a calendar resource.

        HTTP POST admin/directory/v1/customer/{customer}/resources/calendars

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_calendars().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_calendars().insert(**kwargs) # type: ignore
        return request.execute()

    async def resources_calendars_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        query: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a list of calendar resources for an account.

        HTTP GET admin/directory/v1/customer/{customer}/resources/calendars

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            maxResults (int, optional): Maximum number of results to return.
            orderBy (str, optional): Field(s) to sort results by in either ascending or descending order. Supported fields include `resourceId`, `resourceName`, `capacity`, `buildingId`, and `floorName`. If no order is specified, defaults to ascending. Should be of the form "field [asc|desc], field [asc|desc], ...". For example `buildingId, capacity desc` would return results sorted first by `buildingId` in ascending order then by `capacity` in descending order.
            pageToken (str, optional): Token to specify the next page in the list.
            query (str, optional): String query used to filter results. Should be of the form "field operator value" where field can be any of supported fields and operators can be any of supported operations. Operators include '=' for exact match, '!=' for mismatch and ':' for prefix match or HAS match where applicable. For prefix match, the value should always be followed by a *. Logical operators NOT and AND are supported (in this order of precedence). Supported fields include `generatedResourceName`, `name`, `buildingId`, `floor_name`, `capacity`, `featureInstances.feature.name`, `resourceEmail`, `resourceCategory`. For example `buildingId=US-NYC-9TH AND featureInstances.feature.name:Phone`.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if query is not None:
            kwargs['query'] = query

        request = self.client.resources_calendars().list(**kwargs) # type: ignore
        return request.execute()

    async def resources_calendars_update(
        self,
        customer: str,
        calendarResourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a calendar resource. This method supports patch semantics, meaning you only need to include the fields you wish to update. Fields that are not present in the request will be preserved.

        HTTP PUT admin/directory/v1/customer/{customer}/resources/calendars/{calendarResourceId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            calendarResourceId (str, required): The unique ID of the calendar resource to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if calendarResourceId is not None:
            kwargs['calendarResourceId'] = calendarResourceId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_calendars().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_calendars().update(**kwargs) # type: ignore
        return request.execute()

    async def resources_calendars_patch(
        self,
        customer: str,
        calendarResourceId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Patches a calendar resource.

        HTTP PATCH admin/directory/v1/customer/{customer}/resources/calendars/{calendarResourceId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            calendarResourceId (str, required): The unique ID of the calendar resource to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if calendarResourceId is not None:
            kwargs['calendarResourceId'] = calendarResourceId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_calendars().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_calendars().patch(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_delete(
        self,
        customer: str,
        featureKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a feature.

        HTTP DELETE admin/directory/v1/customer/{customer}/resources/features/{featureKey}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            featureKey (str, required): The unique ID of the feature to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if featureKey is not None:
            kwargs['featureKey'] = featureKey

        request = self.client.resources_features().delete(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_get(
        self,
        customer: str,
        featureKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a feature.

        HTTP GET admin/directory/v1/customer/{customer}/resources/features/{featureKey}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            featureKey (str, required): The unique ID of the feature to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if featureKey is not None:
            kwargs['featureKey'] = featureKey

        request = self.client.resources_features().get(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_insert(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Inserts a feature.

        HTTP POST admin/directory/v1/customer/{customer}/resources/features

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_features().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_features().insert(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a list of features for an account.

        HTTP GET admin/directory/v1/customer/{customer}/resources/features

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            maxResults (int, optional): Maximum number of results to return.
            pageToken (str, optional): Token to specify the next page in the list.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.resources_features().list(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_rename(
        self,
        customer: str,
        oldName: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Renames a feature.

        HTTP POST admin/directory/v1/customer/{customer}/resources/features/{oldName}/rename

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            oldName (str, required): The unique ID of the feature to rename.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if oldName is not None:
            kwargs['oldName'] = oldName

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_features().rename(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_features().rename(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_update(
        self,
        customer: str,
        featureKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a feature.

        HTTP PUT admin/directory/v1/customer/{customer}/resources/features/{featureKey}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            featureKey (str, required): The unique ID of the feature to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if featureKey is not None:
            kwargs['featureKey'] = featureKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_features().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_features().update(**kwargs) # type: ignore
        return request.execute()

    async def resources_features_patch(
        self,
        customer: str,
        featureKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Patches a feature.

        HTTP PATCH admin/directory/v1/customer/{customer}/resources/features/{featureKey}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. As an account administrator, you can also use the `my_customer` alias to represent your account's customer ID.
            featureKey (str, required): The unique ID of the feature to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if featureKey is not None:
            kwargs['featureKey'] = featureKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.resources_features().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.resources_features().patch(**kwargs) # type: ignore
        return request.execute()

    async def roles_delete(
        self,
        customer: str,
        roleId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a role.

        HTTP DELETE admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if roleId is not None:
            kwargs['roleId'] = roleId

        request = self.client.roles().delete(**kwargs) # type: ignore
        return request.execute()

    async def roles_get(
        self,
        customer: str,
        roleId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a role.

        HTTP GET admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if roleId is not None:
            kwargs['roleId'] = roleId

        request = self.client.roles().get(**kwargs) # type: ignore
        return request.execute()

    async def roles_insert(
        self,
        customer: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a role.

        HTTP POST admin/directory/v1/customer/{customer}/roles

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.roles().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roles().insert(**kwargs) # type: ignore
        return request.execute()

    async def roles_list(
        self,
        customer: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
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
        if customer is not None:
            kwargs['customer'] = customer
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.roles().list(**kwargs) # type: ignore
        return request.execute()

    async def roles_update(
        self,
        customer: str,
        roleId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a role.

        HTTP PUT admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if roleId is not None:
            kwargs['roleId'] = roleId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.roles().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roles().update(**kwargs) # type: ignore
        return request.execute()

    async def roles_patch(
        self,
        customer: str,
        roleId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Patches a role.

        HTTP PATCH admin/directory/v1/customer/{customer}/roles/{roleId}

        Args:
            customer (str, required): Immutable ID of the Google Workspace account.
            roleId (str, required): Immutable ID of the role.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customer is not None:
            kwargs['customer'] = customer
        if roleId is not None:
            kwargs['roleId'] = roleId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.roles().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.roles().patch(**kwargs) # type: ignore
        return request.execute()

    async def schemas_delete(
        self,
        customerId: str,
        schemaKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a schema.

        HTTP DELETE admin/directory/v1/customer/{customerId}/schemas/{schemaKey}

        Args:
            customerId (str, required): Immutable ID of the Google Workspace account.
            schemaKey (str, required): Name or immutable ID of the schema.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if schemaKey is not None:
            kwargs['schemaKey'] = schemaKey

        request = self.client.schemas().delete(**kwargs) # type: ignore
        return request.execute()

    async def schemas_get(
        self,
        customerId: str,
        schemaKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a schema.

        HTTP GET admin/directory/v1/customer/{customerId}/schemas/{schemaKey}

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            schemaKey (str, required): Name or immutable ID of the schema.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if schemaKey is not None:
            kwargs['schemaKey'] = schemaKey

        request = self.client.schemas().get(**kwargs) # type: ignore
        return request.execute()

    async def schemas_insert(
        self,
        customerId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a schema.

        HTTP POST admin/directory/v1/customer/{customerId}/schemas

        Args:
            customerId (str, required): Immutable ID of the Google Workspace account.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.schemas().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.schemas().insert(**kwargs) # type: ignore
        return request.execute()

    async def schemas_list(
        self,
        customerId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves all schemas for a customer.

        HTTP GET admin/directory/v1/customer/{customerId}/schemas

        Args:
            customerId (str, required): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all groups for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId

        request = self.client.schemas().list(**kwargs) # type: ignore
        return request.execute()

    async def schemas_patch(
        self,
        customerId: str,
        schemaKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Patches a schema.

        HTTP PATCH admin/directory/v1/customer/{customerId}/schemas/{schemaKey}

        Args:
            customerId (str, required): Immutable ID of the Google Workspace account.
            schemaKey (str, required): Name or immutable ID of the schema.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if schemaKey is not None:
            kwargs['schemaKey'] = schemaKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.schemas().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.schemas().patch(**kwargs) # type: ignore
        return request.execute()

    async def schemas_update(
        self,
        customerId: str,
        schemaKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a schema.

        HTTP PUT admin/directory/v1/customer/{customerId}/schemas/{schemaKey}

        Args:
            customerId (str, required): Immutable ID of the Google Workspace account.
            schemaKey (str, required): Name or immutable ID of the schema.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customerId is not None:
            kwargs['customerId'] = customerId
        if schemaKey is not None:
            kwargs['schemaKey'] = schemaKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.schemas().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.schemas().update(**kwargs) # type: ignore
        return request.execute()

    async def tokens_delete(
        self,
        userKey: str,
        clientId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes all access tokens issued by a user for an application.

        HTTP DELETE admin/directory/v1/users/{userKey}/tokens/{clientId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            clientId (str, required): The Client ID of the application the token is issued to.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if clientId is not None:
            kwargs['clientId'] = clientId

        request = self.client.tokens().delete(**kwargs) # type: ignore
        return request.execute()

    async def tokens_get(
        self,
        userKey: str,
        clientId: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Gets information about an access token issued by a user.

        HTTP GET admin/directory/v1/users/{userKey}/tokens/{clientId}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            clientId (str, required): The Client ID of the application the token is issued to.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if clientId is not None:
            kwargs['clientId'] = clientId

        request = self.client.tokens().get(**kwargs) # type: ignore
        return request.execute()

    async def tokens_list(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Returns the set of tokens specified user has issued to 3rd party applications.

        HTTP GET admin/directory/v1/users/{userKey}/tokens

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.tokens().list(**kwargs) # type: ignore
        return request.execute()

    async def two_step_verification_turn_off(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Turns off 2-Step Verification for user.

        HTTP POST admin/directory/v1/users/{userKey}/twoStepVerification/turnOff

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.twoStepVerification().turnOff(**kwargs, body=body) # type: ignore
        else:
            request = self.client.twoStepVerification().turnOff(**kwargs) # type: ignore
        return request.execute()

    async def users_delete(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Deletes a user.

        HTTP DELETE admin/directory/v1/users/{userKey}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.users().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_get(
        self,
        userKey: str,
        customFieldMask: Optional[str] = None,
        projection: Optional[str] = None,
        viewType: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a user.

        HTTP GET admin/directory/v1/users/{userKey}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            customFieldMask (str, optional): A comma-separated list of schema names. All fields from these schemas are fetched. This should only be set when `projection=custom`.
            projection (str, optional): What subset of fields to fetch for this user.
            viewType (str, optional): Whether to fetch the administrator-only or domain-wide public view of the user. For more information, see [Retrieve a user as a non-administrator](https://developers.google.com/workspace/admin/directory/v1/guides/manage-users#retrieve_users_non_admin).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if customFieldMask is not None:
            kwargs['customFieldMask'] = customFieldMask
        if projection is not None:
            kwargs['projection'] = projection
        if viewType is not None:
            kwargs['viewType'] = viewType

        request = self.client.users().get(**kwargs) # type: ignore
        return request.execute()

    async def users_insert(
        self,
        resolveConflictAccount: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Creates a user. Mutate calls immediately following user creation might sometimes fail as the user isn't fully created due to propagation delay in our backends. Check the error details for the "User creation is not complete" message to see if this is the case. Retrying the calls after some time can help in this case. If `resolveConflictAccount` is set to `true`, a `202` response code means that a conflicting unmanaged account exists and was invited to join the organization. A `409` response code means that a conflicting account exists so the user wasn't created based on the [handling unmanaged user accounts](https://support.google.com/a/answer/11112794) option selected.

        HTTP POST admin/directory/v1/users

        Args:
            resolveConflictAccount (bool, optional): Optional. If set to `true`, the option selected for [handling unmanaged user accounts](https://support.google.com/a/answer/11112794) will apply. Default: `false`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if resolveConflictAccount is not None:
            kwargs['resolveConflictAccount'] = resolveConflictAccount

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().insert(**kwargs) # type: ignore
        return request.execute()

    async def users_list(
        self,
        customFieldMask: Optional[str] = None,
        customer: Optional[str] = None,
        domain: Optional[str] = None,
        event: Optional[str] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        projection: Optional[str] = None,
        query: Optional[str] = None,
        showDeleted: Optional[str] = None,
        sortOrder: Optional[str] = None,
        viewType: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves a paginated list of either deleted users or all users in a domain.

        HTTP GET admin/directory/v1/users

        Args:
            customFieldMask (str, optional): A comma-separated list of schema names. All fields from these schemas are fetched. This should only be set when `projection=custom`.
            customer (str, optional): The unique ID for the customer's Google Workspace account. In case of a multi-domain account, to fetch all users for a customer, use this field instead of `domain`. You can also use the `my_customer` alias to represent your account's `customerId`. The `customerId` is also returned as part of the [Users](https://developers.google.com/workspace/admin/directory/v1/reference/users) resource. You must provide either the `customer` or the `domain` parameter.
            domain (str, optional): The domain name. Use this field to get users from only one domain. To return all domains for a customer account, use the `customer` query parameter instead. Either the `customer` or the `domain` parameter must be provided.
            event (str, optional): Event on which subscription is intended (if subscribing)
            maxResults (int, optional): Maximum number of results to return.
            orderBy (str, optional): Property to use for sorting results.
            pageToken (str, optional): Token to specify next page in the list. The page token is only valid for three days.
            projection (str, optional): What subset of fields to fetch for this user.
            query (str, optional): Query string for searching user fields. For more information on constructing user queries, see [Search for Users](https://developers.google.com/workspace/admin/directory/v1/guides/search-users).
            showDeleted (str, optional): If set to `true`, retrieves the list of deleted users. (Default: `false`)
            sortOrder (str, optional): Whether to return results in ascending or descending order, ignoring case.
            viewType (str, optional): Whether to fetch the administrator-only or domain-wide public view of the user. For more information, see [Retrieve a user as a non-administrator](https://developers.google.com/workspace/admin/directory/v1/guides/manage-users#retrieve_users_non_admin).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if customFieldMask is not None:
            kwargs['customFieldMask'] = customFieldMask
        if customer is not None:
            kwargs['customer'] = customer
        if domain is not None:
            kwargs['domain'] = domain
        if event is not None:
            kwargs['event'] = event
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if projection is not None:
            kwargs['projection'] = projection
        if query is not None:
            kwargs['query'] = query
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if sortOrder is not None:
            kwargs['sortOrder'] = sortOrder
        if viewType is not None:
            kwargs['viewType'] = viewType

        request = self.client.users().list(**kwargs) # type: ignore
        return request.execute()

    async def users_make_admin(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Makes a user a super administrator.

        HTTP POST admin/directory/v1/users/{userKey}/makeAdmin

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().makeAdmin(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().makeAdmin(**kwargs) # type: ignore
        return request.execute()

    async def users_patch(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a user using patch semantics. The update method should be used instead, because it also supports patch semantics and has better performance. If you're mapping an external identity to a Google identity, use the [`update`](https://developers.google.com/workspace/admin/directory/v1/reference/users/update) method instead of the `patch` method. This method is unable to clear fields that contain repeated objects (`addresses`, `phones`, etc). Use the update method instead.

        HTTP PATCH admin/directory/v1/users/{userKey}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().patch(**kwargs) # type: ignore
        return request.execute()

    async def users_undelete(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Undeletes a deleted user.

        HTTP POST admin/directory/v1/users/{userKey}/undelete

        Args:
            userKey (str, required): The immutable id of the user

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().undelete(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().undelete(**kwargs) # type: ignore
        return request.execute()

    async def users_update(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Updates a user. This method supports patch semantics, meaning that you only need to include the fields you wish to update. Fields that are not present in the request will be preserved, and fields set to `null` will be cleared. For repeating fields that contain arrays, individual items in the array can't be patched piecemeal; they must be supplied in the request body with the desired values for all items. See the [user accounts guide](https://developers.google.com/workspace/admin/directory/v1/guides/manage-users#update_user) for more information.

        HTTP PUT admin/directory/v1/users/{userKey}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().update(**kwargs) # type: ignore
        return request.execute()

    async def users_watch(
        self,
        domain: Optional[str] = None,
        customer: Optional[str] = None,
        event: Optional[str] = None,
        customFieldMask: Optional[str] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        projection: Optional[str] = None,
        query: Optional[str] = None,
        showDeleted: Optional[str] = None,
        sortOrder: Optional[str] = None,
        viewType: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Watches for changes in users list.

        HTTP POST admin/directory/v1/users/watch

        Args:
            domain (str, optional): Name of the domain. Fill this field to get users from only this domain. To return all users in a multi-domain fill customer field instead."
            customer (str, optional): Immutable ID of the Google Workspace account. In case of multi-domain, to fetch all users for a customer, fill this field instead of domain.
            event (str, optional): Events to watch for.
            customFieldMask (str, optional): Comma-separated list of schema names. All fields from these schemas are fetched. This should only be set when projection=custom.
            maxResults (int, optional): Maximum number of results to return.
            orderBy (str, optional): Column to use for sorting results
            pageToken (str, optional): Token to specify next page in the list
            projection (str, optional): What subset of fields to fetch for this user.
            query (str, optional): Query string search. Should be of the form "". Complete documentation is at https: //developers.google.com/admin-sdk/directory/v1/guides/search-users
            showDeleted (str, optional): If set to true, retrieves the list of deleted users. (Default: false)
            sortOrder (str, optional): Whether to return results in ascending or descending order.
            viewType (str, optional): Whether to fetch the administrator-only or domain-wide public view of the user. For more information, see [Retrieve a user as a non-administrator](https://developers.google.com/workspace/admin/directory/v1/guides/manage-users#retrieve_users_non_admin).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if domain is not None:
            kwargs['domain'] = domain
        if customer is not None:
            kwargs['customer'] = customer
        if event is not None:
            kwargs['event'] = event
        if customFieldMask is not None:
            kwargs['customFieldMask'] = customFieldMask
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if projection is not None:
            kwargs['projection'] = projection
        if query is not None:
            kwargs['query'] = query
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if sortOrder is not None:
            kwargs['sortOrder'] = sortOrder
        if viewType is not None:
            kwargs['viewType'] = viewType

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().watch(**kwargs) # type: ignore
        return request.execute()

    async def users_sign_out(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Signs a user out of all web and device sessions and reset their sign-in cookies. User will have to sign in by authenticating again.

        HTTP POST admin/directory/v1/users/{userKey}/signOut

        Args:
            userKey (str, required): Identifies the target user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().signOut(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().signOut(**kwargs) # type: ignore
        return request.execute()

    async def users_aliases_delete(
        self,
        userKey: str,
        alias: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Removes an alias.

        HTTP DELETE admin/directory/v1/users/{userKey}/aliases/{alias}

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            alias (str, required): The alias to be removed.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if alias is not None:
            kwargs['alias'] = alias

        request = self.client.users_aliases().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_aliases_insert(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Adds an alias.

        HTTP POST admin/directory/v1/users/{userKey}/aliases

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_aliases().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_aliases().insert(**kwargs) # type: ignore
        return request.execute()

    async def users_aliases_list(
        self,
        userKey: str,
        event: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Lists all aliases for a user.

        HTTP GET admin/directory/v1/users/{userKey}/aliases

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.
            event (str, optional): Events to watch for.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if event is not None:
            kwargs['event'] = event

        request = self.client.users_aliases().list(**kwargs) # type: ignore
        return request.execute()

    async def users_aliases_watch(
        self,
        userKey: str,
        event: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Watches for changes in users list.

        HTTP POST admin/directory/v1/users/{userKey}/aliases/watch

        Args:
            userKey (str, required): Email or immutable ID of the user
            event (str, optional): Events to watch for.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey
        if event is not None:
            kwargs['event'] = event

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_aliases().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_aliases().watch(**kwargs) # type: ignore
        return request.execute()

    async def users_photos_delete(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Removes the user's photo.

        HTTP DELETE admin/directory/v1/users/{userKey}/photos/thumbnail

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.users_photos().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_photos_get(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Retrieves the user's photo.

        HTTP GET admin/directory/v1/users/{userKey}/photos/thumbnail

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.users_photos().get(**kwargs) # type: ignore
        return request.execute()

    async def users_photos_update(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Adds a photo for the user.

        HTTP PUT admin/directory/v1/users/{userKey}/photos/thumbnail

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_photos().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_photos().update(**kwargs) # type: ignore
        return request.execute()

    async def users_photos_patch(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Adds a photo for the user. This method supports [patch semantics](https://developers.google.com/workspace/admin/directory/v1/guides/performance#patch).

        HTTP PATCH admin/directory/v1/users/{userKey}/photos/thumbnail

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_photos().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_photos().patch(**kwargs) # type: ignore
        return request.execute()

    async def verification_codes_generate(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Generates new backup verification codes for the user.

        HTTP POST admin/directory/v1/users/{userKey}/verificationCodes/generate

        Args:
            userKey (str, required): Email or immutable ID of the user

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.verificationCodes().generate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.verificationCodes().generate(**kwargs) # type: ignore
        return request.execute()

    async def verification_codes_invalidate(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Invalidates the current backup verification codes for the user.

        HTTP POST admin/directory/v1/users/{userKey}/verificationCodes/invalidate

        Args:
            userKey (str, required): Email or immutable ID of the user

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.verificationCodes().invalidate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.verificationCodes().invalidate(**kwargs) # type: ignore
        return request.execute()

    async def verification_codes_list(
        self,
        userKey: str
    ) -> Dict[str, Any]:
        """Google Admin SDK Directory API: Returns the current set of valid backup verification codes for the specified user.

        HTTP GET admin/directory/v1/users/{userKey}/verificationCodes

        Args:
            userKey (str, required): Identifies the user in the API request. The value can be the user's primary email address, alias email address, or unique user ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userKey is not None:
            kwargs['userKey'] = userKey

        request = self.client.verificationCodes().list(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
