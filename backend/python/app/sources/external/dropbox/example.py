import asyncio
"""
Example usage for DropboxDataSource

This script demonstrates how to use the DropboxDataSource with DropboxClient.
Update the access token and parameters as needed for your Dropbox account.
"""

from app.sources.client.dropbox.dropbox import DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox import DropboxDataSource

# Replace with your actual Dropbox API access token
ACCESS_TOKEN = "sl.u.AF-OT5Ay_mJAhPbmFxQrqy0HH76dNUAtAkl86ZzHz47DxaRAwOr1Xt10DkOGe-c0l-OO4dZN7Kvz57gy8QneyoMLp4XKZufjesufS3TIcZh0rzycJ51w6XamJy_NRfHryg6AnMP2F_kob4_Er3bYUcaDU1Q-Ufr-_HIDHjZzmlIbgnvgCmo_za98jc3gypu2dAFm3VDwHJDm2TlNsDfgDSQ8_O3RJPsTZC7kQn05E9yjEMC6CKGD6E9DfquC4oNPigYWPlho_LEO0ZMnOhFHt70LpzSNcswU3_qHac_YyJRkiCYyR50MJ7oOwl6kTmx5afnrzVMr3F6KJcj3y66ICfHgk7nSGcKqIC_YPZguRo2O_uQBenqwABgKWEUGo9C9ytxa9Vv1X1oBdmHxYcahUgHUyfbtMJaxGjy3rz3lnuXmY1JGA1dXpB9eF6cgmZIbsvX83GTFOOh5TFF8e0agfosQUT4NpirjqASU4EF0PmGMpdwj2-_H6ddajVYWwLAAyhxduAg2qMKNyOpjsVqBzgBissmQbmFCjK1sUlOf9qx6ljXce2IRNp3s8i3IudPs0klvfblF9JKhL9RX1KRDHeE4KskB7-18GXAVFG_wSVLOaGIJmJWgDvabxVt8kCg4iNr510ZxNjVgg37fRWL6-i016wtAhO8gqs4uIkpGWN9Q3r3lO01h537U-dPRjC76eJQ1jMpTOf9erJnI5orx8a1NFBhKjPe9-ou6hq7bX9pxiJUs8bh0F88Uuxw28kWQoX7rF_iQ3WBHFfsOtYVYqYpuWdHyCe9t0WyTbGWnyeHb14bIsdR-2iehdKIwXpjfIJbw_2wLrTJtG_NdaDURPKTJPwluYUrJtVLA-4o_VEygsAPIiv8FsAxfqGCf2mR-iyjQJ5b4Bad8VrQH0l0r5u3B9T-xXA2n9kFiXEV71VkPhoLlsLHWrdezbR0Pcp3fRxmNsN45g-PuWjLKXTEyV0rywpG0UNxI-5oNsCyfwCESTw5Gtrl4GQAq46ZzHsPT9BCIouCp02saLamXnBop4VymoxJ5FHEaxXiTp5UdlcwK9yfIeiCVvHB137QlJ6dXUjPq2xNYS84UpOHQGTvsvAosNCUifObZGlGKuwFNz8vyvfdatzDc0da5xL3WLNEPYCyr4V531hvGncPp7X8ib3mbrphk6dgeN4zz-otq2_d2lufqbkrdpV4Lc5lCgAcWMWfejV12CAyBRL5cKee-qeHoLBlDE1coldnMpAKCms4RRGxhEdCa0nsSb6tH2iZb7E81HAJ6Ze19ZklPeEc4GsJsLALNI1PsfmzAKZWnqqbadw"

async def main():
    # Initialize Dropbox client
    config = DropboxTokenConfig(access_token=ACCESS_TOKEN)
    client = DropboxClient.build_with_config(config)
    # Initialize data source
    data_source = DropboxDataSource(client)

    # Example: List files in the root folder
    print("Listing files in root folder:")
    files = await data_source.list_folder(path="")
    # import pdb; pdb.set_trace()
    print(files)
    for entry in files.get("entries", []):
        print(f"- {entry.get('name')}")

    # Example: Get metadata for a file or folder
    if files.get("entries"):
        first_entry = files["entries"][0]
        metadata = await data_source.get_metadata(path=first_entry["path_display"])
        print("\nMetadata for first entry:")
        print(metadata)

if __name__ == "__main__":
    asyncio.run(main())
