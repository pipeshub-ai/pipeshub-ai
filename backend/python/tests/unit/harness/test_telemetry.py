# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""P9 OT-1, OT-10: PipesHub's tracer switch (``HARNESS_OTEL``) and its OTLP export.

OT-10 posts to a local OTLP/HTTP collector; it needs ``opentelemetry-exporter-otlp-proto-http`` (declared in
``pyproject.toml``) and skips where the environment lacks it.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, override

import pytest

from app.harness import telemetry
from harness_core.telemetry import NOOP_TRACER, RunRef
from harness_core.telemetry.ids import run_trace_id
from harness_core.telemetry.otel import OtelTracer


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_tracers", {})
    for name in ("HARNESS_OTEL", "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


def test_ot1_off_by_default_is_the_noop_tracer() -> None:
    assert telemetry.harness_tracer("worker") is NOOP_TRACER


def test_ot1_off_imports_no_opentelemetry() -> None:
    probe = (
        "import sys; from app.harness.telemetry import harness_tracer; harness_tracer('worker');"
        "print(any(m.startswith('opentelemetry') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_an_invalid_flag_value_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_OTEL", "maybe")
    with pytest.raises(ValueError, match="HARNESS_OTEL"):
        telemetry.harness_tracer("worker")


def _exporter_installed() -> bool:
    try:
        return importlib.util.find_spec("opentelemetry.exporter.otlp.proto.http.trace_exporter") is not None
    except ImportError:
        return False


_EXPORTER = _exporter_installed()


@pytest.mark.skipif(_EXPORTER, reason="the OTLP exporter is installed here")
def test_on_without_the_exporter_runs_untraced_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HARNESS_OTEL", "true")
    with caplog.at_level(logging.WARNING):
        assert telemetry.harness_tracer("worker") is NOOP_TRACER
    assert "telemetry.unavailable" in caplog.text


@pytest.mark.skipif(not _EXPORTER, reason="needs opentelemetry-exporter-otlp-proto-http")
def test_ot10_on_exports_otlp_to_the_configured_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # pyright: ignore[reportMissingImports]
        ExportTraceServiceRequest,
    )

    received: list[bytes] = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()

        @override
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HARNESS_OTEL", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    try:
        tracer = telemetry.harness_tracer("worker")
        assert tracer is not NOOP_TRACER and tracer.capture_content is False
        tracer.start("chat gpt-x", kind="client", run=RunRef("org-1", "run-otlp")).end()
        assert isinstance(tracer, OtelTracer) and tracer.flush(10_000)
    finally:
        server.shutdown()
    request = ExportTraceServiceRequest()
    request.ParseFromString(b"".join(received))
    spans = [s for r in request.resource_spans for ss in r.scope_spans for s in ss.spans]
    assert [s.name for s in spans] == ["chat gpt-x"]
    assert spans[0].trace_id.hex() == run_trace_id("run-otlp")
    service = {a.key: a.value.string_value for a in request.resource_spans[0].resource.attributes}
    assert service["service.name"] == "pipeshub-worker"
