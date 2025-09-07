import asyncio

from app.sources.client.dropbox.dropbox import DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox import DropboxDataSource

ACCESS_TOKEN = "sl.u.AF8tM7lDMad7zO1QIpFCuXDKtACxWFU7enBOjOrilbsGwcAiHman52lvGi3os15feE5vvpRveQQIQeZ0MspIOFHaepX6HB3i1-Kz4IxGc08ma_qyW0Epz-NUxs0wRIi2zkGv-dwGv_6D9wHiEBsvVP1460cswE6IVxdYWJmebg7MPa4LNHoxPcggdjtSEJwaRsrK75wXYk--2ugVjdbU_c94LoMwxNs9N070sg2bEMxMFuiSikeRTSigMiLzlVvVtBxBJuHoW0v4JzoMEQr1kB2Vi3-vM19LJiO2oNQDCDmdZ9BVW9usrR33u6wJTg0RdcurP-ZVXotYVWgd3NLulVW7EB8NW57nPdHvVv7svsxChNWso5weCbK-XUKYQILtbRTY1af5ujid7foBk5fAtXYbwlaSUw_31mhum3g7OQGgpDlIaXKis7V7Lu6TrJ5v-14HnIjekVNRDXxL9U9AGS70ab8KDasmafwjvFibaGPF8TdrCQZWGVgFiIQlim1i8dZpmUcntiCQkw6WBrFseOKU6nhQLyUx8zsERklPJE5XvMe-5ROM5_ke8HWGKHdsnmYKUEs6eWSngQd4isuyoQ5sIC-ReE5pYPPoAIBLP6ese6yIhX5NI2p3jULWZrTgorCBJCScQpoZErtEq_WFREceCdHMcbIwYd718aaHXQ34XS5mxyMTFVH3_mH2964XvCKGl--TzUWjEy_UHxDwpiji8pnKSitIMTlhr_fhj6DD18ikA9MShNJrkh5d-vIle9d0Ya5i3KLu7iLaFGeGQs3MJ4aUfsVBQWF4JBxnbbTFlwotcJqsgEBBtX1hXaqVYcVeUtV6HOxW95lRCHECzvO3PmOh_jksfisFA6mwdppFlcyCjTdKnb0OA6yoyPBlAqb6M-lslBXc3pCCRqzb62CaNTj8XL-PXts_aJGYLaf5ALrPJ4HO-odQJRdDRT4oJ9WGun0ZY0PqrhNfmnNkNPngoQkLyN-EfYF0w_3HJsXECoTO1ulzBNTDZJ4vI_621JZ4dfUGsSPrh8COoEkcShb8jIs4b0uIUDAFJpw825u_ynTuRsSWaLtXsxSy3g2TYxdl8G6awOLCO1ut4SKCc_A3lEIs0sSubZ-nS7BuvK4avIE8CGIO2joLmPhuCu-CnipNh5Te15vij1s5QNRVQ-GDpPjHPt2Wlumd3cSRgcLch_pSfv3gas--ARxWota2Pw-favND6VpBXtW3vEVRV6TuQ4sNUnKohT7klRbcx1zJMAE4RGKx3CnsVzMvfe1bbPAcT0EEG_4wSPe8Q33_0C1GwGOlh9Yw4NDZVXInc6uS3w"

async def main() -> None:
    config = DropboxTokenConfig(access_token=ACCESS_TOKEN)
    client = DropboxClient.build_with_config(config)
    data_source = DropboxDataSource(client)

    # List files in root
    print("Listing root folder:")
    files = await data_source.list_folder(path="")
    print(files)

    # Upload a test file
    print("\nUploading test.txt...")
    upload_resp = await data_source.upload("/test.txt", b"Hello from API integration!")
    print(upload_resp)

    # Download the file
    print("\nDownloading test.txt...")
    download_resp = await data_source.download("/test.txt")
    print(f"Downloaded bytes: {len(download_resp['data'])}")

    # Get metadata
    print("\nGetting metadata for test.txt...")
    metadata = await data_source.get_metadata(path="/test.txt")
    print(metadata)

    # Move the file
    print("\nMoving test.txt to /renamed_test.txt...")
    move_resp = await data_source.move("/test.txt", "/renamed_test.txt")
    print(move_resp)

    # Copy the file
    print("\nCopying renamed_test.txt to /copy_test.txt...")
    copy_resp = await data_source.copy("/renamed_test.txt", "/copy_test.txt")
    print(copy_resp)

    # Search for file
    print("\nSearching for 'test'...")
    search_resp = await data_source.search("test")
    print(search_resp)

    # Delete files
    print("\nDeleting renamed_test.txt...")
    del1 = await data_source.delete("/renamed_test.txt")
    print(del1)

    print("\nDeleting copy_test.txt...")
    del2 = await data_source.delete("/copy_test.txt")
    print(del2)

    # Create a folder
    print("\nCreating folder /MyNewFolder3...")
    folder_resp = await data_source.create_folder("/MyNewFolder3")
    print(folder_resp)

if __name__ == "__main__":
    asyncio.run(main())
