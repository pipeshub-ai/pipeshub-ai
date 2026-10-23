"""`rewrite_request_refs`: what a request-scoped citation ref becomes once the
text carrying it outlives the request (CE-15)."""

from __future__ import annotations

import re

from app.utils.citations import rewrite_request_refs

_REF_TOKEN = re.compile(r"ref\d+")


class TestRewriteRequestRefs:
    def test_expands_every_form_a_tool_writes(self) -> None:
        refs = {"ref1": "https://a.example/x", "ref2": "https://app/record/R/preview#blockIndex=2"}
        text = "[ref1] a\n[3|ref2] b\nurl/Citation ID: https://ref1.xyz\n* Citation ID for summary: ref2"

        out, expanded, unresolved = rewrite_request_refs(text, refs)

        assert out == (
            "[https://a.example/x] a\n[3|https://app/record/R/preview#blockIndex=2] b\n"
            "url/Citation ID: https://a.example/x\n"
            "* Citation ID for summary: https://app/record/R/preview#blockIndex=2"
        )
        assert (expanded, unresolved) == (4, 0)

    def test_leaves_words_paths_and_ids_that_only_contain_ref_alone(self) -> None:
        text = "xref12 https://git.example/ref12/x ref-12 refs refund ref12abc id=ref12"

        assert rewrite_request_refs(text, {"ref12": "https://wrong.example"}) == (text, 0, 0)

    def test_a_ref_no_mapping_knows_is_made_uncitable(self) -> None:
        out, expanded, unresolved = rewrite_request_refs("see [ref7] and https://ref8.xyz", None)

        assert _REF_TOKEN.search(out) is None
        assert (expanded, unresolved) == (0, 2)

    def test_a_ref_cut_by_a_preview_cap_is_never_read_as_a_shorter_ref(self) -> None:
        refs = {"ref1": "https://wrong.example/1"}
        text = "a [ref1… [truncated: 9000 chars total]\nb https://ref1.x… [truncated, 1200 of 4000 chars]"

        out, expanded, unresolved = rewrite_request_refs(text, refs)

        assert "wrong.example" not in out
        assert _REF_TOKEN.search(out) is None
        assert (expanded, unresolved) == (0, 2)
