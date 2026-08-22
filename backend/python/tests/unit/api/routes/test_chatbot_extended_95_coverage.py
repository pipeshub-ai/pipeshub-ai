"""Targeted coverage for app.api.routes.chatbot (attachments, web search stream, helpers)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.blocks import BlockType


# ---------------------------------------------------------------------------
# Helpers: collapse, MIME, attachment extension
# ---------------------------------------------------------------------------


class TestAttachmentMimeHelpers:
    def test_supported_mimes(self):
        from app.api.routes.chatbot import _is_supported_attachment_mime

        assert _is_supported_attachment_mime("application/pdf") is True
        assert _is_supported_attachment_mime("IMAGE/PNG") is True
        assert _is_supported_attachment_mime("text/plain") is True
        assert _is_supported_attachment_mime("text/markdown") is True
        assert _is_supported_attachment_mime("text/mdx") is True

    def test_text_attachment_detection(self):
        from app.api.routes.chatbot import _is_text_attachment

        assert _is_text_attachment("text/plain") is True
        assert _is_text_attachment("TEXT/MARKDOWN") is True
        assert _is_text_attachment("text/mdx") is True
        assert _is_text_attachment("application/pdf") is False

    def test_image_detection(self):
        from app.api.routes.chatbot import _is_image_attachment

        assert _is_image_attachment("image/webp") is True

    def test_extension_prefers_suffix(self):
        from app.api.routes.chatbot import _attachment_extension

        assert _attachment_extension("Report.PDF", "application/pdf") == "pdf"

    def test_extension_from_mime_fallbacks(self):
        from app.api.routes.chatbot import _attachment_extension

        assert _attachment_extension("no_suffix", "application/pdf") == "pdf"
        assert _attachment_extension("x", "image/jpeg") == "jpg"
        assert _attachment_extension("x", "image/png") == "png"
        assert _attachment_extension("x", "text/plain") == "txt"
        assert _attachment_extension("x", "text/markdown") == "md"
        assert _attachment_extension("x", "text/mdx") == "mdx"
        assert _attachment_extension("plain", "application/octet-stream") == "bin"


@pytest.mark.parametrize("mime,expected_subtype", [
    ("image/jpeg", "image/jpeg"),
    ("image/png", "image/png"),
])
def test_build_image_blocks_mime_paths(mime, expected_subtype):
    from app.api.routes.chatbot import _build_image_blocks

    raw = b"\x89PNG\r\n\x1a\n" if "png" in mime else b"\xff\xd8\xff\xd9"
    container = _build_image_blocks(raw, mime)
    assert container.blocks[0].data["uri"].startswith(f"data:{expected_subtype}")


@pytest.mark.asyncio
async def test_build_text_blocks_parses_markdown():
    from app.api.routes.chatbot import _build_text_blocks

    container = await _build_text_blocks(b"# Hello\n\nWorld")
    assert container.blocks
    assert any(
        getattr(b, "type", None) == "text" or (isinstance(b, dict) and b.get("type") == "text")
        for b in container.blocks
    )


@pytest.mark.parametrize("needs_ocr_per_page,len_pages,expect", [
    (True, 1, True),
    (False, 2, False),
])
def test_pdf_has_any_ocr_page_mocked_doc(needs_ocr_per_page, len_pages, expect):
    """ocr_count / total >= 0.5 when all scanned pages."""
    from app.api.routes.chatbot import _pdf_has_any_ocr_page

    mock_pages = [MagicMock() for _ in range(len_pages)]
    mock_pdf = MagicMock()
    mock_pdf.pages = mock_pages

    class FakePDFOpen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return mock_pdf

        def __exit__(self, *_):
            pass

    with patch("app.api.routes.chatbot.pdfplumber.open", FakePDFOpen):
        with patch(
            "app.api.routes.chatbot.OCRStrategy.needs_ocr",
            return_value=needs_ocr_per_page,
        ):
            assert _pdf_has_any_ocr_page(b"%PDF-x") is expect


def test_pdf_has_any_empty_doc():
    from app.api.routes.chatbot import _pdf_has_any_ocr_page

    mock_pdf = MagicMock()
    mock_pdf.pages = []

    class FakePDFOpen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return mock_pdf

        def __exit__(self, *_):
            pass

    with patch("app.api.routes.chatbot.pdfplumber.open", FakePDFOpen):
        assert _pdf_has_any_ocr_page(b"%PDF-empty") is False


def test_pdf_page_count_fixture():
    from app.api.routes.chatbot import _pdf_page_count

    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock() for _ in range(5)]

    class FakePDFOpen:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return mock_pdf

        def __exit__(self, *_):
            pass

    with patch("app.api.routes.chatbot.pdfplumber.open", FakePDFOpen):
        assert _pdf_page_count(b"%PDF-x") == 5


def test_build_pdf_image_blocks_one_page():
    from app.api.routes.chatbot import _build_pdf_image_blocks

    mock_image = MagicMock()

    with patch(
        "app.api.routes.chatbot.render_all_pages_as_pil_from_bytes_sync",
        return_value=[mock_image],
    ) as mock_render:
        bc = _build_pdf_image_blocks(b"%PDF-sample")
        assert len(bc.blocks) == 1
        assert bc.blocks[0].type == BlockType.IMAGE
        mock_render.assert_called_once_with(b"%PDF-sample", resolution=144)
        mock_image.save.assert_called_once()


@pytest.mark.asyncio
async def test_upload_chat_attachments_json_and_payload_errors():

    from app.api.routes.chatbot import upload_chat_attachments

    r1 = MagicMock()
    r1.json = AsyncMock(side_effect=ValueError("bad"))
    with pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(r1, AsyncMock(), AsyncMock())
    assert exc.value.status_code == 400

    r2 = MagicMock()
    r2.json = AsyncMock(return_value={"conversationId": "c"})
    with pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(r2, AsyncMock(), AsyncMock())
    assert "attachments" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_upload_chat_attachments_missing_org():
    import base64

    from app.api.routes.chatbot import upload_chat_attachments

    png_b64 = base64.b64encode(b"x" * 8).decode("ascii")
    body = MagicMock()
    body.json = AsyncMock(
        return_value={
            "attachments": [
                {
                    "fileName": "x.png",
                    "mimeType": "image/png",
                    "size": 50,
                    "contentBase64": png_b64,
                },
            ],
        },
    )
    body.state.user = {}
    with pytest.raises(HTTPException) as exc:
        await upload_chat_attachments(body, AsyncMock(), AsyncMock())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_png_happy_mocked_sink():
    import base64

    from app.models.blocks import BlocksContainer

    from app.api.routes.chatbot import upload_chat_attachments

    png_b64 = base64.b64encode(b"z" * 32).decode("ascii")
    req = MagicMock()
    req.json = AsyncMock(
        return_value={
            "conversationId": "conv-z",
            "attachments": [
                {
                    "fileName": "f.png",
                    "mimeType": "image/png",
                    "size": 32,
                    "contentBase64": png_b64,
                },
            ],
        },
    )
    req.state.user = {"orgId": "org-x", "userId": "u-x", "isServiceAccount": False}

    gp = AsyncMock()

    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "gk"})

    gp.batch_upsert_nodes = AsyncMock()
    gp.batch_create_edges = AsyncMock()

    cont = MagicMock()
    req.app.container = cont
    cont.logger.return_value = MagicMock()

    with patch(
        "app.api.routes.chatbot.BlobStorage",
    ) as mock_bs_cls:

        bs_inst = AsyncMock()

        bs_inst.save_binary_to_storage = AsyncMock(return_value=("ext", None))

        mock_bs_cls.return_value = bs_inst

        with patch(
            "app.api.routes.chatbot.GraphDBTransformer",
            return_value=MagicMock(),
        ):
            orch = AsyncMock()

            orch.apply = AsyncMock()

            fake_rec = MagicMock()

            fake_rec.block_containers = BlocksContainer(blocks=[], block_groups=[])

            with patch(
                "app.api.routes.chatbot.SinkOrchestrator",
                return_value=orch,
            ):
                with patch(
                    "app.api.routes.chatbot.convert_record_dict_to_record",
                    return_value=fake_rec,
                ):
                    with patch(
                        "app.api.routes.chatbot.TransformContext",
                        MagicMock(side_effect=lambda **_: MagicMock()),
                    ):
                        out = await upload_chat_attachments(req, gp, AsyncMock())

    assert out["conversationId"] == "conv-z"
    assert len(out["attachments"]) == 1
    orch.index.assert_awaited()


@pytest.mark.asyncio
async def test_grant_revoke_attachment_permissions():

    import app.api.routes.chatbot as cr

    from app.api.routes.chatbot import grant_attachment_permissions, revoke_attachment_permissions

    req_g = MagicMock()

    req_g.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["rec"]})

    gp = AsyncMock()

    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "k1"})

    gp.batch_create_edges = AsyncMock()

    gp.batch_delete_edges = AsyncMock()

    with patch.object(cr.logger, "warning", MagicMock()):

        r1 = await grant_attachment_permissions(req_g, gp)
        assert r1["granted"] == 1

    req_none = MagicMock()

    req_none.json = AsyncMock(return_value={"userIds": [], "recordIds": []})

    r2 = await grant_attachment_permissions(req_none, gp)
    assert r2["granted"] == 0

    gp.get_user_by_user_id = AsyncMock(return_value=None)
    req_skip = MagicMock()

    req_skip.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["rec"]})

    with patch.object(cr.logger, "warning", MagicMock()):

        r3 = await grant_attachment_permissions(req_skip, gp)

    assert r3["granted"] == 0

    req_r = MagicMock()

    req_r.json = AsyncMock(return_value={"userIds": ["x"], "recordIds": ["y"]})

    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "kk"})

    out_r = await revoke_attachment_permissions(req_r, gp)
    gp.batch_delete_edges.assert_awaited()
    assert out_r["revoked"] >= 1


@pytest.mark.asyncio
async def test_delete_chat_attachment_paths():
    from app.api.routes.chatbot import delete_chat_attachment

    r = MagicMock()
    r.state.user = {}
    with pytest.raises(HTTPException) as ex:
        await delete_chat_attachment("rid", r, AsyncMock())
    assert ex.value.status_code == 400

    gp = AsyncMock()

    gp.get_document = AsyncMock(return_value={"orgId": "other"})
    rr = MagicMock()
    rr.state.user = {"orgId": "mine"}
    with pytest.raises(HTTPException) as ex:

        await delete_chat_attachment("rid", rr, gp)

    assert ex.value.status_code == 403


@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_upload_missing_user_context():
    import base64

    from app.api.routes.chatbot import upload_chat_attachments

    rr = MagicMock()
    rr.json = AsyncMock(
        return_value={
            "attachments": [
                {
                    "fileName": "a.png",
                    "mimeType": "image/png",
                    "size": 4,
                    "contentBase64": base64.b64encode(b"abcd").decode(),
                },
            ],
        },
    )
    rr.state.user = {"orgId": "org"}
    rr.app.container.logger.return_value = MagicMock()
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value={"_key": "k"})
    with pytest.raises(HTTPException) as ex:
        await upload_chat_attachments(rr, gp, AsyncMock())
    assert ex.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_user_resolve_failures():
    import base64

    from app.api.routes.chatbot import upload_chat_attachments

    att = {
        "fileName": "a.png",
        "mimeType": "image/png",
        "size": 8,
        "contentBase64": base64.b64encode(b"12345678").decode(),
    }

    rr = MagicMock()

    rr.json = AsyncMock(return_value={"attachments": [att]})


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": False}




    rr.app.container.logger.return_value = MagicMock()



    gp1 = AsyncMock()



    gp1.get_user_by_user_id = AsyncMock(return_value=None)



    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp1, AsyncMock())


    assert ex.value.status_code == 404



    gp2 = AsyncMock()


    gp2.get_user_by_user_id = AsyncMock(return_value={"noKeys": True})


    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp2, AsyncMock())


    assert ex.value.status_code == 500




@pytest.mark.asyncio
async def test_upload_validation_helpers():
    import base64



    from app.api.routes.chatbot import upload_chat_attachments




    user = {"orgId": "o", "userId": "u", "isServiceAccount": True}




    rr = MagicMock()


    rr.state.user = user


    rr.app.container.logger.return_value = MagicMock()



    rr.json = AsyncMock(

        return_value={

            "attachments": [{"fileName": "x.gif", "mimeType": "image/gif", "size": 8,

                             "contentBase64": base64.b64encode(b"zzzzzzzz").decode()}],

        },




    )


    gp = AsyncMock()


    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert ex.value.status_code == 400















    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "n.png",




                                                      "mimeType": "image/png",

                                                      "size": 0,




                                                      "contentBase64": base64.b64encode(b"z").decode(),



                                                     }]},




                    )



    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert "positive" in str(ex.value.detail).lower()

















    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "n.png",




                                                      "mimeType": "image/png",




                                                      "size": 2,




                                                      "contentBase64": "bad!!!",

                                                      }]},


                    )



    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert "Invalid base64" in str(ex.value.detail)





@pytest.mark.asyncio

async def test_upload_image_block_failure():






    import base64


    from app.api.routes.chatbot import upload_chat_attachments








    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr.app.container.logger.return_value = MagicMock()


    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "i.png",




                                                      "mimeType": "image/png",

                                                      "size": 8,

                                                      "contentBase64":




                                                      base64.b64encode(b"hhhhhhhh").decode(),



                                                      }]},




                    )


    gp = AsyncMock()


    gp.batch_upsert_nodes = AsyncMock()


    gp.batch_create_edges = AsyncMock()






    orch = AsyncMock()


    orch.apply = AsyncMock()


    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bs = AsyncMock()


        bs.save_binary_to_storage = AsyncMock(return_value=("x", None))


        bscl.return_value = bs


        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot._build_image_blocks", side_effect=RuntimeError("x")):


                with pytest.raises(HTTPException) as ex:


                    await upload_chat_attachments(rr, gp, AsyncMock())


                assert ex.value.status_code == 400















@pytest.mark.asyncio


async def test_upload_pdf_regular_and_scan_cap():




    import base64




    from app.models.blocks import BlocksContainer




    from app.api.routes.chatbot import OCR_IMAGE_PAGE_CAP, upload_chat_attachments

















    pdf_b64 = base64.b64encode(b"%PDF-xx").decode()








    pym = MagicMock()


    pym.parse_document = AsyncMock(return_value={"chunks": True})


    pym.create_blocks = AsyncMock(return_value=BlocksContainer(blocks=[], block_groups=[]))





    orch_ok = AsyncMock()


    orch_ok.apply = AsyncMock()





    fakerec = MagicMock()


    fakerec.block_containers = BlocksContainer(blocks=[], block_groups=[])






    rr_ok = MagicMock()


    rr_ok.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr_ok.app.container.logger.return_value = MagicMock()


    rr_ok.json = AsyncMock(return_value={"attachments": [{"fileName": "f.pdf",


                                                           "mimeType": "application/pdf",


                                                           "size": 8,




                                                           "contentBase64": pdf_b64,




                                                           }]},




                    )








    gp_ok = AsyncMock()


    gp_ok.batch_upsert_nodes = AsyncMock()


    gp_ok.batch_create_edges = AsyncMock()












    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bins = AsyncMock()


        bins.save_binary_to_storage = AsyncMock(return_value=("id", None))


        bscl.return_value = bins




        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):


                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=False):


                    with patch(


                        "app.api.routes.chatbot.convert_record_dict_to_record",





                        return_value=fakerec,





                    ):


                        with patch(




                            "app.api.routes.chatbot.TransformContext",





                            MagicMock(side_effect=lambda **_: MagicMock()),





                        ):


                            with patch(




                                "app.api.routes.chatbot.SinkOrchestrator",





                                return_value=orch_ok,





                            ):


                                await upload_chat_attachments(rr_ok, gp_ok, AsyncMock())














    pym.parse_document.assert_awaited()


    orch_ok.index.assert_awaited()



















    orch2 = AsyncMock()



    orch2.apply = AsyncMock()



    rr_bad = MagicMock()



    rr_bad.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}



    rr_bad.app.container.logger.return_value = rr_ok.app.container.logger.return_value



    rr_bad.json = rr_ok.json






    gp_bad = AsyncMock()





    gp_bad.batch_upsert_nodes = AsyncMock()





    gp_bad.batch_create_edges = AsyncMock()

















    with patch("app.api.routes.chatbot.BlobStorage") as bscl:








        bins = AsyncMock()


        bins.save_binary_to_storage = AsyncMock(return_value=("id", None))




        bscl.return_value = bins







        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):



            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):




                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=True):




                    with patch(

                        "app.api.routes.chatbot._pdf_page_count",

                        return_value=OCR_IMAGE_PAGE_CAP + 1,

                    ):


                        with pytest.raises(HTTPException) as exc:





                            await upload_chat_attachments(rr_bad, gp_bad, AsyncMock())





                        assert "Scanned attachment page cap" in str(exc.value.detail)





@pytest.mark.asyncio


async def test_grant_and_revoke_permissions_json_noise():






    """1408-1466"""


    import app.api.routes.chatbot as cr




    from app.api.routes.chatbot import grant_attachment_permissions, revoke_attachment_permissions






    req_g_json = MagicMock()


    req_g_json.json = AsyncMock(side_effect=RuntimeError("boom"))






    with pytest.raises(HTTPException) as exc:


        await grant_attachment_permissions(req_g_json, AsyncMock())






    assert exc.value.status_code == 400

















    rk = MagicMock()


    rk.json = AsyncMock(return_value={})








    with pytest.raises(HTTPException) as exc:


        await grant_attachment_permissions(rk, AsyncMock())





    assert exc.value.status_code == 400








    gp_miss = AsyncMock()


    gp_miss.get_user_by_user_id = AsyncMock(return_value={"n": True})


    rk2 = MagicMock()


    rk2.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["rid"]})








    with patch.object(cr.logger, "warning", MagicMock()):





        grant_out = await grant_attachment_permissions(rk2, gp_miss)







    assert grant_out["granted"] == 0











    rq = MagicMock()




    rq.json = AsyncMock(side_effect=RuntimeError("boom"))















    with pytest.raises(HTTPException) as exc:


        await revoke_attachment_permissions(rq, AsyncMock())


    assert exc.value.status_code == 400












@pytest.mark.asyncio


async def test_delete_chat_attachment_early_and_full():





    """1534 and 1540-1545."""





    from app.api.routes.chatbot import delete_chat_attachment








    rr = MagicMock()




    rr.state.user = {"orgId": "o1"}




    gp = AsyncMock()




    gp.get_document = AsyncMock(return_value=None)




    await delete_chat_attachment("rid", rr, gp)
















    gp.get_document.return_value = {"orgId": "o1"}




    gp.delete_nodes_and_edges = AsyncMock()





    gp.delete_nodes = AsyncMock()








    await delete_chat_attachment("rid", rr, gp)






    gp.delete_nodes_and_edges.assert_awaited()

    gp.delete_nodes.assert_awaited()



@pytest.mark.asyncio


async def test_upload_no_attachments_in_body():






    """1195."""








    import base64


    from app.api.routes.chatbot import upload_chat_attachments









    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}




    rr.app.container.logger.return_value = MagicMock()




    rr.json = AsyncMock(return_value={"attachments": []})






    gp = AsyncMock()








    with pytest.raises(HTTPException) as ex:


        await upload_chat_attachments(rr, gp, AsyncMock())


    assert ex.value.status_code == 400











@pytest.mark.asyncio


async def test_upload_pdf_ocr_success_small_doc():






    """1297-1298."""








    import base64









    from app.models.blocks import BlocksContainer









    from app.api.routes.chatbot import upload_chat_attachments









    pym = MagicMock()


    orch = AsyncMock()


    orch.apply = AsyncMock()


    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr.app.container.logger.return_value = MagicMock()


    pdf_raw = base64.b64encode(b"%PDF-xx").decode()


    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "s.pdf",

                                                        "mimeType": "application/pdf",

                                                        "size": 16,

                                                        "contentBase64": pdf_raw}]})


    gp = AsyncMock()


    gp.batch_upsert_nodes = AsyncMock()


    gp.batch_create_edges = AsyncMock()


    fakerec = MagicMock()


    fakerec.block_containers = BlocksContainer(blocks=[], block_groups=[])






    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bs = AsyncMock()


        bs.save_binary_to_storage = AsyncMock(return_value=("id", None))


        bscl.return_value = bs


        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):


                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=True):


                    with patch("app.api.routes.chatbot._pdf_page_count", return_value=5):


                        with patch(


                            "app.api.routes.chatbot._build_pdf_image_blocks",






                            return_value=BlocksContainer(blocks=[], block_groups=[]),





                        ):


                            with patch("app.api.routes.chatbot.convert_record_dict_to_record",

                                      return_value=fakerec):


                                with patch("app.api.routes.chatbot.TransformContext",


                                           MagicMock(side_effect=lambda **_: MagicMock())):


                                    with patch("app.api.routes.chatbot.SinkOrchestrator",


                                               return_value=orch):


                                        await upload_chat_attachments(rr, gp, AsyncMock())


    orch.index.assert_awaited()





@pytest.mark.asyncio


async def test_upload_pdf_parse_generic_failure():






    """1305-1306."""








    import base64










    from app.api.routes.chatbot import upload_chat_attachments

















    pym = MagicMock()


    pym.parse_document = AsyncMock(side_effect=RuntimeError("parse"))




    rr = MagicMock()


    rr.state.user = {"orgId": "o", "userId": "u", "isServiceAccount": True}


    rr.app.container.logger.return_value = MagicMock()


    pdf_raw = base64.b64encode(b"x" * 8).decode()


    rr.json = AsyncMock(return_value={"attachments": [{"fileName": "p.pdf",

                                                        "mimeType": "application/pdf",

                                                        "size": 16,

                                                        "contentBase64": pdf_raw}]})


    gp = AsyncMock()


    gp.batch_upsert_nodes = AsyncMock()


    gp.batch_create_edges = AsyncMock()








    with patch("app.api.routes.chatbot.BlobStorage") as bscl:


        bs = AsyncMock()


        bs.save_binary_to_storage = AsyncMock(return_value=("id", None))


        bscl.return_value = bs


        with patch("app.api.routes.chatbot.GraphDBTransformer", return_value=MagicMock()):


            with patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor", return_value=pym):


                with patch("app.api.routes.chatbot._pdf_has_any_ocr_page", return_value=False):


                    with pytest.raises(HTTPException) as ex:


                        await upload_chat_attachments(rr, gp, AsyncMock())


                    assert ex.value.status_code == 400












@pytest.mark.asyncio


async def test_revoke_invalid_payload_and_bad_user():





    import app.api.routes.chatbot as cr


    from app.api.routes.chatbot import revoke_attachment_permissions









    rk = MagicMock()


    rk.json = AsyncMock(return_value={})








    with pytest.raises(HTTPException) as ex:


        await revoke_attachment_permissions(rk, AsyncMock())





    assert ex.value.status_code == 400











    rp = AsyncMock()




    rp.get_user_by_user_id = AsyncMock(return_value={"n": True})


    rk2 = MagicMock()


    rk2.json = AsyncMock(return_value={"userIds": ["u"], "recordIds": ["x"]})








    with patch.object(cr.logger, "warning", MagicMock()):


        out = await revoke_attachment_permissions(rk2, rp)








    assert out["revoked"] == 0

