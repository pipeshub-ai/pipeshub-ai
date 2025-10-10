

from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.featureflag import FeatureFlagService


def example_basic_usage():
    """Example 1: Basic usage"""

    ff_service = FeatureFlagService.get_service()
    if ff_service.is_feature_enabled(CONFIG.ENABLE_WORKFLOW_BUILDER):
        print("Workflow builder is enabled")
    else:
        print("Workflow builder is disabled")

if __name__ == "__main__":
    print("Feature Flag Service Examples\n")

    ff_service = FeatureFlagService.get_service()

    flags_to_check = [
        CONFIG.ENABLE_WORKFLOW_BUILDER,
    ]

    print("Current Feature Flag Status:")
    print("-" * 50)
    for flag in flags_to_check:
        status = ff_service.is_feature_enabled(flag)
        print(f"{flag}: {'✓ Enabled' if status else '✗ Disabled'}")

    print("\n" + "=" * 50)
    print("Service initialized successfully!")
    print("=" * 50)
