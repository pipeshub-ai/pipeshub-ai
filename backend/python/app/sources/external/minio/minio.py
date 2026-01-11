import asyncio
from typing import Any, Dict, Optional, Union

try:
    import aioboto3  # type: ignore
    from botocore.config import Config  # type: ignore
    from botocore.exceptions import ClientError  # type: ignore
except ImportError:
    raise ImportError("aioboto3 is not installed. Please install it with `pip install aioboto3`")

from app.sources.client.minio.minio import MinIOClient
from app.sources.client.s3.s3 import S3Response


class MinIODataSource:
    """
    MinIO S3-compatible API client wrapper using aioboto3.

    This class provides the same interface as S3DataSource but connects to a MinIO
    server instead of AWS S3. The key difference is the use of a custom endpoint_url
    and optional SSL configuration.

    Inherits the same method signatures as S3DataSource for S3-compatible operations.
    """

    def __init__(self, minio_client: MinIOClient) -> None:
        """Initialize with MinIOClient."""
        self._minio_client = minio_client
        self._session = None
        # Get endpoint configuration from client
        credentials = self._minio_client.get_credentials()
        self._endpoint_url = credentials.get('endpoint_url')
        self._use_ssl = credentials.get('use_ssl', True)
        self._verify_ssl = credentials.get('verify_ssl', True)

    async def _get_aioboto3_session(self) -> aioboto3.Session:  # type: ignore[valid-type]
        """Get or create the aioboto3 session."""
        if self._session is None:
            self._session = self._minio_client.get_session()
        return self._session

    def _get_client_kwargs(self) -> Dict[str, Any]:
        """Get client kwargs for MinIO connection (endpoint_url, ssl settings).

        Note: 'use_ssl' is not a valid parameter for boto3 session.client().
        SSL is determined by the protocol in endpoint_url (http vs https).
        'verify' controls SSL certificate verification.
        """
        return {
            'endpoint_url': self._endpoint_url,
            'verify': self._verify_ssl
        }

    def _handle_s3_response(self, response: object) -> S3Response:
        """Handle S3 API response with comprehensive error handling."""
        try:
            if response is None:
                return S3Response(success=False, error="Empty response from MinIO API")

            if isinstance(response, dict):
                if 'Error' in response:
                    error_info = response['Error']
                    error_code = error_info.get('Code', 'Unknown')
                    error_message = error_info.get('Message', 'No message')
                    return S3Response(success=False, error=f"{error_code}: {error_message}")
                return S3Response(success=True, data=response)

            return S3Response(success=True, data=response)

        except Exception as e:
            return S3Response(success=False, error=f"Response handling error: {str(e)}")

    # Core S3-compatible methods used by the connector

    async def list_buckets(self) -> S3Response:
        """MinIO List Buckets operation."""
        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.list_buckets()
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_objects_v2(
        self,
        Bucket: str,
        Delimiter: Optional[str] = None,
        EncodingType: Optional[str] = None,
        MaxKeys: Optional[int] = None,
        Prefix: Optional[str] = None,
        ContinuationToken: Optional[str] = None,
        FetchOwner: Optional[bool] = None,
        StartAfter: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None
    ) -> S3Response:
        """MinIO List Objects V2 operation."""
        kwargs = {'Bucket': Bucket}
        if Delimiter is not None:
            kwargs['Delimiter'] = Delimiter
        if EncodingType is not None:
            kwargs['EncodingType'] = EncodingType
        if MaxKeys is not None:
            kwargs['MaxKeys'] = MaxKeys
        if Prefix is not None:
            kwargs['Prefix'] = Prefix
        if ContinuationToken is not None:
            kwargs['ContinuationToken'] = ContinuationToken
        if FetchOwner is not None:
            kwargs['FetchOwner'] = FetchOwner
        if StartAfter is not None:
            kwargs['StartAfter'] = StartAfter
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.list_objects_v2(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_location(
        self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None
    ) -> S3Response:
        """MinIO Get Bucket Location operation.

        Note: MinIO may return None for location as it doesn't have AWS regions.
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.get_bucket_location(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def head_object(
        self,
        Bucket: str,
        Key: str,
        IfMatch: Optional[str] = None,
        IfModifiedSince: Optional[Any] = None,
        IfNoneMatch: Optional[str] = None,
        IfUnmodifiedSince: Optional[Any] = None,
        Range: Optional[str] = None,
        VersionId: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        PartNumber: Optional[int] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumMode: Optional[str] = None
    ) -> S3Response:
        """MinIO Head Object operation - get object metadata without downloading."""
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if IfMatch is not None:
            kwargs['IfMatch'] = IfMatch
        if IfModifiedSince is not None:
            kwargs['IfModifiedSince'] = IfModifiedSince
        if IfNoneMatch is not None:
            kwargs['IfNoneMatch'] = IfNoneMatch
        if IfUnmodifiedSince is not None:
            kwargs['IfUnmodifiedSince'] = IfUnmodifiedSince
        if Range is not None:
            kwargs['Range'] = Range
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if PartNumber is not None:
            kwargs['PartNumber'] = PartNumber
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if ChecksumMode is not None:
            kwargs['ChecksumMode'] = ChecksumMode

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.head_object(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object(
        self,
        Bucket: str,
        Key: str,
        IfMatch: Optional[str] = None,
        IfModifiedSince: Optional[Any] = None,
        IfNoneMatch: Optional[str] = None,
        IfUnmodifiedSince: Optional[Any] = None,
        Range: Optional[str] = None,
        ResponseCacheControl: Optional[str] = None,
        ResponseContentDisposition: Optional[str] = None,
        ResponseContentEncoding: Optional[str] = None,
        ResponseContentLanguage: Optional[str] = None,
        ResponseContentType: Optional[str] = None,
        ResponseExpires: Optional[Any] = None,
        VersionId: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        PartNumber: Optional[int] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumMode: Optional[str] = None
    ) -> S3Response:
        """MinIO Get Object operation - download object content."""
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if IfMatch is not None:
            kwargs['IfMatch'] = IfMatch
        if IfModifiedSince is not None:
            kwargs['IfModifiedSince'] = IfModifiedSince
        if IfNoneMatch is not None:
            kwargs['IfNoneMatch'] = IfNoneMatch
        if IfUnmodifiedSince is not None:
            kwargs['IfUnmodifiedSince'] = IfUnmodifiedSince
        if Range is not None:
            kwargs['Range'] = Range
        if ResponseCacheControl is not None:
            kwargs['ResponseCacheControl'] = ResponseCacheControl
        if ResponseContentDisposition is not None:
            kwargs['ResponseContentDisposition'] = ResponseContentDisposition
        if ResponseContentEncoding is not None:
            kwargs['ResponseContentEncoding'] = ResponseContentEncoding
        if ResponseContentLanguage is not None:
            kwargs['ResponseContentLanguage'] = ResponseContentLanguage
        if ResponseContentType is not None:
            kwargs['ResponseContentType'] = ResponseContentType
        if ResponseExpires is not None:
            kwargs['ResponseExpires'] = ResponseExpires
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if PartNumber is not None:
            kwargs['PartNumber'] = PartNumber
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if ChecksumMode is not None:
            kwargs['ChecksumMode'] = ChecksumMode

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.get_object(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: Optional[Dict[str, Any]] = None,
        ExpiresIn: Optional[int] = None,
        HttpMethod: Optional[str] = None,
        region_name: Optional[str] = None
    ) -> S3Response:
        """MinIO Generate Presigned URL operation.

        Note: For MinIO, the presigned URL will use the MinIO endpoint instead of S3.
        The region_name parameter is less relevant for MinIO but kept for API compatibility.
        """
        kwargs = {'ClientMethod': ClientMethod}
        if Params is not None:
            kwargs['Params'] = Params
        if ExpiresIn is not None:
            kwargs['ExpiresIn'] = ExpiresIn
        if HttpMethod is not None:
            kwargs['HttpMethod'] = HttpMethod

        try:
            session = await self._get_aioboto3_session()
            # Use Signature Version 4 for compatibility
            s3_config = Config(signature_version='s3v4')
            client_kwargs = self._get_client_kwargs()
            client_kwargs['config'] = s3_config

            async with session.client('s3', **client_kwargs) as s3_client:
                # generate_presigned_url is a synchronous method in boto3
                url_result = s3_client.generate_presigned_url(**kwargs)
                if asyncio.iscoroutine(url_result):
                    response = await url_result
                else:
                    response = url_result
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: Optional[Union[bytes, Any]] = None,
        ACL: Optional[str] = None,
        CacheControl: Optional[str] = None,
        ContentDisposition: Optional[str] = None,
        ContentEncoding: Optional[str] = None,
        ContentLanguage: Optional[str] = None,
        ContentLength: Optional[int] = None,
        ContentMD5: Optional[str] = None,
        ContentType: Optional[str] = None,
        Expires: Optional[Any] = None,
        GrantFullControl: Optional[str] = None,
        GrantRead: Optional[str] = None,
        GrantReadACP: Optional[str] = None,
        GrantWriteACP: Optional[str] = None,
        Metadata: Optional[Dict[str, str]] = None,
        ServerSideEncryption: Optional[str] = None,
        StorageClass: Optional[str] = None,
        WebsiteRedirectLocation: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        SSEKMSKeyId: Optional[str] = None,
        SSEKMSEncryptionContext: Optional[str] = None,
        BucketKeyEnabled: Optional[bool] = None,
        RequestPayer: Optional[str] = None,
        Tagging: Optional[str] = None,
        ObjectLockMode: Optional[str] = None,
        ObjectLockRetainUntilDate: Optional[Any] = None,
        ObjectLockLegalHoldStatus: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ChecksumCRC32: Optional[str] = None,
        ChecksumCRC32C: Optional[str] = None,
        ChecksumSHA1: Optional[str] = None,
        ChecksumSHA256: Optional[str] = None
    ) -> S3Response:
        """MinIO Put Object operation - upload object content."""
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if Body is not None:
            kwargs['Body'] = Body
        if ACL is not None:
            kwargs['ACL'] = ACL
        if CacheControl is not None:
            kwargs['CacheControl'] = CacheControl
        if ContentDisposition is not None:
            kwargs['ContentDisposition'] = ContentDisposition
        if ContentEncoding is not None:
            kwargs['ContentEncoding'] = ContentEncoding
        if ContentLanguage is not None:
            kwargs['ContentLanguage'] = ContentLanguage
        if ContentLength is not None:
            kwargs['ContentLength'] = ContentLength
        if ContentMD5 is not None:
            kwargs['ContentMD5'] = ContentMD5
        if ContentType is not None:
            kwargs['ContentType'] = ContentType
        if Expires is not None:
            kwargs['Expires'] = Expires
        if GrantFullControl is not None:
            kwargs['GrantFullControl'] = GrantFullControl
        if GrantRead is not None:
            kwargs['GrantRead'] = GrantRead
        if GrantReadACP is not None:
            kwargs['GrantReadACP'] = GrantReadACP
        if GrantWriteACP is not None:
            kwargs['GrantWriteACP'] = GrantWriteACP
        if Metadata is not None:
            kwargs['Metadata'] = Metadata
        if ServerSideEncryption is not None:
            kwargs['ServerSideEncryption'] = ServerSideEncryption
        if StorageClass is not None:
            kwargs['StorageClass'] = StorageClass
        if WebsiteRedirectLocation is not None:
            kwargs['WebsiteRedirectLocation'] = WebsiteRedirectLocation
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if SSEKMSKeyId is not None:
            kwargs['SSEKMSKeyId'] = SSEKMSKeyId
        if SSEKMSEncryptionContext is not None:
            kwargs['SSEKMSEncryptionContext'] = SSEKMSEncryptionContext
        if BucketKeyEnabled is not None:
            kwargs['BucketKeyEnabled'] = BucketKeyEnabled
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if Tagging is not None:
            kwargs['Tagging'] = Tagging
        if ObjectLockMode is not None:
            kwargs['ObjectLockMode'] = ObjectLockMode
        if ObjectLockRetainUntilDate is not None:
            kwargs['ObjectLockRetainUntilDate'] = ObjectLockRetainUntilDate
        if ObjectLockLegalHoldStatus is not None:
            kwargs['ObjectLockLegalHoldStatus'] = ObjectLockLegalHoldStatus
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ChecksumCRC32 is not None:
            kwargs['ChecksumCRC32'] = ChecksumCRC32
        if ChecksumCRC32C is not None:
            kwargs['ChecksumCRC32C'] = ChecksumCRC32C
        if ChecksumSHA1 is not None:
            kwargs['ChecksumSHA1'] = ChecksumSHA1
        if ChecksumSHA256 is not None:
            kwargs['ChecksumSHA256'] = ChecksumSHA256

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.put_object(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_object(
        self,
        Bucket: str,
        Key: str,
        MFA: Optional[str] = None,
        VersionId: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        BypassGovernanceRetention: Optional[bool] = None,
        ExpectedBucketOwner: Optional[str] = None
    ) -> S3Response:
        """MinIO Delete Object operation."""
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if MFA is not None:
            kwargs['MFA'] = MFA
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if BypassGovernanceRetention is not None:
            kwargs['BypassGovernanceRetention'] = BypassGovernanceRetention
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.delete_object(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def copy_object(
        self,
        Bucket: str,
        CopySource: Union[str, Dict[str, Any]],
        Key: str,
        ACL: Optional[str] = None,
        MetadataDirective: Optional[str] = None,
        Metadata: Optional[Dict[str, str]] = None
    ) -> S3Response:
        """MinIO Copy Object operation (simplified signature)."""
        kwargs = {'Bucket': Bucket, 'CopySource': CopySource, 'Key': Key}
        if ACL is not None:
            kwargs['ACL'] = ACL
        if MetadataDirective is not None:
            kwargs['MetadataDirective'] = MetadataDirective
        if Metadata is not None:
            kwargs['Metadata'] = Metadata

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3', **self._get_client_kwargs()) as s3_client:
                response = await s3_client.copy_object(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")
