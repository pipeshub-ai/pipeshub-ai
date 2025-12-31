from typing import Any, Dict, List, Optional, Union
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.salesforce.salesforce import SalesforceClient, SalesforceResponse


class SalesforceDataSource:
    """Comprehensive Salesforce API Data Source.
    
    Covers:
    - Core CRM SObjects (Account, Contact, Lead, Opportunity, Custom objects)
    - Query & Search (SOQL, SOSL)
    - Composite API (Tree, Batch, Collections) for efficient bulk operations
    - Bulk API v2 for large data sets
    - UI API for layout and list view metadata
    - System Limits & Info
    """

    def __init__(self, client: SalesforceClient):
        self.client = client.get_client()
        self.base_url = client.get_base_url()

    async def query(self, q: str) -> SalesforceResponse:
        """Execute a SOQL query

        Args:
            q: The SOQL query string

        Returns:
            SalesforceResponse: API response object
        """
        path = "/query"
        params = {}
        if q is not None:
            params['q'] = q

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def query_all(self, q: str) -> SalesforceResponse:
        """Execute a SOQL query (includes deleted/archived records)

        Args:
            q: The SOQL query string

        Returns:
            SalesforceResponse: API response object
        """
        path = "/queryAll"
        params = {}
        if q is not None:
            params['q'] = q

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def search(self, q: str) -> SalesforceResponse:
        """Execute a SOSL search

        Args:
            q: The SOSL search string

        Returns:
            SalesforceResponse: API response object
        """
        path = "/search"
        params = {}
        if q is not None:
            params['q'] = q

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def describe_global(self) -> SalesforceResponse:
        """Lists available objects and their metadata

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects"

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def describe_sobject(self, sobject: str) -> SalesforceResponse:
        """Completely describes the individual metadata at all levels for the specified object

        Args:
            sobject: Object name (e.g., Account)

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/describe".format(sobject=sobject)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_sobject_info(self, sobject: str) -> SalesforceResponse:
        """Retrieves basic metadata for a specific object

        Args:
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}".format(sobject=sobject)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def create_record(self, data: Dict[str, Any], sobject: str) -> SalesforceResponse:
        """Create a new record

        Args:
            data: JSON data for the record
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}".format(sobject=sobject)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='POST',
            url=url,
            headers=headers,
            body=data
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_record(self, record_id: str, sobject: str, fields: Optional[List[str]] = None) -> SalesforceResponse:
        """Retrieve a record by ID

        Args:
            record_id: Record ID
            sobject: Object name
            fields: List of fields to return

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/{record_id}".format(sobject=sobject, record_id=record_id)
        params = {}
        if fields is not None:
            params['fields'] = ','.join(fields)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def update_record(self, data: Dict[str, Any], record_id: str, sobject: str) -> SalesforceResponse:
        """Update a record by ID

        Args:
            data: Fields to update
            record_id: Record ID
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/{record_id}".format(sobject=sobject, record_id=record_id)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='PATCH',
            url=url,
            headers=headers,
            body=data
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def delete_record(self, record_id: str, sobject: str) -> SalesforceResponse:
        """Delete a record by ID

        Args:
            record_id: Record ID
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/{record_id}".format(sobject=sobject, record_id=record_id)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='DELETE',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def upsert_record(self, data: Dict[str, Any], external_id: str, external_id_field: str, sobject: str) -> SalesforceResponse:
        """Upsert a record using an external ID

        Args:
            data: Record data
            external_id: External ID value
            external_id_field: External ID field name
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/{external_id_field}/{external_id}".format(sobject=sobject, external_id_field=external_id_field, external_id=external_id)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='PATCH',
            url=url,
            headers=headers,
            body=data
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_record_blob(self, blob_field: str, record_id: str, sobject: str) -> SalesforceResponse:
        """Retrieves the specified blob field (e.g., Body on Attachment) for a record

        Args:
            blob_field: Name of the blob field
            record_id: Record ID
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/{record_id}/{blob_field}".format(sobject=sobject, record_id=record_id, blob_field=blob_field)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_records_collection(self, ids: List[str], sobject: str, fields: Optional[List[str]] = None) -> SalesforceResponse:
        """Retrieve multiple records by ID

        Args:
            ids: List of record IDs
            sobject: Object name
            fields: List of fields to return

        Returns:
            SalesforceResponse: API response object
        """
        path = "/composite/sobjects/{sobject}".format(sobject=sobject)
        params = {}
        if ids is not None:
            params['ids'] = ','.join(ids)
        if fields is not None:
            params['fields'] = ','.join(fields)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def create_records_collection(self, records: List[Dict[str, Any]], all_or_none: Optional[bool] = None) -> SalesforceResponse:
        """Create up to 200 records

        Args:
            records: List of SObject records to create (must include attributes.type)
            all_or_none: Rollback on any error

        Returns:
            SalesforceResponse: API response object
        """
        path = "/composite/sobjects"
        body_payload = {}
        if records is not None:
            body_payload['records'] = records
        if all_or_none is not None:
            body_payload['all_or_none'] = all_or_none

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='POST',
            url=url,
            headers=headers,
            body=body_payload
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def update_records_collection(self, records: List[Dict[str, Any]], all_or_none: Optional[bool] = None) -> SalesforceResponse:
        """Update up to 200 records

        Args:
            records: List of SObject records to update (must include id)
            all_or_none: Rollback on any error

        Returns:
            SalesforceResponse: API response object
        """
        path = "/composite/sobjects"
        body_payload = {}
        if records is not None:
            body_payload['records'] = records
        if all_or_none is not None:
            body_payload['all_or_none'] = all_or_none

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='PATCH',
            url=url,
            headers=headers,
            body=body_payload
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def delete_records_collection(self, ids: List[str], all_or_none: Optional[bool] = None) -> SalesforceResponse:
        """Delete up to 200 records

        Args:
            ids: List of record IDs to delete
            all_or_none: Rollback on any error

        Returns:
            SalesforceResponse: API response object
        """
        path = "/composite/sobjects"
        params = {}
        if ids is not None:
            params['ids'] = ','.join(ids)
        if all_or_none is not None:
            params['all_or_none'] = all_or_none

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='DELETE',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_updated_records(self, end: str, sobject: str, start: str) -> SalesforceResponse:
        """Get IDs of records updated in a time range

        Args:
            end: End time (ISO 8601)
            sobject: Object name
            start: Start time (ISO 8601)

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/updated".format(sobject=sobject)
        params = {}
        if start is not None:
            params['start'] = start
        if end is not None:
            params['end'] = end

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_deleted_records(self, end: str, sobject: str, start: str) -> SalesforceResponse:
        """Get IDs of records deleted in a time range

        Args:
            end: End time (ISO 8601)
            sobject: Object name
            start: Start time (ISO 8601)

        Returns:
            SalesforceResponse: API response object
        """
        path = "/sobjects/{sobject}/deleted".format(sobject=sobject)
        params = {}
        if start is not None:
            params['start'] = start
        if end is not None:
            params['end'] = end

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def composite_batch(self, batch_requests: List[Dict[str, Any]], halt_on_error: Optional[bool] = None) -> SalesforceResponse:
        """Execute up to 25 subrequests in a single batch

        Args:
            batch_requests: List of subrequests
            halt_on_error: Stop processing on error

        Returns:
            SalesforceResponse: API response object
        """
        path = "/composite/batch"
        body_payload = {}
        if batch_requests is not None:
            body_payload['batch_requests'] = batch_requests
        if halt_on_error is not None:
            body_payload['halt_on_error'] = halt_on_error

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='POST',
            url=url,
            headers=headers,
            body=body_payload
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def create_sobject_tree(self, records: List[Dict[str, Any]], sobject: str) -> SalesforceResponse:
        """Create a tree of SObjects (up to 200 records)

        Args:
            records: Record tree data
            sobject: Root object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/composite/tree/{sobject}".format(sobject=sobject)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='POST',
            url=url,
            headers=headers,
            body=records
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def create_job(self, object: str, operation: str, contentType: Optional[str] = None) -> SalesforceResponse:
        """Create a new Bulk v2 ingest job

        Args:
            object: Object type
            operation: Operation (insert, delete, update, upsert)
            contentType: CSV (default)

        Returns:
            SalesforceResponse: API response object
        """
        path = "/jobs/ingest"
        body_payload = {}
        if object is not None:
            body_payload['object'] = object
        if operation is not None:
            body_payload['operation'] = operation
        if contentType is not None:
            body_payload['contentType'] = contentType

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='POST',
            url=url,
            headers=headers,
            body=body_payload
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_job_info(self, job_id: str) -> SalesforceResponse:
        """Get details about a bulk job

        Args:
            job_id: Job ID

        Returns:
            SalesforceResponse: API response object
        """
        path = "/jobs/ingest/{job_id}".format(job_id=job_id)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def close_job(self, job_id: str, state: str) -> SalesforceResponse:
        """Close a bulk job (marks it as UploadComplete)

        Args:
            job_id: Job ID
            state: Set to "UploadComplete"

        Returns:
            SalesforceResponse: API response object
        """
        path = "/jobs/ingest/{job_id}".format(job_id=job_id)
        body_payload = {}
        if state is not None:
            body_payload['state'] = state

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='PATCH',
            url=url,
            headers=headers,
            body=body_payload
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def put_job_data(self, content: str, job_id: str) -> SalesforceResponse:
        """Upload CSV data for a bulk job

        Args:
            content: CSV Content (String)
            job_id: Job ID

        Returns:
            SalesforceResponse: API response object
        """
        path = "/jobs/ingest/{job_id}/batches".format(job_id=job_id)
        # Body is raw string content

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='PUT',
            url=url,
            headers=headers,
            body=content
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_list_views(self, sobject: str) -> SalesforceResponse:
        """Get list views for an object

        Args:
            sobject: Object name

        Returns:
            SalesforceResponse: API response object
        """
        path = "/ui-api/object-info/{sobject}/list-views".format(sobject=sobject)

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_list_view_records(self, list_view_id: str, pageSize: Optional[int] = None) -> SalesforceResponse:
        """Get records and metadata for a specific list view

        Args:
            list_view_id: List View ID
            pageSize: Number of records per page

        Returns:
            SalesforceResponse: API response object
        """
        path = "/ui-api/list-ui/{list_view_id}".format(list_view_id=list_view_id)
        params = {}
        if pageSize is not None:
            params['pageSize'] = pageSize

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_record_ui(self, record_ids: List[str], layoutTypes: Optional[str] = None) -> SalesforceResponse:
        """Get layout information and data for specific records

        Args:
            record_ids: Comma separated record IDs
            layoutTypes: Full or Compact

        Returns:
            SalesforceResponse: API response object
        """
        if isinstance(record_ids, list):
            record_ids = ','.join(record_ids)
        path = "/ui-api/record-ui/{record_ids}".format(record_ids=record_ids)
        params = {}
        if layoutTypes is not None:
            params['layoutTypes'] = layoutTypes

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_favorites(self) -> SalesforceResponse:
        """Get user favorites

        Returns:
            SalesforceResponse: API response object
        """
        path = "/ui-api/favorites"

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def get_limits(self) -> SalesforceResponse:
        """Lists information about organization limits

        Returns:
            SalesforceResponse: API response object
        """
        path = "/limits"

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))

    async def recent_items(self, limit: Optional[int] = None) -> SalesforceResponse:
        """Get recently viewed items

        Args:
            limit: Max items to return

        Returns:
            SalesforceResponse: API response object
        """
        path = "/recent"
        params = {}
        if limit is not None:
            params['limit'] = limit

        # Construct full URL
        url = self.base_url + path

        headers = self.client.headers.copy()
        
        request = HTTPRequest(
            method='GET',
            url=url,
            headers=headers,
            query_params=params,
        )

        try:
            response = await self.client.execute(request)
            # Salesforce API usually returns 200/201/204
            # 204 No Content means success (e.g. DELETE)
            data = response.json() if response.status != 204 and response.text() else {}
            return SalesforceResponse(success=response.status < 300, data=data)
        except Exception as e:
            return SalesforceResponse(success=False, error=str(e))
