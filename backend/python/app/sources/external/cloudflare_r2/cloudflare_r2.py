from app.sources.external.minio.minio import MinIODataSource


class CloudflareR2DataSource(MinIODataSource):
    """R2 S3 API operations. The calls are MinIO's; what is specific to R2 is in its client."""
