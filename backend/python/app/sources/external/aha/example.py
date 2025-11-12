import asyncio
import logging
import os

from app.sources.client.aha.aha import AhaClient, AhaTokenConfig


async def main() -> None:
    # Setup logger
    logger = logging.getLogger("AhaExample")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")

    # ---- Example configuration ----

    base_url = os.getenv("AHA_BASE_URL")
    access_token = os.getenv("AHA_TOKEN")

    # Create configuration and client
    config = AhaTokenConfig(base_url=base_url, access_token=access_token)
    client = AhaClient.build_with_config(config).get_client()

    try:
        # =============================
        # Fetch Features
        # =============================
        print("\n" + "=" * 80)
        print("📦 Fetching Features...")
        print("=" * 80)

        response = await client.get_features()
        features_data = response.json()

        TOP_FEATURES_LIMIT = 10
        features = features_data.get("features", [])
        print(f"\n✅ Found {len(features)} features:\n")
        for f in features[:TOP_FEATURES_LIMIT]:  # Show first 10
            print(f"  - {f['name']} ({f['reference_num']})")
        if len(features) > TOP_FEATURES_LIMIT:
            print(f"  ...and {len(features) - TOP_FEATURES_LIMIT} more.\n")


        # =============================
        # Fetch Products
        # =============================
        print("\n" + "=" * 80)
        print("🏷️  Fetching Products...")
        print("=" * 80)

        response = await client.get_products()
        products_data = response.json()

        products = products_data.get("products", [])
        print(f"\n✅ Found {len(products)} products:\n")
        for p in products:
            print(f"  - {p['name']} ({p['reference_prefix']})")

        # Optional: pretty print entire JSON
        # print(json.dumps(products_data, indent=2))

        print("\n🎯 All Aha! API calls completed successfully.\n")

    except Exception as e:
        logger.exception(f"Error during Aha! API interaction: {e}")


if __name__ == "__main__":
    asyncio.run(main())
