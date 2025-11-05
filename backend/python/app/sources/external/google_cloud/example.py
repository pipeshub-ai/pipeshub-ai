# app/sources/external/google_cloud/example.py

import logging
import os
import sys

# Add the project root to the path to allow imports from 'app'
# FIX: Go up four levels (..) instead of three to reach 'backend/python'
project_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
sys.path.insert(0, project_root)

try:
    from app.sources.client.google_cloud.google_cloud import (
        GoogleCloudClient,
        GoogleCloudServiceAccountConfig,
    )
    from app.sources.external.google_cloud.google_cloud import GoogleCloudDataSource
except ImportError:
    print(
        "Error: Could not import necessary modules."
        "Ensure you are running this from the 'backend/python' directory"
        "or that your PYTHONPATH is set correctly."
    )
    sys.exit(1)


# --- Configuration ---
# Set up basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# !!! IMPORTANT !!!
# Set this environment variable before running the example:
# export GCS_SERVICE_ACCOUNT_PATH="/path/to/your/service-account-key.json"
#
# You also need to specify a bucket and file to test with:
# export GCS_TEST_BUCKET_NAME="your-test-bucket-name"
# export GCS_TEST_FILE_NAME="your-test-file.txt"

SERVICE_ACCOUNT_PATH = os.environ.get("GCS_SERVICE_ACCOUNT_PATH")
TEST_BUCKET = os.environ.get("GCS_TEST_BUCKET_NAME")
TEST_FILE = os.environ.get("GCS_TEST_FILE_NAME")
# ---------------------


def main():
    """
    Run the example to test the GoogleCloudDataSource.
    """
    if not SERVICE_ACCOUNT_PATH or not TEST_BUCKET or not TEST_FILE:
        logger.error(
            "Missing environment variables. Please set:"
            "GCS_SERVICE_ACCOUNT_PATH, GCS_TEST_BUCKET_NAME, and GCS_TEST_FILE_NAME"
        )
        return

    logger.info("--- Starting Google Cloud Storage Example ---")

    try:
        # 1. Create the configuration
        config = GoogleCloudServiceAccountConfig(
            service_account_json_path=SERVICE_ACCOUNT_PATH
        )

        # 2. Build the GoogleCloudClient
        # This uses the classmethod build_with_config
        gcs_client = GoogleCloudClient.build_with_config(config)

        # 3. Instantiate the DataSource with the client
        data_source = GoogleCloudDataSource(client=gcs_client)

        # 4. Test API: List all buckets
        logger.info("--- 1. Listing Buckets ---")
        buckets = data_source.get_all_buckets()
        if buckets:
            logger.info(f"Successfully retrieved {len(buckets)} buckets.")
            for i, bucket in enumerate(buckets[:5]):  # Print first 5
                logger.info(f"  - Bucket {i + 1}: {bucket.name}")
        else:
            logger.warning("Could not retrieve any buckets.")

        # 5. Test API: List files in a specific bucket
        logger.info(f"--- 2. Listing Files in Bucket: {TEST_BUCKET} ---")
        blobs = data_source.get_files_in_bucket(TEST_BUCKET)
        if blobs:
            logger.info(f"Successfully retrieved {len(blobs)} files (blobs).")
            for i, blob in enumerate(blobs[:5]):  # Print first 5
                logger.info(f"  - File {i + 1}: {blob.name}")
        else:
            logger.warning(f"Could not retrieve any files from {TEST_BUCKET}.")

        # 6. Test API: Get file content
        logger.info(f"--- 3. Reading File Content: {TEST_FILE} ---")
        content = data_source.get_file_content(TEST_BUCKET, TEST_FILE)
        if content:
            logger.info(f"Successfully read content from {TEST_FILE}:")
            # Print first 200 characters
            print(f"Content preview: '{content[:200]}...'")
        else:
            logger.warning(f"Could not read content from {TEST_FILE}.")

    except Exception as e:
        logger.error(f"An error occurred during the example run: {e}", exc_info=True)

    logger.info("--- Google Cloud Storage Example Finished ---")


if __name__ == "__main__":
    main()
