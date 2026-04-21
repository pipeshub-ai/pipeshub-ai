"""Tests for _append_task_markers logic from app.utils.streaming.

The function is a pure string transformer. We copy its logic here to avoid
importing the full streaming module, which pulls in heavy LLM SDK deps
that may not be present in the test environment.
"""

import pytest


def _append_task_markers(answer: str, conversation_tasks: list | None) -> str:
    """Mirror of app.utils.streaming._append_task_markers for isolated testing.

    Keep this in sync with the production implementation.
    """
    if not conversation_tasks:
        return answer

    parts: list[str] = []
    for t in conversation_tasks:
        task_type = t.get("type", "")

        if task_type == "artifacts":
            for art in t.get("artifacts", []):
                url = art.get("signedUrl") or art.get("downloadUrl", "")
                if not url:
                    continue
                fname = art.get("fileName", "Download")
                mime = art.get("mimeType", "application/octet-stream")
                doc_id = art.get("documentId", "")
                record_id = art.get("recordId", "")
                parts.append(f"::artifact[{fname}]({url}){{{mime}|{doc_id}|{record_id}}}")
        else:
            url = t.get("signedUrl") or t.get("downloadUrl", "")
            if url:
                fname = t.get("fileName", "Download")
                parts.append(f"::download_conversation_task[{fname}]({url})")

    if not parts:
        return answer

    return answer + "\n\n" + "  ".join(parts)


class TestAppendTaskMarkersLegacy:
    """Tests for the pre-existing download_conversation_task marker logic."""

    def test_none_tasks(self):
        assert _append_task_markers("answer", None) == "answer"

    def test_empty_tasks(self):
        assert _append_task_markers("answer", []) == "answer"

    def test_single_download_task(self):
        tasks = [{"fileName": "data.csv", "signedUrl": "https://s3/data.csv"}]
        result = _append_task_markers("answer", tasks)
        assert "::download_conversation_task[data.csv](https://s3/data.csv)" in result
        assert result.startswith("answer")

    def test_download_task_with_downloadUrl(self):
        tasks = [{"fileName": "data.csv", "downloadUrl": "https://local/data.csv"}]
        result = _append_task_markers("answer", tasks)
        assert "::download_conversation_task[data.csv](https://local/data.csv)" in result

    def test_prefers_signedUrl(self):
        tasks = [{
            "fileName": "data.csv",
            "signedUrl": "https://s3/signed",
            "downloadUrl": "https://local/download",
        }]
        result = _append_task_markers("answer", tasks)
        assert "https://s3/signed" in result
        assert "https://local/download" not in result

    def test_skips_task_without_url(self):
        tasks = [{"fileName": "data.csv"}]
        result = _append_task_markers("answer", tasks)
        assert result == "answer"


class TestAppendTaskMarkersArtifacts:
    """Tests for the new artifact marker logic."""

    def test_single_artifact(self):
        tasks = [{
            "type": "artifacts",
            "artifacts": [{
                "fileName": "chart.png",
                "signedUrl": "https://s3/chart.png",
                "mimeType": "image/png",
                "documentId": "doc123",
                "recordId": "rec456",
            }],
        }]
        result = _append_task_markers("answer", tasks)
        assert "::artifact[chart.png](https://s3/chart.png){image/png|doc123|rec456}" in result

    def test_multiple_artifacts(self):
        tasks = [{
            "type": "artifacts",
            "artifacts": [
                {
                    "fileName": "chart.png",
                    "signedUrl": "https://s3/chart.png",
                    "mimeType": "image/png",
                    "documentId": "doc1",
                },
                {
                    "fileName": "data.csv",
                    "downloadUrl": "https://local/data.csv",
                    "mimeType": "text/csv",
                    "documentId": "doc2",
                },
            ],
        }]
        result = _append_task_markers("answer", tasks)
        assert "::artifact[chart.png]" in result
        assert "::artifact[data.csv]" in result

    def test_artifact_uses_downloadUrl_fallback(self):
        tasks = [{
            "type": "artifacts",
            "artifacts": [{
                "fileName": "report.pdf",
                "downloadUrl": "https://local/report.pdf",
                "mimeType": "application/pdf",
                "documentId": "",
            }],
        }]
        result = _append_task_markers("answer", tasks)
        assert "https://local/report.pdf" in result

    def test_artifact_without_url_skipped(self):
        tasks = [{
            "type": "artifacts",
            "artifacts": [{"fileName": "test.txt", "mimeType": "text/plain"}],
        }]
        result = _append_task_markers("answer", tasks)
        assert result == "answer"

    def test_empty_artifacts_array(self):
        tasks = [{"type": "artifacts", "artifacts": []}]
        result = _append_task_markers("answer", tasks)
        assert result == "answer"


class TestAppendTaskMarkersMixed:
    """Tests for mixed legacy download + artifact markers."""

    def test_mixed_tasks(self):
        tasks = [
            {"fileName": "query.csv", "signedUrl": "https://s3/query.csv"},
            {
                "type": "artifacts",
                "artifacts": [{
                    "fileName": "chart.png",
                    "signedUrl": "https://s3/chart.png",
                    "mimeType": "image/png",
                    "documentId": "doc123",
                    "recordId": "rec789",
                }],
            },
        ]
        result = _append_task_markers("answer", tasks)
        assert "::download_conversation_task[query.csv]" in result
        assert "::artifact[chart.png]" in result

    def test_default_values(self):
        tasks = [{
            "type": "artifacts",
            "artifacts": [{
                "signedUrl": "https://s3/file",
            }],
        }]
        result = _append_task_markers("answer", tasks)
        assert "::artifact[Download](https://s3/file){application/octet-stream||}" in result

    def test_separator_is_double_newline(self):
        tasks = [{"fileName": "f.csv", "signedUrl": "https://url"}]
        result = _append_task_markers("answer text", tasks)
        assert "\n\n" in result
        assert result.startswith("answer text\n\n")
