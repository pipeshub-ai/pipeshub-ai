"""Tests for the request-id context module (app/utils/request_context.py).

Covers the behaviour the rest of the tracing layer relies on:
  - sanitize_root_id() defends logs against client-controlled ids
  - the ContextVar set/get/reset lifecycle, including the cross-context reset
  - the process-global service suffix
  - header / envelope injection contracts (copy, never mutate, never clobber)
  - context_from_envelope() fallback to a fresh system root
"""

import contextvars

import pytest

from app.utils import request_context as rc


@pytest.fixture(autouse=True)
def _isolate_context():
    """Each test starts with no context and an empty service suffix, and the
    process-global suffix is restored afterwards so tests can't leak into
    each other (it is a module-level global, not a ContextVar)."""
    token = rc._ctx.set(None)
    saved_suffix = rc.get_service_suffix()
    rc.set_service_suffix("")
    try:
        yield
    finally:
        rc._ctx.reset(token)
        rc.set_service_suffix(saved_suffix)


class TestSanitizeRootId:
    def test_none_and_empty_return_none(self):
        assert rc.sanitize_root_id(None) is None
        assert rc.sanitize_root_id("") is None

    def test_allowed_charset_is_preserved(self):
        raw = "User_42.svc:abc-DEF"
        assert rc.sanitize_root_id(raw) == raw

    def test_strips_crlf_to_prevent_log_forging(self):
        # The whole point: a client cannot inject a fake log line.
        assert rc.sanitize_root_id("abc\r\nFAKE LOG LINE") == "abcFAKELOGLINE"

    def test_strips_other_unsafe_chars(self):
        assert rc.sanitize_root_id("a b/c@d#e") == "abcde"

    def test_all_unsafe_collapses_to_none(self):
        assert rc.sanitize_root_id("@@@ ///") is None

    def test_truncates_to_max_length(self):
        raw = "a" * (rc._MAX_ROOT_ID_LEN + 50)
        assert rc.sanitize_root_id(raw) == "a" * rc._MAX_ROOT_ID_LEN

    def test_full_frontend_id_survives_uncut(self):
        # 24-char ObjectId + '-' + 21-char nanoid = 46 chars, under the 64 cap.
        frontend_id = "6a3992e0a771842adbf1039f-ZgUvzvsipDj0C_kjKwhMj"
        assert len(frontend_id) == 46
        assert rc.sanitize_root_id(frontend_id) == frontend_id


class TestServiceSuffix:
    def test_set_and_get(self):
        rc.set_service_suffix("-idx")
        assert rc.get_service_suffix() == "-idx"

    def test_none_normalises_to_empty_string(self):
        rc.set_service_suffix(None)  # type: ignore[arg-type]
        assert rc.get_service_suffix() == ""


class TestContextLifecycle:
    def test_get_returns_none_without_context(self):
        assert rc.get_context() is None

    def test_set_then_get(self):
        rc.set_context("root-123")
        ctx = rc.get_context()
        assert ctx is not None
        assert ctx.root_id == "root-123"

    def test_reset_restores_previous_context(self):
        rc.set_context("outer")
        token = rc.set_context("inner")
        inner = rc.get_context()
        assert inner is not None and inner.root_id == "inner"
        rc.reset_context(token)
        outer = rc.get_context()
        assert outer is not None and outer.root_id == "outer"

    def test_reset_with_none_token_is_noop(self):
        rc.set_context("root")
        rc.reset_context(None)
        ctx = rc.get_context()
        assert ctx is not None and ctx.root_id == "root"

    def test_reset_with_token_from_other_context_does_not_raise(self):
        """A token minted in a different context (e.g. across a thread/task
        boundary) cannot be reset normally; reset_context must swallow that and
        clear the var instead of propagating ValueError/LookupError."""
        captured = {}

        def _inner():
            captured["token"] = rc.set_context("from-other-context")

        contextvars.copy_context().run(_inner)

        # Token belongs to the copied context, not this one.
        rc.reset_context(captured["token"])
        assert rc.get_context() is None


class TestRootIdGenerators:
    def test_new_system_root_is_prefixed_and_unique(self):
        a, b = rc.new_system_root(), rc.new_system_root()
        assert a != b
        assert a.startswith("sys-")
        int(a[len("sys-"):], 16)  # body is valid hex

    def test_new_anon_root_is_prefixed_and_unique(self):
        a, b = rc.new_anon_root(), rc.new_anon_root()
        assert a != b
        assert a.startswith("anon-")


class TestCurrentDisplayId:
    def test_no_context_returns_placeholder(self):
        assert rc.current_display_id() == rc.NO_CONTEXT

    def test_context_without_suffix(self):
        rc.set_context("root-1")
        assert rc.current_display_id() == "root-1"

    def test_context_with_suffix(self):
        rc.set_service_suffix("-query")
        rc.set_context("root-1")
        assert rc.current_display_id() == "root-1-query"


class TestInjectRequestHeaders:
    def test_no_context_returns_copy_unchanged(self):
        original = {"content-type": "application/json"}
        out = rc.inject_request_headers(original)
        assert out == original
        assert out is not original  # never mutate caller's dict

    def test_none_input_returns_empty_dict(self):
        assert rc.inject_request_headers(None) == {}

    def test_adds_request_id_when_context_present(self):
        rc.set_context("root-9")
        out = rc.inject_request_headers({})
        assert out[rc.HEADER_REQUEST_ID] == "root-9"

    def test_does_not_overwrite_existing_header(self):
        rc.set_context("root-9")
        out = rc.inject_request_headers({rc.HEADER_REQUEST_ID: "preexisting"})
        assert out[rc.HEADER_REQUEST_ID] == "preexisting"


