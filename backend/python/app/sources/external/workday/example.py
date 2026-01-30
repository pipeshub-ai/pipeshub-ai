import asyncio
import logging
import os

from app.sources.client.workday import (
    WorkdayClient,
    WorkdayConfig,
)
from app.sources.external.workday.workday import WorkdayDataSource

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main() -> None:
    workday_base_url = os.environ.get("WORKDAY_BASE_URL")
    workday_token = os.environ.get("WORKDAY_TOKEN")
    workday_oauth_token = os.environ.get("WORKDAY_OAUTH_TOKEN")

    if not workday_base_url:
        logger.error("WORKDAY_BASE_URL environment variable is required")
        return

    # Use whichever token is available (API token or OAuth token)
    token = workday_token or workday_oauth_token
    if not token:
        logger.error("Either WORKDAY_TOKEN or WORKDAY_OAUTH_TOKEN environment variable is required")
        return

    logger.info(f"Using {'Token' if workday_token else 'OAuth'} Authentication")
    config = WorkdayConfig(base_url=workday_base_url, token=token)
    client = WorkdayClient.build_with_config(config)

    # Initialize Data Source
    data_source = WorkdayDataSource(client)

    logger.info("=== Workday API Operations Demo ===\n")

    # =========================================================================
    # 1. WORKERS / USERS OPERATIONS
    # =========================================================================
    logger.info("📋 [1] WORKERS (Users) Operations")
    logger.info("-" * 60)

    # List workers with pagination
    logger.info("  ↳ Fetching workers list (limit: 5)...")
    workers_response = await data_source.list_staffing_v7_workers(limit=5, offset=0)
    if workers_response.success:
        workers_count = len(workers_response.data.get('data', [])) if workers_response.data else 0
        logger.info(f"  ✓ Successfully fetched {workers_count} workers")

        # Get details of first worker if available
        if workers_response.data and workers_response.data.get('data'):
            first_worker = workers_response.data['data'][0]
            worker_id = first_worker.get('id')
            if worker_id:
                logger.info(f"  ↳ Fetching details for worker: {worker_id}")
                worker_detail = await data_source.get_staffing_v7_workers(ID=worker_id)
                if worker_detail.success:
                    logger.info("  ✓ Retrieved worker details successfully")
                else:
                    logger.error(f"  ✗ Failed to fetch worker details: {worker_detail.error}")
    else:
        logger.error(f"  ✗ Failed to fetch workers: {workers_response.error}")

    # Get worker personal information
    logger.info("\n  ↳ Fetching worker personal information...")
    personal_info = await data_source.list_person_v4_people_personal_information(limit=3)
    if personal_info.success:
        info_count = len(personal_info.data.get('data', [])) if personal_info.data else 0
        logger.info(f"  ✓ Retrieved personal info for {info_count} people")
    else:
        logger.error(f"  ✗ Failed to fetch personal info: {personal_info.error}")

    # =========================================================================
    # 2. GROUPS / SUPERVISORY ORGANIZATIONS OPERATIONS
    # =========================================================================
    logger.info("\n📂 [2] GROUPS (Supervisory Organizations) Operations")
    logger.info("-" * 60)

    # List supervisory organizations
    logger.info("  ↳ Fetching supervisory organizations...")
    orgs_response = await data_source.list_staffing_v7_supervisory_organizations(limit=5)
    if orgs_response.success:
        orgs_count = len(orgs_response.data.get('data', [])) if orgs_response.data else 0
        logger.info(f"  ✓ Successfully fetched {orgs_count} organizations")

        # Get details of first organization
        if orgs_response.data and orgs_response.data.get('data'):
            first_org = orgs_response.data['data'][0]
            org_id = first_org.get('id')
            if org_id:
                logger.info(f"  ↳ Fetching details for organization: {org_id}")
                org_detail = await data_source.get_staffing_v7_supervisory_organizations(ID=org_id)
                if org_detail.success:
                    logger.info("  ✓ Retrieved organization details successfully")

                # Get workers in this organization
                logger.info(f"  ↳ Fetching workers in organization: {org_id}")
                org_workers = await data_source.list_staffing_v7_supervisory_organizations_workers(
                    ID=org_id,
                    limit=5
                )
                if org_workers.success:
                    worker_count = len(org_workers.data.get('data', [])) if org_workers.data else 0
                    logger.info(f"  ✓ Found {worker_count} workers in this organization")
    else:
        logger.error(f"  ✗ Failed to fetch organizations: {orgs_response.error}")

    # =========================================================================
    # 3. PERMISSIONS / SECURITY GROUPS
    # =========================================================================
    logger.info("\n🔒 [3] PERMISSIONS (Security Groups) Operations")
    logger.info("-" * 60)

    # Get common API workers organizations (includes security groups)
    logger.info("  ↳ Fetching worker organizations (includes security context)...")
    worker_orgs = await data_source.list_api_common_v1_workers_organizations(limit=5)
    if worker_orgs.success:
        orgs_count = len(worker_orgs.data.get('data', [])) if worker_orgs.data else 0
        logger.info(f"  ✓ Retrieved {orgs_count} worker organization records")
    else:
        logger.error(f"  ✗ Failed to fetch worker organizations: {worker_orgs.error}")

    # Get supervisory organizations managed (permission context)
    logger.info("  ↳ Fetching managed supervisory organizations...")
    managed_orgs = await data_source.list_api_common_v1_workers_supervisory_organizations_managed(limit=5)
    if managed_orgs.success:
        managed_count = len(managed_orgs.data.get('data', [])) if managed_orgs.data else 0
        logger.info(f"  ✓ Retrieved {managed_count} managed organizations")
    else:
        logger.error(f"  ✗ Failed to fetch managed orgs: {managed_orgs.error}")

    # =========================================================================
    # 4. HR ROLES / JOB PROFILES OPERATIONS
    # =========================================================================
    logger.info("\n👤 [4] HR ROLES (Job Profiles) Operations")
    logger.info("-" * 60)

    # List job profiles (HR roles)
    logger.info("  ↳ Fetching job profiles...")
    profiles_response = await data_source.list_staffing_v7_job_profiles(limit=5)
    if profiles_response.success:
        profiles_count = len(profiles_response.data.get('data', [])) if profiles_response.data else 0
        logger.info(f"  ✓ Successfully fetched {profiles_count} job profiles")

        # Get details of first job profile
        if profiles_response.data and profiles_response.data.get('data'):
            first_profile = profiles_response.data['data'][0]
            profile_id = first_profile.get('id')
            if profile_id:
                logger.info(f"  ↳ Fetching details for job profile: {profile_id}")
                profile_detail = await data_source.get_staffing_v7_job_profiles(ID=profile_id)
                if profile_detail.success:
                    logger.info("  ✓ Retrieved job profile details successfully")
    else:
        logger.error(f"  ✗ Failed to fetch job profiles: {profiles_response.error}")

    # List job families (role hierarchies)
    logger.info("  ↳ Fetching job families...")
    families_response = await data_source.list_staffing_v7_job_families(limit=5)
    if families_response.success:
        families_count = len(families_response.data.get('data', [])) if families_response.data else 0
        logger.info(f"  ✓ Successfully fetched {families_count} job families")
    else:
        logger.error(f"  ✗ Failed to fetch job families: {families_response.error}")

    # =========================================================================
    # 5. ADDITIONAL OPERATIONS
    # =========================================================================
    logger.info("\n➕ [5] Additional Operations")
    logger.info("-" * 60)

    # List worker direct reports (reporting hierarchy)
    logger.info("  ↳ Fetching worker direct reports...")
    reports_response = await data_source.list_api_common_v1_workers_direct_reports(limit=5)
    if reports_response.success:
        reports_count = len(reports_response.data.get('data', [])) if reports_response.data else 0
        logger.info(f"  ✓ Retrieved {reports_count} direct report records")
    else:
        logger.error(f"  ✗ Failed to fetch direct reports: {reports_response.error}")

    # List worker history (employment changes)
    logger.info("  ↳ Fetching worker history...")
    history_response = await data_source.list_api_common_v1_workers_history(limit=5)
    if history_response.success:
        history_count = len(history_response.data.get('data', [])) if history_response.data else 0
        logger.info(f"  ✓ Retrieved {history_count} history records")
    else:
        logger.error(f"  ✗ Failed to fetch history: {history_response.error}")

    logger.info("\n" + "=" * 60)
    logger.info("✅ Demo completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())

