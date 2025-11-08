from app.sources.client.google_cloud.google_cloud import GoogleCloudClient
from app.sources.external.google_cloud.google_cloud_data_source import GoogleCloudDataSource

def main():
    # Initialize client (with service account)
    client = GoogleCloudClient(credentials_path="service-account.json", project_id="your-gcp-project-id")

    # Create data source
    data_source = GoogleCloudDataSource(client)

    # Example usage
    print("Buckets:", data_source.get_all_buckets())
    print(data_source.upload_to_bucket("my-bucket", "local.txt", "uploads/local.txt"))
    print(data_source.download_from_bucket("my-bucket", "uploads/local.txt", "downloaded.txt"))

if __name__ == "__main__":
    main()
