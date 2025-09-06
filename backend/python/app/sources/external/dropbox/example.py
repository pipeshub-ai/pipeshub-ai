import asyncio
from app.sources.client.dropbox.dropbox import DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox import DropboxDataSource

ACCESS_TOKEN = "sl.u.AF9V4q-BYr8FWMhX3lNtTikj1YcE7LP-sZ-5bV6sopUYWGU69E-I4KNAI2xbjt4y_6FWwZ0nWZlBJRCbZ1CxCOU9Y2lsNUTeOFs4q-Ut8RhfDAjN2unWffhbWnfCLrPHdYhK7613EQYvsQnxtmHx30cjbl9dF7NlpZGg5g2mEBFDUvBNgbkZw4SUxqq_VbHkRgpTcEEaLYTVn6FNzWPGFeSW5uOATRG1EKbYaq9I8ZQ5O9vDjUwTdNS1xdSVj9RknLGLyCh_JYlPW5qm4uOJojkqVGfyFcAJT7G6SyaQjY3tCo37WupWp0mi1kD2C44c1facW1H9ml7wuABfB2NBsOPGPJm_W3Ooeo0t547Q_az8iVfIZ2fPyISJvC3b6hL2OX_KRSTSajbInfsHhmO48TGFtB4UlWZcj4ZZM5EZWwRI7MyRnDPGb7HuTWvHBnrlrrsNHwgWH9X0WZ03xT1tUOnaczEEIxGTqZU_6_FJvXneya6CrH1WIH88Be-GvB8GwoyGuQAA56R4wBlINMRhrsP2I8S83d3wunxHQUZ_oLFYzXfyU9kLHSXNQrbb-PLb38rb3XznoGpQ3UL80Tu8Gz0CnXG-DKOkV8zXU2_I9ZyZRGVt7R2ZBBSy3RfDAHQ0XQbZsGjkyQeiLprErFyDHjhpe3jdVbWMaAnzPX4t0p4r0q2QE0Xbr0ezeWSP4AZ6qciEYmok9rBSwNPwRaKb31eJGkNidcjUxR_R0c0E3rWcCu9mzHhWn8AnFgh3qC6p-kNOJzFFMzwWtujILUmruPgE51NdSkRZIkwaD1LNRqKVT2WZJ-Yse9_EX6eMSKK_qigkyp4OPew3jvKFzA3rIN5d8SAKwocuW65Svm0PKjv-tqmnSv02PIecyJilBwnezv6ZAjIXe2jtWY8WjsHh7lhVJSN-bKapDeVn2AKGUSAdcE-L_pOzArAKIcJHo3WjA3zsbJdXFpIUN6I0rt9EObek-_Jr65d2L2ymmlZyXhs1AYsuS47ozT5khrzSv1ARTPv4N12kuuDvbqcFQRZCRyg0plPcEq14Abj9yFq2GG5OOkBj-lHqRYopuuxrvP9lqygtBpcW1CGo11sGQLaCftVdeujz56eeH3mLxPk9vuphjMY6l5nuYS3kx3TFCzqeUAdN-Sq072lPSzEzopc5gwsOGaHkYlHAZ-Uaz1qg35fyLoM11MVSHr5yTHCCbzaJDmvWHrOSqIPSjfxNreZtPioa3tgd4BtHnLKuh0QU88dysSAYV4y1sGyv0Tab_2xknY10CL9uf62KcrDEpcjX4k7bwtS8picrPmKAOIsB4nAdDw"  # Replace with real token

async def main():
    config = DropboxTokenConfig(access_token=ACCESS_TOKEN)
    client = DropboxClient.build_with_config(config)
    data_source = DropboxDataSource(client)

    # List files in root
    print("📂 Listing root folder:")
    files = await data_source.list_folder(path="")
    print(files)

    # Upload a test file
    print("\n⬆️ Uploading test.txt...")
    upload_resp = await data_source.upload("/test.txt", b"Hello from API integration!")
    print(upload_resp)

    # Download the file
    print("\n⬇️ Downloading test.txt...")
    download_resp = await data_source.download("/test.txt")
    print(f"Downloaded bytes: {len(download_resp['data'])}")

    # Get metadata
    print("\nℹ️ Getting metadata for test.txt...")
    metadata = await data_source.get_metadata(path="/test.txt")
    print(metadata)

    # Move the file
    print("\n📦 Moving test.txt to /renamed_test.txt...")
    move_resp = await data_source.move("/test.txt", "/renamed_test.txt")
    print(move_resp)

    # Copy the file
    print("\n📑 Copying renamed_test.txt to /copy_test.txt...")
    copy_resp = await data_source.copy("/renamed_test.txt", "/copy_test.txt")
    print(copy_resp)

    # Search for file
    print("\n🔍 Searching for 'test'...")
    search_resp = await data_source.search("test")
    print(search_resp)

    # Delete files
    print("\n🗑️ Deleting renamed_test.txt...")
    del1 = await data_source.delete("/renamed_test.txt")
    print(del1)

    print("\n🗑️ Deleting copy_test.txt...")
    del2 = await data_source.delete("/copy_test.txt")
    print(del2)

    # Create a folder
    print("\n📁 Creating folder /MyNewFolder...")
    folder_resp = await data_source.create_folder("/MyNewFolder")
    print(folder_resp)

if __name__ == "__main__":
    asyncio.run(main())
