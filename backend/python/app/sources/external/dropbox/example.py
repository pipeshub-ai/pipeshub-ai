import asyncio
from app.sources.client.dropbox.dropbox import DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox import DropboxDataSource

ACCESS_TOKEN = "sl.u.AF--YKpcUKuKDQJEbkTf4oVI4XXluYeg1woCrhXT7EJOPUgXtSMF0m6iwJadf4zY9Dra3ugQzL6q8g0zPipamTqMsbqz0BjcNGyqo058vQLHTV1-nFpKUE5TP1-02OO-BL3LHoXhZC1cV88JHdgori65Y_7xS6IDegKU4V73h-Yxi8R0_icHsvROGkQqKf2kXDp9QFC2nSxHeV2tWuyJiW4ADYGhvoa2E7oWYpwJR5fjrA8cbo3nvM0LPpz9IMfQNKoLa50SGiNd7-PHavWDQC_ZczpUUc6kz7fDDRrh6cqzi2TH2WC3IlB3czj6trIT_P-EDkuSKRyBEFL4MWlr-hpFixR8nKdblyp7RtHfs19HIXsuF-37JelnAZVXzNK95pdokK0MNKMYtWE6__aPbFiY6qSGkol2mIw49Rfqvx-RBT-KYqbHdiGsC9Z3Roork9iE1sNLLRn-nbEjikkHF2Fz14_o7aWobpixGqJ4K_ngelfSwWQiX0mDbLHonCayWYveUWC3UP1fCilCEJtjkD3w3kVYTcHem5AqERDYSKvaA__Q5r6vDimPgxONvw1EtROGfYR3Cq6HbLxS1ZB1yLGBgkg4nnQG-kvwEJsfDAZKYL4lv_laPKXKXBHsebAAYSpc2Qj5ujfzfEGhiYNrhWr7_tphh-buhOcIGbP5Db5-aAPTky2cQ4LaSh4H3E5KE0mbDOU4hafB2jgzEfZn2H3wLphk7C7JamKDJHM7qrcjklKmLpWz1T7UGcT1W_p82HAXjO1UMS0lQikhrH5ToR5rB0z5da5Js_ustZCNUPy9mnG7wFR181mQIYdKFUO-ASfgriUZXq69JVurWXYrjd88UVjWH8Pzf5t7hRS2areAsrUU0mx4JDx9iBKEcQAouAN6eRxlyZ-Vw2SyBHDa0OPaLCtqQZtO4s4GJiwFZPJQ3h2WMV_Mgckys1TbQktUmefUfKODyKzkiMg9ON5yBaKu-7lm6OYqU2gKTwdQXiVp9Eh6lgY-qrgUJMN52hKi000k-MfNAUOxJpAb00S75zLHtPQlr42kfaesE08OhBxhVGpNYJS40odORYbLzCV6M4wzOg0Og1zoF-u2exed8899Zd4w4H942O27_1kRrHoiGqvqVwnxPD57E7VI3ApENrqhEzjtfas0vFA8k8duzkoEUuzZEO5ihaafYOtBV3SQu9eHCzutgesojMuqQ-XZAvmnq2WTznVN7i2OnVY9lFwtovuqPRzqNrJ1yUyast_tX_QbC-Dbb-el13OxEGa9y4TG7WKzgxX6HXIIw4OWubmZYDwUIxbGxfB9R5gbele49A"  # Replace with real token

async def main():
    config = DropboxTokenConfig(access_token=ACCESS_TOKEN)
    client = DropboxClient.build_with_config(config)
    data_source = DropboxDataSource(client)

    # List files in root
    print("Listing root folder:")
    files = await data_source.list_folder(path="")
    print(files)

    # Upload a test file
    print("\n Uploading test.txt...")
    upload_resp = await data_source.upload("/test.txt", b"Hello from API integration!")
    print(upload_resp)

    # Download the file
    print("\n Downloading test.txt...")
    download_resp = await data_source.download("/test.txt")
    print(f"Downloaded bytes: {len(download_resp['data'])}")

    # Get metadata
    print("\n Getting metadata for test.txt...")
    metadata = await data_source.get_metadata(path="/test.txt")
    print(metadata)

    # Move the file
    print("\n Moving test.txt to /renamed_test.txt...")
    move_resp = await data_source.move("/test.txt", "/renamed_test.txt")
    print(move_resp)

    # Copy the file
    print("\n Copying renamed_test.txt to /copy_test.txt...")
    copy_resp = await data_source.copy("/renamed_test.txt", "/copy_test.txt")
    print(copy_resp)

    # Search for file
    print("\n Searching for 'test'...")
    search_resp = await data_source.search("test")
    print(search_resp)

    # Delete files
    print("\n Deleting renamed_test.txt...")
    del1 = await data_source.delete("/renamed_test.txt")
    print(del1)

    print("\n Deleting copy_test.txt...")
    del2 = await data_source.delete("/copy_test.txt")
    print(del2)

    # Create a folder
    print("\n Creating folder /MyNewFolder2...")
    folder_resp = await data_source.create_folder("/MyNewFolder3")
    print(folder_resp)

if __name__ == "__main__":
    asyncio.run(main())