class TestInjectEnvelope:
    def test_no_context_returns_same_message(self):
        msg = {"recordId": "r1"}
        out = rc.inject_envelope(msg)
        assert out is msg  # documented: returns input unchanged when no context

    def test_adds_request_id_and_does_not_mutate_input(self):
        rc.set_context("root-7")
        msg = {"recordId": "r1"}
        out = rc.inject_envelope(msg)
        assert out[rc.ENVELOPE_REQUEST_ID] == "root-7"
        assert out is not msg
        assert rc.ENVELOPE_REQUEST_ID not in msg

    def test_does_not_overwrite_existing_request_id(self):
        rc.set_context("root-7")
        out = rc.inject_envelope({rc.ENVELOPE_REQUEST_ID: "preexisting"})
        assert out[rc.ENVELOPE_REQUEST_ID] == "preexisting"


class TestContextFromEnvelope:
    def test_reads_and_sanitizes_request_id(self):
        ctx = rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: "abc\r\ndef"})
        assert ctx.root_id == "abcdef"

    def test_missing_request_id_falls_back_to_anon_root(self):
        ctx = rc.context_from_envelope({})
        assert ctx.root_id.startswith("anon-")  # propagation gap, not system work

    def test_unsafe_only_request_id_falls_back_to_anon_root(self):
        ctx = rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: "@@@ ///"})
        assert ctx.root_id.startswith("anon-")

    def test_non_string_ids_are_ignored(self) -> None:
        ctx = rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: 7, rc.ENVELOPE_ORG_ID: {"a": 1}})
        assert ctx.root_id.startswith("anon-")
        assert ctx.org_id is None


class TestRunInContext:
    @pytest.mark.asyncio
    async def test_binds_for_the_awaitable_then_restores(self) -> None:
        rc.set_context("outer")

        async def read() -> "rc.RequestContext | None":
            return rc.get_context()

        seen = await rc.run_in_context(rc.RequestContext(root_id="inner", org_id="org-1"), read())
        assert seen == rc.RequestContext(root_id="inner", org_id="org-1")
        assert rc.get_context() == rc.RequestContext(root_id="outer")


class TestAdmittedInPropagation:
    def test_an_admission_stamp_rides_on_outbound_headers(self) -> None:
        token = rc.set_admitted_in("dom-1")
        try:
            assert rc.inject_request_headers({})[rc.HEADER_ADMITTED_IN] == "dom-1"
        finally:
            rc.reset_admitted_in(token)
        assert rc.HEADER_ADMITTED_IN not in rc.inject_request_headers({})


class TestOrgOnContext:
    def test_set_context_org_attaches_to_current_context(self) -> None:
        rc.set_context("root-1")
        rc.set_context_org("org-1")
        assert rc.get_org_id() == "org-1"
        assert rc.get_context().org_id == "org-1"

    def test_set_context_org_without_context_is_noop(self) -> None:
        rc.set_context_org("org-1")
        assert rc.get_context() is None
        assert rc.get_org_id() is None

    def test_empty_org_is_ignored(self) -> None:
        rc.set_context("root-1")
        rc.set_context_org("")
        rc.set_context_org(None)
        assert rc.get_org_id() is None

    def test_org_is_sanitized(self) -> None:
        rc.set_context("root-1")
        rc.set_context_org("org\r\n-1")
        assert rc.get_org_id() == "org-1"

    def test_new_context_starts_without_org(self) -> None:
        rc.set_context("root-1")
        rc.set_context_org("org-1")
        rc.set_context("root-2")
        assert rc.get_org_id() is None


class TestOrgOnEnvelope:
    def test_inject_envelope_adds_org(self) -> None:
        rc.set_context("root-7")
        rc.set_context_org("org-7")
        out = rc.inject_envelope({"recordId": "r1"})
        assert out[rc.ENVELOPE_ORG_ID] == "org-7"

    def test_inject_envelope_without_org_leaves_key_out(self) -> None:
        rc.set_context("root-7")
        out = rc.inject_envelope({"recordId": "r1"})
        assert rc.ENVELOPE_ORG_ID not in out

    def test_inject_envelope_keeps_existing_org(self) -> None:
        rc.set_context("root-7")
        rc.set_context_org("org-7")
        out = rc.inject_envelope({rc.ENVELOPE_ORG_ID: "org-producer"})
        assert out[rc.ENVELOPE_ORG_ID] == "org-producer"

    def test_context_from_envelope_reads_org(self) -> None:
        ctx = rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: "root-1", rc.ENVELOPE_ORG_ID: "org-1"})
        assert (ctx.root_id, ctx.org_id) == ("root-1", "org-1")

    def test_context_from_envelope_without_org(self) -> None:
        assert rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: "root-1"}).org_id is None

    def test_bind_context_keeps_org(self) -> None:
        """set_context(ctx.root_id) would drop the org; consumers must bind the whole context."""
        token = rc.bind_context(rc.context_from_envelope({rc.ENVELOPE_REQUEST_ID: "root-1", rc.ENVELOPE_ORG_ID: "org-1"}))
        try:
            assert rc.get_org_id() == "org-1"
            assert rc.get_context().root_id == "root-1"
        finally:
            rc.reset_context(token)
        assert rc.get_context() is None
