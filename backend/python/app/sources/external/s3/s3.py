from datetime import datetime
from typing import Any, Dict, List, Optional, Union

try:
    import aioboto3  # type: ignore
    from botocore.exceptions import ClientError  # type: ignore
except ImportError:
    raise ImportError("aioboto3 is not installed. Please install it with `pip install aioboto3`")

from app.sources.client.s3.s3 import S3Client, S3Response


class S3DataSource:
    """
    Complete Amazon S3 API client wrapper with EXPLICIT METHOD SIGNATURES using aioboto3.
    All 102 S3 methods with proper parameter signatures:
    - Required parameters are explicitly typed (e.g., Bucket: str, Key: str)
    - Optional parameters use Optional[Type] = None
    - No **kwargs - every parameter is explicitly defined
    - Matches aioboto3 S3 client signatures exactly
    - Each parameter on separate line for better readability
    """

    def __init__(self, s3_client: S3Client) -> None:
        """Initialize with S3Client."""
        self._s3_client = s3_client
        self._session = None

    async def _get_aioboto3_session(self) -> aioboto3.Session:  # type: ignore[valid-type]
        """Get or create the aioboto3 session."""
        if self._session is None:
            # Option 1: Get the existing session directly from S3Client (recommended)
            self._session = self._s3_client.get_session()

            # Option 2: Create new session from credentials (if needed)
            # credentials = self._s3_client.get_credentials()
            # self._session = aioboto3.Session(
            #     aws_access_key_id=credentials['aws_access_key_id'],
            #     aws_secret_access_key=credentials['aws_secret_access_key'],
            #     region_name=credentials['region_name']
            # )
        return self._session

    def _handle_s3_response(self, response: object) -> S3Response:
        """Handle S3 API response with comprehensive error handling."""
        try:
            if response is None:
                return S3Response(success=False, error="Empty response from S3 API")

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

    async def abort_multipart_upload(self,
        Bucket: str,
        Key: str,
        UploadId: str,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Abort Multipart Upload operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            UploadId (str): Required parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'UploadId': UploadId}
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'abort_multipart_upload')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def complete_multipart_upload(self,
        Bucket: str,
        Key: str,
        UploadId: str,
        MultipartUpload: Optional[Dict[str, Any]] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None) -> S3Response:
        """S3 Complete Multipart Upload operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            UploadId (str): Required parameter
            MultipartUpload (Optional[Dict[str, Any]]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'UploadId': UploadId}
        if MultipartUpload is not None:
            kwargs['MultipartUpload'] = MultipartUpload
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'complete_multipart_upload')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def copy_object(self,
        Bucket: str,
        CopySource: Union[str, Dict[str, Any]],
        Key: str,
        ACL: Optional[str] = None,
        CacheControl: Optional[str] = None,
        ContentDisposition: Optional[str] = None,
        ContentEncoding: Optional[str] = None,
        ContentLanguage: Optional[str] = None,
        ContentType: Optional[str] = None,
        CopySourceIfMatch: Optional[str] = None,
        CopySourceIfModifiedSince: Optional[datetime] = None,
        CopySourceIfNoneMatch: Optional[str] = None,
        CopySourceIfUnmodifiedSince: Optional[datetime] = None,
        Expires: Optional[datetime] = None,
        GrantFullControl: Optional[str] = None,
        GrantRead: Optional[str] = None,
        GrantReadACP: Optional[str] = None,
        GrantWriteACP: Optional[str] = None,
        Metadata: Optional[Dict[str, str]] = None,
        MetadataDirective: Optional[str] = None,
        TaggingDirective: Optional[str] = None,
        ServerSideEncryption: Optional[str] = None,
        StorageClass: Optional[str] = None,
        WebsiteRedirectLocation: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        SSEKMSKeyId: Optional[str] = None,
        SSEKMSEncryptionContext: Optional[str] = None,
        BucketKeyEnabled: Optional[bool] = None,
        CopySourceSSECustomerAlgorithm: Optional[str] = None,
        CopySourceSSECustomerKey: Optional[str] = None,
        CopySourceSSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        Tagging: Optional[str] = None,
        ObjectLockMode: Optional[str] = None,
        ObjectLockRetainUntilDate: Optional[datetime] = None,
        ObjectLockLegalHoldStatus: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ExpectedSourceBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Copy Object operation.

        Args:
            Bucket (str): Required parameter
            CopySource (Union[str, Dict[str, Any]]): Required parameter
            Key (str): Required parameter
            ACL (Optional[str]): Optional parameter
            CacheControl (Optional[str]): Optional parameter
            ContentDisposition (Optional[str]): Optional parameter
            ContentEncoding (Optional[str]): Optional parameter
            ContentLanguage (Optional[str]): Optional parameter
            ContentType (Optional[str]): Optional parameter
            CopySourceIfMatch (Optional[str]): Optional parameter
            CopySourceIfModifiedSince (Optional[datetime]): Optional parameter
            CopySourceIfNoneMatch (Optional[str]): Optional parameter
            CopySourceIfUnmodifiedSince (Optional[datetime]): Optional parameter
            Expires (Optional[datetime]): Optional parameter
            GrantFullControl (Optional[str]): Optional parameter
            GrantRead (Optional[str]): Optional parameter
            GrantReadACP (Optional[str]): Optional parameter
            GrantWriteACP (Optional[str]): Optional parameter
            Metadata (Optional[Dict[str, str]]): Optional parameter
            MetadataDirective (Optional[str]): Optional parameter
            TaggingDirective (Optional[str]): Optional parameter
            ServerSideEncryption (Optional[str]): Optional parameter
            StorageClass (Optional[str]): Optional parameter
            WebsiteRedirectLocation (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            SSEKMSKeyId (Optional[str]): Optional parameter
            SSEKMSEncryptionContext (Optional[str]): Optional parameter
            BucketKeyEnabled (Optional[bool]): Optional parameter
            CopySourceSSECustomerAlgorithm (Optional[str]): Optional parameter
            CopySourceSSECustomerKey (Optional[str]): Optional parameter
            CopySourceSSECustomerKeyMD5 (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            Tagging (Optional[str]): Optional parameter
            ObjectLockMode (Optional[str]): Optional parameter
            ObjectLockRetainUntilDate (Optional[datetime]): Optional parameter
            ObjectLockLegalHoldStatus (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ExpectedSourceBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'CopySource': CopySource, 'Key': Key}
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
        if ContentType is not None:
            kwargs['ContentType'] = ContentType
        if CopySourceIfMatch is not None:
            kwargs['CopySourceIfMatch'] = CopySourceIfMatch
        if CopySourceIfModifiedSince is not None:
            kwargs['CopySourceIfModifiedSince'] = CopySourceIfModifiedSince
        if CopySourceIfNoneMatch is not None:
            kwargs['CopySourceIfNoneMatch'] = CopySourceIfNoneMatch
        if CopySourceIfUnmodifiedSince is not None:
            kwargs['CopySourceIfUnmodifiedSince'] = CopySourceIfUnmodifiedSince
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
        if MetadataDirective is not None:
            kwargs['MetadataDirective'] = MetadataDirective
        if TaggingDirective is not None:
            kwargs['TaggingDirective'] = TaggingDirective
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
        if CopySourceSSECustomerAlgorithm is not None:
            kwargs['CopySourceSSECustomerAlgorithm'] = CopySourceSSECustomerAlgorithm
        if CopySourceSSECustomerKey is not None:
            kwargs['CopySourceSSECustomerKey'] = CopySourceSSECustomerKey
        if CopySourceSSECustomerKeyMD5 is not None:
            kwargs['CopySourceSSECustomerKeyMD5'] = CopySourceSSECustomerKeyMD5
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
        if ExpectedSourceBucketOwner is not None:
            kwargs['ExpectedSourceBucketOwner'] = ExpectedSourceBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'copy_object')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def create_bucket(self,
        Bucket: str,
        ACL: Optional[str] = None,
        CreateBucketConfiguration: Optional[Dict[str, Any]] = None,
        GrantFullControl: Optional[str] = None,
        GrantRead: Optional[str] = None,
        GrantReadACP: Optional[str] = None,
        GrantWrite: Optional[str] = None,
        GrantWriteACP: Optional[str] = None,
        ObjectLockEnabledForBucket: Optional[bool] = None,
        ObjectOwnership: Optional[str] = None) -> S3Response:
        """S3 Create Bucket operation.

        Args:
            Bucket (str): Required parameter
            ACL (Optional[str]): Optional parameter
            CreateBucketConfiguration (Optional[Dict[str, Any]]): Optional parameter
            GrantFullControl (Optional[str]): Optional parameter
            GrantRead (Optional[str]): Optional parameter
            GrantReadACP (Optional[str]): Optional parameter
            GrantWrite (Optional[str]): Optional parameter
            GrantWriteACP (Optional[str]): Optional parameter
            ObjectLockEnabledForBucket (Optional[bool]): Optional parameter
            ObjectOwnership (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ACL is not None:
            kwargs['ACL'] = ACL
        if CreateBucketConfiguration is not None:
            kwargs['CreateBucketConfiguration'] = CreateBucketConfiguration
        if GrantFullControl is not None:
            kwargs['GrantFullControl'] = GrantFullControl
        if GrantRead is not None:
            kwargs['GrantRead'] = GrantRead
        if GrantReadACP is not None:
            kwargs['GrantReadACP'] = GrantReadACP
        if GrantWrite is not None:
            kwargs['GrantWrite'] = GrantWrite
        if GrantWriteACP is not None:
            kwargs['GrantWriteACP'] = GrantWriteACP
        if ObjectLockEnabledForBucket is not None:
            kwargs['ObjectLockEnabledForBucket'] = ObjectLockEnabledForBucket
        if ObjectOwnership is not None:
            kwargs['ObjectOwnership'] = ObjectOwnership

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'create_bucket')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def create_multipart_upload(self,
        Bucket: str,
        Key: str,
        ACL: Optional[str] = None,
        CacheControl: Optional[str] = None,
        ContentDisposition: Optional[str] = None,
        ContentEncoding: Optional[str] = None,
        ContentLanguage: Optional[str] = None,
        ContentType: Optional[str] = None,
        Expires: Optional[datetime] = None,
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
        ObjectLockRetainUntilDate: Optional[datetime] = None,
        ObjectLockLegalHoldStatus: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None) -> S3Response:
        """S3 Create Multipart Upload operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            ACL (Optional[str]): Optional parameter
            CacheControl (Optional[str]): Optional parameter
            ContentDisposition (Optional[str]): Optional parameter
            ContentEncoding (Optional[str]): Optional parameter
            ContentLanguage (Optional[str]): Optional parameter
            ContentType (Optional[str]): Optional parameter
            Expires (Optional[datetime]): Optional parameter
            GrantFullControl (Optional[str]): Optional parameter
            GrantRead (Optional[str]): Optional parameter
            GrantReadACP (Optional[str]): Optional parameter
            GrantWriteACP (Optional[str]): Optional parameter
            Metadata (Optional[Dict[str, str]]): Optional parameter
            ServerSideEncryption (Optional[str]): Optional parameter
            StorageClass (Optional[str]): Optional parameter
            WebsiteRedirectLocation (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            SSEKMSKeyId (Optional[str]): Optional parameter
            SSEKMSEncryptionContext (Optional[str]): Optional parameter
            BucketKeyEnabled (Optional[bool]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            Tagging (Optional[str]): Optional parameter
            ObjectLockMode (Optional[str]): Optional parameter
            ObjectLockRetainUntilDate (Optional[datetime]): Optional parameter
            ObjectLockLegalHoldStatus (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
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

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'create_multipart_upload')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_analytics_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Analytics Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_analytics_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_cors(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Cors operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_cors')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_encryption(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Encryption operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_encryption')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_intelligent_tiering_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Intelligent Tiering Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_intelligent_tiering_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_inventory_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Inventory Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_inventory_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_lifecycle(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Lifecycle operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_lifecycle')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_metrics_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Metrics Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_metrics_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_ownership_controls(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Ownership Controls operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_ownership_controls')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_policy(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Policy operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_policy')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_replication(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Replication operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_replication')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_tagging(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Tagging operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_tagging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_bucket_website(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Bucket Website operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_bucket_website')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_object(self,
        Bucket: str,
        Key: str,
        MFA: Optional[str] = None,
        VersionId: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        BypassGovernanceRetention: Optional[bool] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Object operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            MFA (Optional[str]): Optional parameter
            VersionId (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            BypassGovernanceRetention (Optional[bool]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
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
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_object')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_object_tagging(self,
        Bucket: str,
        Key: str,
        VersionId: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Object Tagging operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_object_tagging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_objects(self,
        Bucket: str,
        Delete: Dict[str, Any],
        MFA: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        BypassGovernanceRetention: Optional[bool] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None) -> S3Response:
        """S3 Delete Objects operation.

        Args:
            Bucket (str): Required parameter
            Delete (Dict[str, Any]): Required parameter
            MFA (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            BypassGovernanceRetention (Optional[bool]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Delete': Delete}
        if MFA is not None:
            kwargs['MFA'] = MFA
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if BypassGovernanceRetention is not None:
            kwargs['BypassGovernanceRetention'] = BypassGovernanceRetention
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_objects')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_public_access_block(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Delete Public Access Block operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'delete_public_access_block')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def download_file(self,
        Bucket: str,
        Key: str,
        Filename: str,
        ExtraArgs: Optional[Dict[str, Any]] = None,
        Callback: Optional[object] = None,
        Config: Optional[object] = None) -> S3Response:
        """S3 Download File operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            Filename (str): Required parameter
            ExtraArgs (Optional[Optional[Dict[str, Any]]]): Optional parameter
            Callback (Optional[object]): Optional parameter
            Config (Optional[object]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'Filename': Filename}
        if ExtraArgs is not None:
            kwargs['ExtraArgs'] = ExtraArgs
        if Callback is not None:
            kwargs['Callback'] = Callback
        if Config is not None:
            kwargs['Config'] = Config

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'download_file')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def download_fileobj(self,
        Bucket: str,
        Key: str,
        Fileobj: object,
        ExtraArgs: Optional[Dict[str, Any]] = None,
        Callback: Optional[object] = None,
        Config: Optional[object] = None) -> S3Response:
        """S3 Download Fileobj operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            Fileobj (Any): Required parameter
            ExtraArgs (Optional[Optional[Dict[str, Any]]]): Optional parameter
            Callback (Optional[object]): Optional parameter
            Config (Optional[object]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'Fileobj': Fileobj}
        if ExtraArgs is not None:
            kwargs['ExtraArgs'] = ExtraArgs
        if Callback is not None:
            kwargs['Callback'] = Callback
        if Config is not None:
            kwargs['Config'] = Config

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'download_fileobj')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def generate_presigned_post(self,
        Bucket: str,
        Key: str,
        Fields: Optional[Dict[str, Any]] = None,
        Conditions: Optional[List[Any]] = None,
        ExpiresIn: Optional[int] = None) -> S3Response:
        """S3 Generate Presigned Post operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            Fields (Optional[Optional[Dict[str, Any]]]): Optional parameter
            Conditions (Optional[Optional[List[Any]]]): Optional parameter
            ExpiresIn (Optional[int]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if Fields is not None:
            kwargs['Fields'] = Fields
        if Conditions is not None:
            kwargs['Conditions'] = Conditions
        if ExpiresIn is not None:
            kwargs['ExpiresIn'] = ExpiresIn

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'generate_presigned_post')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def generate_presigned_url(self,
        ClientMethod: str,
        Params: Optional[Dict[str, Any]] = None,
        ExpiresIn: Optional[int] = None,
        HttpMethod: Optional[str] = None) -> S3Response:
        """S3 Generate Presigned Url operation.

        Args:
            ClientMethod (str): Required parameter
            Params (Optional[Optional[Dict[str, Any]]]): Optional parameter
            ExpiresIn (Optional[int]): Optional parameter
            HttpMethod (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
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
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'generate_presigned_url')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_accelerate_configuration(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None,
        RequestPayer: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Accelerate Configuration operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_accelerate_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_acl(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Acl operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_acl')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_analytics_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Analytics Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_analytics_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_cors(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Cors operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_cors')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_encryption(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Encryption operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_encryption')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_intelligent_tiering_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Intelligent Tiering Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_intelligent_tiering_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_inventory_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Inventory Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_inventory_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_lifecycle(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Lifecycle operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_lifecycle')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_lifecycle_configuration(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Lifecycle Configuration operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_lifecycle_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_location(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Location operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_location')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_logging(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Logging operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_logging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_metrics_configuration(self,
        Bucket: str,
        Id: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Metrics Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_metrics_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_notification(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Notification operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_notification')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_notification_configuration(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Notification Configuration operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_notification_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_ownership_controls(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Ownership Controls operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_ownership_controls')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_policy(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Policy operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_policy')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_policy_status(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Policy Status operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_policy_status')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_replication(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Replication operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_replication')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_request_payment(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Request Payment operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_request_payment')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_tagging(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Tagging operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_tagging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_versioning(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Versioning operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_versioning')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_bucket_website(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Bucket Website operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_bucket_website')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object(self,
        Bucket: str,
        Key: str,
        IfMatch: Optional[str] = None,
        IfModifiedSince: Optional[datetime] = None,
        IfNoneMatch: Optional[str] = None,
        IfUnmodifiedSince: Optional[datetime] = None,
        Range: Optional[str] = None,
        ResponseCacheControl: Optional[str] = None,
        ResponseContentDisposition: Optional[str] = None,
        ResponseContentEncoding: Optional[str] = None,
        ResponseContentLanguage: Optional[str] = None,
        ResponseContentType: Optional[str] = None,
        ResponseExpires: Optional[datetime] = None,
        VersionId: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        PartNumber: Optional[int] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumMode: Optional[str] = None) -> S3Response:
        """S3 Get Object operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            IfMatch (Optional[str]): Optional parameter
            IfModifiedSince (Optional[datetime]): Optional parameter
            IfNoneMatch (Optional[str]): Optional parameter
            IfUnmodifiedSince (Optional[datetime]): Optional parameter
            Range (Optional[str]): Optional parameter
            ResponseCacheControl (Optional[str]): Optional parameter
            ResponseContentDisposition (Optional[str]): Optional parameter
            ResponseContentEncoding (Optional[str]): Optional parameter
            ResponseContentLanguage (Optional[str]): Optional parameter
            ResponseContentType (Optional[str]): Optional parameter
            ResponseExpires (Optional[datetime]): Optional parameter
            VersionId (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            PartNumber (Optional[int]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ChecksumMode (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
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
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_acl(self,
        Bucket: str,
        Key: str,
        VersionId: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Object Acl operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_acl')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_attributes(self,
        Bucket: str,
        Key: str,
        ObjectAttributes: List[str],
        VersionId: Optional[str] = None,
        MaxParts: Optional[int] = None,
        PartNumberMarker: Optional[int] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Object Attributes operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            ObjectAttributes (List[str]): Required parameter
            VersionId (Optional[str]): Optional parameter
            MaxParts (Optional[int]): Optional parameter
            PartNumberMarker (Optional[int]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'ObjectAttributes': ObjectAttributes}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if MaxParts is not None:
            kwargs['MaxParts'] = MaxParts
        if PartNumberMarker is not None:
            kwargs['PartNumberMarker'] = PartNumberMarker
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_attributes')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_legal_hold(self,
        Bucket: str,
        Key: str,
        VersionId: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Object Legal Hold operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_legal_hold')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_lock_configuration(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Object Lock Configuration operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_lock_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_retention(self,
        Bucket: str,
        Key: str,
        VersionId: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Object Retention operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_retention')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_tagging(self,
        Bucket: str,
        Key: str,
        VersionId: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        RequestPayer: Optional[str] = None) -> S3Response:
        """S3 Get Object Tagging operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_tagging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_object_torrent(self,
        Bucket: str,
        Key: str,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Object Torrent operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_object_torrent')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def get_public_access_block(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Get Public Access Block operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'get_public_access_block')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def head_bucket(self,
        Bucket: str,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Head Bucket operation.

        Args:
            Bucket (str): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'head_bucket')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def head_object(self,
        Bucket: str,
        Key: str,
        IfMatch: Optional[str] = None,
        IfModifiedSince: Optional[datetime] = None,
        IfNoneMatch: Optional[str] = None,
        IfUnmodifiedSince: Optional[datetime] = None,
        Range: Optional[str] = None,
        VersionId: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        PartNumber: Optional[int] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumMode: Optional[str] = None) -> S3Response:
        """S3 Head Object operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            IfMatch (Optional[str]): Optional parameter
            IfModifiedSince (Optional[datetime]): Optional parameter
            IfNoneMatch (Optional[str]): Optional parameter
            IfUnmodifiedSince (Optional[datetime]): Optional parameter
            Range (Optional[str]): Optional parameter
            VersionId (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            PartNumber (Optional[int]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ChecksumMode (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
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
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'head_object')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_bucket_analytics_configurations(self,
        Bucket: str,
        ContinuationToken: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 List Bucket Analytics Configurations operation.

        Args:
            Bucket (str): Required parameter
            ContinuationToken (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ContinuationToken is not None:
            kwargs['ContinuationToken'] = ContinuationToken
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_bucket_analytics_configurations')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_bucket_intelligent_tiering_configurations(self,
        Bucket: str,
        ContinuationToken: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 List Bucket Intelligent Tiering Configurations operation.

        Args:
            Bucket (str): Required parameter
            ContinuationToken (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ContinuationToken is not None:
            kwargs['ContinuationToken'] = ContinuationToken
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_bucket_intelligent_tiering_configurations')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_bucket_inventory_configurations(self,
        Bucket: str,
        ContinuationToken: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 List Bucket Inventory Configurations operation.

        Args:
            Bucket (str): Required parameter
            ContinuationToken (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ContinuationToken is not None:
            kwargs['ContinuationToken'] = ContinuationToken
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_bucket_inventory_configurations')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_bucket_metrics_configurations(self,
        Bucket: str,
        ContinuationToken: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 List Bucket Metrics Configurations operation.

        Args:
            Bucket (str): Required parameter
            ContinuationToken (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ContinuationToken is not None:
            kwargs['ContinuationToken'] = ContinuationToken
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_bucket_metrics_configurations')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_buckets(self) -> S3Response:
        """S3 List Buckets operation.


        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {}

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_buckets')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_multipart_uploads(self,
        Bucket: str,
        Delimiter: Optional[str] = None,
        EncodingType: Optional[str] = None,
        KeyMarker: Optional[str] = None,
        MaxUploads: Optional[int] = None,
        Prefix: Optional[str] = None,
        UploadIdMarker: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        RequestPayer: Optional[str] = None) -> S3Response:
        """S3 List Multipart Uploads operation.

        Args:
            Bucket (str): Required parameter
            Delimiter (Optional[str]): Optional parameter
            EncodingType (Optional[str]): Optional parameter
            KeyMarker (Optional[str]): Optional parameter
            MaxUploads (Optional[int]): Optional parameter
            Prefix (Optional[str]): Optional parameter
            UploadIdMarker (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if Delimiter is not None:
            kwargs['Delimiter'] = Delimiter
        if EncodingType is not None:
            kwargs['EncodingType'] = EncodingType
        if KeyMarker is not None:
            kwargs['KeyMarker'] = KeyMarker
        if MaxUploads is not None:
            kwargs['MaxUploads'] = MaxUploads
        if Prefix is not None:
            kwargs['Prefix'] = Prefix
        if UploadIdMarker is not None:
            kwargs['UploadIdMarker'] = UploadIdMarker
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_multipart_uploads')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_object_versions(self,
        Bucket: str,
        Delimiter: Optional[str] = None,
        EncodingType: Optional[str] = None,
        KeyMarker: Optional[str] = None,
        MaxKeys: Optional[int] = None,
        Prefix: Optional[str] = None,
        VersionIdMarker: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        OptionalObjectAttributes: Optional[List[str]] = None) -> S3Response:
        """S3 List Object Versions operation.

        Args:
            Bucket (str): Required parameter
            Delimiter (Optional[str]): Optional parameter
            EncodingType (Optional[str]): Optional parameter
            KeyMarker (Optional[str]): Optional parameter
            MaxKeys (Optional[int]): Optional parameter
            Prefix (Optional[str]): Optional parameter
            VersionIdMarker (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            OptionalObjectAttributes (Optional[List[str]]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if Delimiter is not None:
            kwargs['Delimiter'] = Delimiter
        if EncodingType is not None:
            kwargs['EncodingType'] = EncodingType
        if KeyMarker is not None:
            kwargs['KeyMarker'] = KeyMarker
        if MaxKeys is not None:
            kwargs['MaxKeys'] = MaxKeys
        if Prefix is not None:
            kwargs['Prefix'] = Prefix
        if VersionIdMarker is not None:
            kwargs['VersionIdMarker'] = VersionIdMarker
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if OptionalObjectAttributes is not None:
            kwargs['OptionalObjectAttributes'] = OptionalObjectAttributes

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_object_versions')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_objects(self,
        Bucket: str,
        Delimiter: Optional[str] = None,
        EncodingType: Optional[str] = None,
        Marker: Optional[str] = None,
        MaxKeys: Optional[int] = None,
        Prefix: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        OptionalObjectAttributes: Optional[List[str]] = None) -> S3Response:
        """S3 List Objects operation.

        Args:
            Bucket (str): Required parameter
            Delimiter (Optional[str]): Optional parameter
            EncodingType (Optional[str]): Optional parameter
            Marker (Optional[str]): Optional parameter
            MaxKeys (Optional[int]): Optional parameter
            Prefix (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            OptionalObjectAttributes (Optional[List[str]]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if Delimiter is not None:
            kwargs['Delimiter'] = Delimiter
        if EncodingType is not None:
            kwargs['EncodingType'] = EncodingType
        if Marker is not None:
            kwargs['Marker'] = Marker
        if MaxKeys is not None:
            kwargs['MaxKeys'] = MaxKeys
        if Prefix is not None:
            kwargs['Prefix'] = Prefix
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if OptionalObjectAttributes is not None:
            kwargs['OptionalObjectAttributes'] = OptionalObjectAttributes

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_objects')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_objects_v2(self,
        Bucket: str,
        Delimiter: Optional[str] = None,
        EncodingType: Optional[str] = None,
        MaxKeys: Optional[int] = None,
        Prefix: Optional[str] = None,
        ContinuationToken: Optional[str] = None,
        FetchOwner: Optional[bool] = None,
        StartAfter: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        OptionalObjectAttributes: Optional[List[str]] = None) -> S3Response:
        """S3 List Objects V2 operation.

        Args:
            Bucket (str): Required parameter
            Delimiter (Optional[str]): Optional parameter
            EncodingType (Optional[str]): Optional parameter
            MaxKeys (Optional[int]): Optional parameter
            Prefix (Optional[str]): Optional parameter
            ContinuationToken (Optional[str]): Optional parameter
            FetchOwner (Optional[bool]): Optional parameter
            StartAfter (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            OptionalObjectAttributes (Optional[List[str]]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
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
        if OptionalObjectAttributes is not None:
            kwargs['OptionalObjectAttributes'] = OptionalObjectAttributes

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_objects_v2')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def list_parts(self,
        Bucket: str,
        Key: str,
        UploadId: str,
        MaxParts: Optional[int] = None,
        PartNumberMarker: Optional[int] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None) -> S3Response:
        """S3 List Parts operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            UploadId (str): Required parameter
            MaxParts (Optional[int]): Optional parameter
            PartNumberMarker (Optional[int]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'UploadId': UploadId}
        if MaxParts is not None:
            kwargs['MaxParts'] = MaxParts
        if PartNumberMarker is not None:
            kwargs['PartNumberMarker'] = PartNumberMarker
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'list_parts')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_accelerate_configuration(self,
        Bucket: str,
        AccelerateConfiguration: Dict[str, Any],
        ExpectedBucketOwner: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Accelerate Configuration operation.

        Args:
            Bucket (str): Required parameter
            AccelerateConfiguration (Dict[str, Any]): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'AccelerateConfiguration': AccelerateConfiguration}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_accelerate_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_acl(self,
        Bucket: str,
        ACL: Optional[str] = None,
        AccessControlPolicy: Optional[Dict[str, Any]] = None,
        ChecksumAlgorithm: Optional[str] = None,
        GrantFullControl: Optional[str] = None,
        GrantRead: Optional[str] = None,
        GrantReadACP: Optional[str] = None,
        GrantWrite: Optional[str] = None,
        GrantWriteACP: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Acl operation.

        Args:
            Bucket (str): Required parameter
            ACL (Optional[str]): Optional parameter
            AccessControlPolicy (Optional[Dict[str, Any]]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            GrantFullControl (Optional[str]): Optional parameter
            GrantRead (Optional[str]): Optional parameter
            GrantReadACP (Optional[str]): Optional parameter
            GrantWrite (Optional[str]): Optional parameter
            GrantWriteACP (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ACL is not None:
            kwargs['ACL'] = ACL
        if AccessControlPolicy is not None:
            kwargs['AccessControlPolicy'] = AccessControlPolicy
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if GrantFullControl is not None:
            kwargs['GrantFullControl'] = GrantFullControl
        if GrantRead is not None:
            kwargs['GrantRead'] = GrantRead
        if GrantReadACP is not None:
            kwargs['GrantReadACP'] = GrantReadACP
        if GrantWrite is not None:
            kwargs['GrantWrite'] = GrantWrite
        if GrantWriteACP is not None:
            kwargs['GrantWriteACP'] = GrantWriteACP
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_acl')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_analytics_configuration(self,
        Bucket: str,
        Id: str,
        AnalyticsConfiguration: Dict[str, Any],
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Analytics Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            AnalyticsConfiguration (Dict[str, Any]): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id, 'AnalyticsConfiguration': AnalyticsConfiguration}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_analytics_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_cors(self,
        Bucket: str,
        CORSConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Cors operation.

        Args:
            Bucket (str): Required parameter
            CORSConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'CORSConfiguration': CORSConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_cors')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_encryption(self,
        Bucket: str,
        ServerSideEncryptionConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Encryption operation.

        Args:
            Bucket (str): Required parameter
            ServerSideEncryptionConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'ServerSideEncryptionConfiguration': ServerSideEncryptionConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_encryption')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_intelligent_tiering_configuration(self,
        Bucket: str,
        Id: str,
        IntelligentTieringConfiguration: Dict[str, Any],
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Intelligent Tiering Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            IntelligentTieringConfiguration (Dict[str, Any]): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id, 'IntelligentTieringConfiguration': IntelligentTieringConfiguration}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_intelligent_tiering_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_inventory_configuration(self,
        Bucket: str,
        Id: str,
        InventoryConfiguration: Dict[str, Any],
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Inventory Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            InventoryConfiguration (Dict[str, Any]): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id, 'InventoryConfiguration': InventoryConfiguration}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_inventory_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_lifecycle(self,
        Bucket: str,
        LifecycleConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Lifecycle operation.

        Args:
            Bucket (str): Required parameter
            LifecycleConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'LifecycleConfiguration': LifecycleConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_lifecycle')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_lifecycle_configuration(self,
        Bucket: str,
        ChecksumAlgorithm: Optional[str] = None,
        LifecycleConfiguration: Optional[Dict[str, Any]] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Lifecycle Configuration operation.

        Args:
            Bucket (str): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            LifecycleConfiguration (Optional[Dict[str, Any]]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if LifecycleConfiguration is not None:
            kwargs['LifecycleConfiguration'] = LifecycleConfiguration
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_lifecycle_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_logging(self,
        Bucket: str,
        BucketLoggingStatus: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Logging operation.

        Args:
            Bucket (str): Required parameter
            BucketLoggingStatus (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'BucketLoggingStatus': BucketLoggingStatus}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_logging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_metrics_configuration(self,
        Bucket: str,
        Id: str,
        MetricsConfiguration: Dict[str, Any],
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Metrics Configuration operation.

        Args:
            Bucket (str): Required parameter
            Id (str): Required parameter
            MetricsConfiguration (Dict[str, Any]): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Id': Id, 'MetricsConfiguration': MetricsConfiguration}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_metrics_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_notification(self,
        Bucket: str,
        NotificationConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Notification operation.

        Args:
            Bucket (str): Required parameter
            NotificationConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'NotificationConfiguration': NotificationConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_notification')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_notification_configuration(self,
        Bucket: str,
        NotificationConfiguration: Dict[str, Any],
        ExpectedBucketOwner: Optional[str] = None,
        SkipDestinationValidation: Optional[bool] = None) -> S3Response:
        """S3 Put Bucket Notification Configuration operation.

        Args:
            Bucket (str): Required parameter
            NotificationConfiguration (Dict[str, Any]): Required parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            SkipDestinationValidation (Optional[bool]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'NotificationConfiguration': NotificationConfiguration}
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if SkipDestinationValidation is not None:
            kwargs['SkipDestinationValidation'] = SkipDestinationValidation

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_notification_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_ownership_controls(self,
        Bucket: str,
        OwnershipControls: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Ownership Controls operation.

        Args:
            Bucket (str): Required parameter
            OwnershipControls (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'OwnershipControls': OwnershipControls}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_ownership_controls')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_policy(self,
        Bucket: str,
        Policy: str,
        ChecksumAlgorithm: Optional[str] = None,
        ConfirmRemoveSelfBucketAccess: Optional[bool] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Policy operation.

        Args:
            Bucket (str): Required parameter
            Policy (str): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ConfirmRemoveSelfBucketAccess (Optional[bool]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Policy': Policy}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ConfirmRemoveSelfBucketAccess is not None:
            kwargs['ConfirmRemoveSelfBucketAccess'] = ConfirmRemoveSelfBucketAccess
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_policy')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_replication(self,
        Bucket: str,
        ReplicationConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        Token: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Replication operation.

        Args:
            Bucket (str): Required parameter
            ReplicationConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            Token (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'ReplicationConfiguration': ReplicationConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if Token is not None:
            kwargs['Token'] = Token
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_replication')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_request_payment(self,
        Bucket: str,
        RequestPaymentConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Request Payment operation.

        Args:
            Bucket (str): Required parameter
            RequestPaymentConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'RequestPaymentConfiguration': RequestPaymentConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_request_payment')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_tagging(self,
        Bucket: str,
        Tagging: str,
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Tagging operation.

        Args:
            Bucket (str): Required parameter
            Tagging (str): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Tagging': Tagging}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_tagging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_versioning(self,
        Bucket: str,
        VersioningConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        MFA: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Versioning operation.

        Args:
            Bucket (str): Required parameter
            VersioningConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            MFA (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'VersioningConfiguration': VersioningConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if MFA is not None:
            kwargs['MFA'] = MFA
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_versioning')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_bucket_website(self,
        Bucket: str,
        WebsiteConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Bucket Website operation.

        Args:
            Bucket (str): Required parameter
            WebsiteConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'WebsiteConfiguration': WebsiteConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_bucket_website')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object(self,
        Bucket: str,
        Key: str,
        ACL: Optional[str] = None,
        Body: Optional[object] = None,
        CacheControl: Optional[str] = None,
        ContentDisposition: Optional[str] = None,
        ContentEncoding: Optional[str] = None,
        ContentLanguage: Optional[str] = None,
        ContentLength: Optional[int] = None,
        ContentMD5: Optional[str] = None,
        ContentType: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ChecksumCRC32: Optional[str] = None,
        ChecksumCRC32C: Optional[str] = None,
        ChecksumSHA1: Optional[str] = None,
        ChecksumSHA256: Optional[str] = None,
        Expires: Optional[datetime] = None,
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
        ObjectLockRetainUntilDate: Optional[datetime] = None,
        ObjectLockLegalHoldStatus: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Object operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            ACL (Optional[str]): Optional parameter
            Body (Optional[object]): Optional parameter
            CacheControl (Optional[str]): Optional parameter
            ContentDisposition (Optional[str]): Optional parameter
            ContentEncoding (Optional[str]): Optional parameter
            ContentLanguage (Optional[str]): Optional parameter
            ContentLength (Optional[int]): Optional parameter
            ContentMD5 (Optional[str]): Optional parameter
            ContentType (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ChecksumCRC32 (Optional[str]): Optional parameter
            ChecksumCRC32C (Optional[str]): Optional parameter
            ChecksumSHA1 (Optional[str]): Optional parameter
            ChecksumSHA256 (Optional[str]): Optional parameter
            Expires (Optional[datetime]): Optional parameter
            GrantFullControl (Optional[str]): Optional parameter
            GrantRead (Optional[str]): Optional parameter
            GrantReadACP (Optional[str]): Optional parameter
            GrantWriteACP (Optional[str]): Optional parameter
            Metadata (Optional[Dict[str, str]]): Optional parameter
            ServerSideEncryption (Optional[str]): Optional parameter
            StorageClass (Optional[str]): Optional parameter
            WebsiteRedirectLocation (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            SSEKMSKeyId (Optional[str]): Optional parameter
            SSEKMSEncryptionContext (Optional[str]): Optional parameter
            BucketKeyEnabled (Optional[bool]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            Tagging (Optional[str]): Optional parameter
            ObjectLockMode (Optional[str]): Optional parameter
            ObjectLockRetainUntilDate (Optional[datetime]): Optional parameter
            ObjectLockLegalHoldStatus (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if ACL is not None:
            kwargs['ACL'] = ACL
        if Body is not None:
            kwargs['Body'] = Body
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

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_object')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object_acl(self,
        Bucket: str,
        Key: str,
        ACL: Optional[str] = None,
        AccessControlPolicy: Optional[Dict[str, Any]] = None,
        ChecksumAlgorithm: Optional[str] = None,
        GrantFullControl: Optional[str] = None,
        GrantRead: Optional[str] = None,
        GrantReadACP: Optional[str] = None,
        GrantWrite: Optional[str] = None,
        GrantWriteACP: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        VersionId: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Object Acl operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            ACL (Optional[str]): Optional parameter
            AccessControlPolicy (Optional[Dict[str, Any]]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            GrantFullControl (Optional[str]): Optional parameter
            GrantRead (Optional[str]): Optional parameter
            GrantReadACP (Optional[str]): Optional parameter
            GrantWrite (Optional[str]): Optional parameter
            GrantWriteACP (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            VersionId (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if ACL is not None:
            kwargs['ACL'] = ACL
        if AccessControlPolicy is not None:
            kwargs['AccessControlPolicy'] = AccessControlPolicy
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if GrantFullControl is not None:
            kwargs['GrantFullControl'] = GrantFullControl
        if GrantRead is not None:
            kwargs['GrantRead'] = GrantRead
        if GrantReadACP is not None:
            kwargs['GrantReadACP'] = GrantReadACP
        if GrantWrite is not None:
            kwargs['GrantWrite'] = GrantWrite
        if GrantWriteACP is not None:
            kwargs['GrantWriteACP'] = GrantWriteACP
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_object_acl')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object_legal_hold(self,
        Bucket: str,
        Key: str,
        LegalHold: Optional[Dict[str, Any]] = None,
        RequestPayer: Optional[str] = None,
        VersionId: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Object Legal Hold operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            LegalHold (Optional[Dict[str, Any]]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            VersionId (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if LegalHold is not None:
            kwargs['LegalHold'] = LegalHold
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_object_legal_hold')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object_lock_configuration(self,
        Bucket: str,
        ObjectLockConfiguration: Optional[Dict[str, Any]] = None,
        RequestPayer: Optional[str] = None,
        Token: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Object Lock Configuration operation.

        Args:
            Bucket (str): Required parameter
            ObjectLockConfiguration (Optional[Dict[str, Any]]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            Token (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket}
        if ObjectLockConfiguration is not None:
            kwargs['ObjectLockConfiguration'] = ObjectLockConfiguration
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if Token is not None:
            kwargs['Token'] = Token
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_object_lock_configuration')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object_retention(self,
        Bucket: str,
        Key: str,
        Retention: Optional[Dict[str, Any]] = None,
        RequestPayer: Optional[str] = None,
        VersionId: Optional[str] = None,
        BypassGovernanceRetention: Optional[bool] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Object Retention operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            Retention (Optional[Dict[str, Any]]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            VersionId (Optional[str]): Optional parameter
            BypassGovernanceRetention (Optional[bool]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if Retention is not None:
            kwargs['Retention'] = Retention
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if BypassGovernanceRetention is not None:
            kwargs['BypassGovernanceRetention'] = BypassGovernanceRetention
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_object_retention')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_object_tagging(self,
        Bucket: str,
        Key: str,
        Tagging: str,
        VersionId: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        RequestPayer: Optional[str] = None) -> S3Response:
        """S3 Put Object Tagging operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            Tagging (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'Tagging': Tagging}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_object_tagging')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def put_public_access_block(self,
        Bucket: str,
        PublicAccessBlockConfiguration: Dict[str, Any],
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Put Public Access Block operation.

        Args:
            Bucket (str): Required parameter
            PublicAccessBlockConfiguration (Dict[str, Any]): Required parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'PublicAccessBlockConfiguration': PublicAccessBlockConfiguration}
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'put_public_access_block')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def restore_object(self,
        Bucket: str,
        Key: str,
        VersionId: Optional[str] = None,
        RestoreRequest: Optional[Dict[str, Any]] = None,
        RequestPayer: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Restore Object operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            VersionId (Optional[str]): Optional parameter
            RestoreRequest (Optional[Dict[str, Any]]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key}
        if VersionId is not None:
            kwargs['VersionId'] = VersionId
        if RestoreRequest is not None:
            kwargs['RestoreRequest'] = RestoreRequest
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ChecksumAlgorithm is not None:
            kwargs['ChecksumAlgorithm'] = ChecksumAlgorithm
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'restore_object')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def select_object_content(self,
        Bucket: str,
        Key: str,
        Expression: str,
        ExpressionType: str,
        InputSerialization: Dict[str, Any],
        OutputSerialization: Dict[str, Any],
        RequestProgress: Optional[Dict[str, Any]] = None,
        ScanRange: Optional[Dict[str, Any]] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Select Object Content operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            Expression (str): Required parameter
            ExpressionType (str): Required parameter
            InputSerialization (Dict[str, Any]): Required parameter
            OutputSerialization (Dict[str, Any]): Required parameter
            RequestProgress (Optional[Dict[str, Any]]): Optional parameter
            ScanRange (Optional[Dict[str, Any]]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'Expression': Expression, 'ExpressionType': ExpressionType, 'InputSerialization': InputSerialization, 'OutputSerialization': OutputSerialization}
        if RequestProgress is not None:
            kwargs['RequestProgress'] = RequestProgress
        if ScanRange is not None:
            kwargs['ScanRange'] = ScanRange
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'select_object_content')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def upload_file(self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: Optional[Dict[str, Any]] = None,
        Callback: Optional[object] = None,
        Config: Optional[object] = None) -> S3Response:
        """S3 Upload File operation.

        Args:
            Filename (str): Required parameter
            Bucket (str): Required parameter
            Key (str): Required parameter
            ExtraArgs (Optional[Optional[Dict[str, Any]]]): Optional parameter
            Callback (Optional[object]): Optional parameter
            Config (Optional[object]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Filename': Filename, 'Bucket': Bucket, 'Key': Key}
        if ExtraArgs is not None:
            kwargs['ExtraArgs'] = ExtraArgs
        if Callback is not None:
            kwargs['Callback'] = Callback
        if Config is not None:
            kwargs['Config'] = Config

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'upload_file')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def upload_fileobj(self,
        Fileobj: object,
        Bucket: str,
        Key: str,
        ExtraArgs: Optional[Dict[str, Any]] = None,
        Callback: Optional[object] = None,
        Config: Optional[object] = None) -> S3Response:
        """S3 Upload Fileobj operation.

        Args:
            Fileobj (Any): Required parameter
            Bucket (str): Required parameter
            Key (str): Required parameter
            ExtraArgs (Optional[Optional[Dict[str, Any]]]): Optional parameter
            Callback (Optional[object]): Optional parameter
            Config (Optional[object]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Fileobj': Fileobj, 'Bucket': Bucket, 'Key': Key}
        if ExtraArgs is not None:
            kwargs['ExtraArgs'] = ExtraArgs
        if Callback is not None:
            kwargs['Callback'] = Callback
        if Config is not None:
            kwargs['Config'] = Config

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'upload_fileobj')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def upload_part(self,
        Bucket: str,
        Key: str,
        PartNumber: int,
        UploadId: str,
        Body: Optional[object] = None,
        ContentLength: Optional[int] = None,
        ContentMD5: Optional[str] = None,
        ChecksumAlgorithm: Optional[str] = None,
        ChecksumCRC32: Optional[str] = None,
        ChecksumCRC32C: Optional[str] = None,
        ChecksumSHA1: Optional[str] = None,
        ChecksumSHA256: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Upload Part operation.

        Args:
            Bucket (str): Required parameter
            Key (str): Required parameter
            PartNumber (int): Required parameter
            UploadId (str): Required parameter
            Body (Optional[object]): Optional parameter
            ContentLength (Optional[int]): Optional parameter
            ContentMD5 (Optional[str]): Optional parameter
            ChecksumAlgorithm (Optional[str]): Optional parameter
            ChecksumCRC32 (Optional[str]): Optional parameter
            ChecksumCRC32C (Optional[str]): Optional parameter
            ChecksumSHA1 (Optional[str]): Optional parameter
            ChecksumSHA256 (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'Key': Key, 'PartNumber': PartNumber, 'UploadId': UploadId}
        if Body is not None:
            kwargs['Body'] = Body
        if ContentLength is not None:
            kwargs['ContentLength'] = ContentLength
        if ContentMD5 is not None:
            kwargs['ContentMD5'] = ContentMD5
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
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'upload_part')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    async def upload_part_copy(self,
        Bucket: str,
        CopySource: Union[str, Dict[str, Any]],
        Key: str,
        PartNumber: int,
        UploadId: str,
        CopySourceIfMatch: Optional[str] = None,
        CopySourceIfModifiedSince: Optional[datetime] = None,
        CopySourceIfNoneMatch: Optional[str] = None,
        CopySourceIfUnmodifiedSince: Optional[datetime] = None,
        CopySourceRange: Optional[str] = None,
        SSECustomerAlgorithm: Optional[str] = None,
        SSECustomerKey: Optional[str] = None,
        SSECustomerKeyMD5: Optional[str] = None,
        CopySourceSSECustomerAlgorithm: Optional[str] = None,
        CopySourceSSECustomerKey: Optional[str] = None,
        CopySourceSSECustomerKeyMD5: Optional[str] = None,
        RequestPayer: Optional[str] = None,
        ExpectedBucketOwner: Optional[str] = None,
        ExpectedSourceBucketOwner: Optional[str] = None) -> S3Response:
        """S3 Upload Part Copy operation.

        Args:
            Bucket (str): Required parameter
            CopySource (Union[str, Dict[str, Any]]): Required parameter
            Key (str): Required parameter
            PartNumber (int): Required parameter
            UploadId (str): Required parameter
            CopySourceIfMatch (Optional[str]): Optional parameter
            CopySourceIfModifiedSince (Optional[datetime]): Optional parameter
            CopySourceIfNoneMatch (Optional[str]): Optional parameter
            CopySourceIfUnmodifiedSince (Optional[datetime]): Optional parameter
            CopySourceRange (Optional[str]): Optional parameter
            SSECustomerAlgorithm (Optional[str]): Optional parameter
            SSECustomerKey (Optional[str]): Optional parameter
            SSECustomerKeyMD5 (Optional[str]): Optional parameter
            CopySourceSSECustomerAlgorithm (Optional[str]): Optional parameter
            CopySourceSSECustomerKey (Optional[str]): Optional parameter
            CopySourceSSECustomerKeyMD5 (Optional[str]): Optional parameter
            RequestPayer (Optional[str]): Optional parameter
            ExpectedBucketOwner (Optional[str]): Optional parameter
            ExpectedSourceBucketOwner (Optional[str]): Optional parameter

        Returns:
            S3Response: Standardized response with success/data/error format
        """
        kwargs = {'Bucket': Bucket, 'CopySource': CopySource, 'Key': Key, 'PartNumber': PartNumber, 'UploadId': UploadId}
        if CopySourceIfMatch is not None:
            kwargs['CopySourceIfMatch'] = CopySourceIfMatch
        if CopySourceIfModifiedSince is not None:
            kwargs['CopySourceIfModifiedSince'] = CopySourceIfModifiedSince
        if CopySourceIfNoneMatch is not None:
            kwargs['CopySourceIfNoneMatch'] = CopySourceIfNoneMatch
        if CopySourceIfUnmodifiedSince is not None:
            kwargs['CopySourceIfUnmodifiedSince'] = CopySourceIfUnmodifiedSince
        if CopySourceRange is not None:
            kwargs['CopySourceRange'] = CopySourceRange
        if SSECustomerAlgorithm is not None:
            kwargs['SSECustomerAlgorithm'] = SSECustomerAlgorithm
        if SSECustomerKey is not None:
            kwargs['SSECustomerKey'] = SSECustomerKey
        if SSECustomerKeyMD5 is not None:
            kwargs['SSECustomerKeyMD5'] = SSECustomerKeyMD5
        if CopySourceSSECustomerAlgorithm is not None:
            kwargs['CopySourceSSECustomerAlgorithm'] = CopySourceSSECustomerAlgorithm
        if CopySourceSSECustomerKey is not None:
            kwargs['CopySourceSSECustomerKey'] = CopySourceSSECustomerKey
        if CopySourceSSECustomerKeyMD5 is not None:
            kwargs['CopySourceSSECustomerKeyMD5'] = CopySourceSSECustomerKeyMD5
        if RequestPayer is not None:
            kwargs['RequestPayer'] = RequestPayer
        if ExpectedBucketOwner is not None:
            kwargs['ExpectedBucketOwner'] = ExpectedBucketOwner
        if ExpectedSourceBucketOwner is not None:
            kwargs['ExpectedSourceBucketOwner'] = ExpectedSourceBucketOwner

        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, 'upload_part_copy')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            return S3Response(success=False, error=f"{error_code}: {error_message}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e)}")

    # Utility Methods
    async def get_aioboto3_session(self) -> aioboto3.Session:
        """Get the underlying aioboto3 session."""
        return await self._get_aioboto3_session()

    def get_s3_client(self) -> S3Client:
        """Get the S3Client wrapper."""
        return self._s3_client

    async def get_sdk_info(self) -> S3Response:
        """Get information about the wrapped SDK methods."""
        info = {
            'total_methods': 102,
            'sdk_version': aioboto3.__version__,
            'service': 's3'
        }
        return S3Response(success=True, data=info)
