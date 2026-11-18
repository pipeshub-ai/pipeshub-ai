"""Tests for integration-tests/sdk_tests/check_operation_surface.py on small synthetic SDK trees."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "integration-tests/sdk_tests/check_operation_surface.py"
_spec = importlib.util.spec_from_file_location("check_operation_surface", _SCRIPT)
surface = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(surface)

OP = "streamRecordBuffer"

TS_FUNC = """\
/** Stream record{doc} */
export function knowledgeBaseStreamRecordBuffer() {{
  const [result] = await M.match<ReadableStream<Uint8Array>>(
    M.stream(
      200,
      z.custom<ReadableStream<Uint8Array>>(x => x instanceof ReadableStream),
      {{ ctype: "*/*" }},
    ),{extra}
    M.jsonErr([404, 409], errors.E$inboundSchema),
    M.fail("4XX"),
  )(response);
  // operationID: other
  const context = {{ operationID: "streamRecordBuffer"{hdrs} }};
}}
"""
TS_MODEL = "export type StreamRecordBufferRequest = { recordId: string };\n"
TS_SDK = """\
class KnowledgeBase {
  /** Stream{doc} */
  async streamRecordBuffer(request: R): Promise<ReadableStream<Uint8Array>> {
    return unwrapAsync(f(this, request));
  }

  async other() {}
}
"""
PY_SDK = '''\
class KnowledgeBase:
    def stream_record_buffer(self, *, record_id: str):
        """Stream the record{doc}."""
        # comment {doc}
        return self.do(record_id)

    def other(self):
        return 1
'''
PY_MODEL = "class StreamRecordBufferRequest:\n    record_id: str\n"
GO_SDK = """\
package pipeshub

// StreamRecordBuffer{doc}
func (s *KnowledgeBase) StreamRecordBuffer(ctx context.Context, id string) (*R, error) {{
	return s.do(ctx, id)
}}

func (s *KnowledgeBase) Other() {{}}
"""
GO_MODEL = "package operations\n\ntype StreamRecordBufferRequest struct{ RecordID string }\n"


def build(root: Path, *, doc="", extra="", hdrs="", skip: str | None = None, py_extra="") -> None:
    files = {
        "pipeshub-sdk-typescript/src/funcs/knowledge-base-stream-record-buffer.ts": TS_FUNC.format(doc=doc, extra=extra, hdrs=hdrs),
        "pipeshub-sdk-typescript/src/models/operations/stream-record-buffer.ts": TS_MODEL,
        "pipeshub-sdk-typescript/src/sdk/knowledge-base.ts": TS_SDK.replace("{doc}", doc),
        "pipeshub-sdk-python/src/pipeshub_sdk/knowledge_base_sdk.py": PY_SDK.replace("{doc}", doc) + py_extra,
        "pipeshub-sdk-python/src/pipeshub_sdk/models/streamrecordbufferop.py": PY_MODEL,
        "pipeshub-sdk-go/knowledgebase.go": GO_SDK.replace("{doc}", doc),
        "pipeshub-sdk-go/models/operations/streamrecordbuffer.go": GO_MODEL,
    }
    for rel, text in files.items():
        if skip and rel.startswith(f"pipeshub-sdk-{skip}/"):
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


class SurfaceCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cur, self.base = Path(self._tmp.name, "cur"), Path(self._tmp.name, "base")

    def problems(self):
        return surface.compare(OP, self.cur, self.base)

    def test_identical_generations_pass(self):
        build(self.cur)
        build(self.base)
        self.assertEqual(self.problems(), [])

    def test_a_doc_comment_only_change_passes(self):
        build(self.cur, doc=" and a new sentence about ETag and Cache-Control")
        build(self.base)
        self.assertEqual(self.problems(), [])

    def test_declared_response_headers_fail(self):
        build(self.cur, hdrs=", hdrs: true")
        build(self.base)
        found = self.problems()
        self.assertTrue(any("hdrs: true" in p for p in found), found)
        self.assertTrue(any("changed" in p for p in found), found)

    def test_a_new_status_matcher_fails(self):
        for code in ("304", "206", "416"):
            with self.subTest(code=code):
                build(self.cur, extra=f"\n    M.nil({code}, z.void()),")
                build(self.base)
                found = self.problems()
                self.assertTrue(any(f"status {code}" in p for p in found), found)

    def test_a_status_matcher_inside_a_list_fails(self):
        build(self.cur, extra="\n    M.jsonErr([400, 416], errors.E$inboundSchema),")
        build(self.base)
        self.assertTrue(any("status 416" in p for p in self.problems()))

    def test_a_changed_python_method_fails(self):
        build(self.cur, py_extra="\n    def stream_record_buffer_async(self):\n        return 2\n")
        build(self.base)
        self.assertTrue(any(p.startswith("python:") for p in self.problems()))

    def test_the_stream_losing_its_200_matcher_fails(self):
        build(self.cur)
        build(self.base)
        f = self.cur / "pipeshub-sdk-typescript/src/funcs/knowledge-base-stream-record-buffer.ts"
        f.write_text(f.read_text().replace("M.stream(\n      200,", "M.json(\n      200,"))
        self.assertTrue(any("M.stream(200)" in p for p in self.problems()))

    def test_a_missing_checkout_fails_for_each_language(self):
        for lang in surface.LANGUAGES:
            with self.subTest(lang=lang):
                shutil.rmtree(self.cur, ignore_errors=True)
                build(self.cur, skip=lang)
                build(self.base)
                self.assertTrue(any(f"{lang}: the current checkout" in p for p in self.problems()))

    def test_an_operation_with_no_files_fails(self):
        build(self.cur)
        build(self.base)
        (self.cur / "pipeshub-sdk-go/models/operations/streamrecordbuffer.go").unlink()
        (self.cur / "pipeshub-sdk-go/knowledgebase.go").write_text("package pipeshub\n")
        self.assertTrue(any("go: no generated file" in p for p in self.problems()))

    def test_a_string_holding_comment_marks_is_not_cut(self):
        text = 'a = "*/*"; b = 1 // note\nc = "http://x"\n'
        self.assertEqual(surface.strip_comments(text, "typescript"), 'a = "*/*"; b = 1\nc = "http://x"')


if __name__ == "__main__":
    unittest.main()
