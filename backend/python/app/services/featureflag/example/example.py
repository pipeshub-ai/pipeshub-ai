

from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.featureflag import FeatureFlagService


def example_basic_usage() -> None:
    """Example 1: Basic usage"""

    ff_service = FeatureFlagService.get_service()
    if ff_service.is_feature_enabled(CONFIG.ENABLE_WORKFLOW_BUILDER):
        print("Workflow builder is enabled")
    else:
        print("Workflow builder is disabled")

if __name__ == "__main__":
    print("Feature Flag Service Examples\n")

    example_basic_usage()
