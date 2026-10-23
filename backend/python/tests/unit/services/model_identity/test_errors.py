import json
from typing import Never

import httpx
import pytest

from app.agents.agent_loop.error_classification import classify_error
from app.services.model_identity import metrics
from app.services.model_identity.errors import (
    BUDGET_EXCEEDED_CODE,
    MODEL_ACCESS_DENIED_CODE,
    GatewayPolicyError,
    GatewayPolicyKind,
    classify_for_user,
    classify_gateway_error,
    policy_error_from_exception,
    record_refusal,
    report_background_refusal,
)
from app.services.model_identity.workload import Workload

BUDGET_BODY = {
    "error": {
        "message": "Budget has been exceeded! Current cost: 1.2, Max budget: 1.0",
        "type": "budget_exceeded",
        "param": None,
        "code": "400",
    }
}
CUSTOMER_ACCESS_BODY = {
    "error": {
        "message": "customer not allowed to access model. Tried to access gpt-5",
        "type": "customer_model_access_denied",
        "code": "401",
    }
}
TEAM_ACCESS_BODY = {
    "error": {"message": "team not allowed to access model", "type": "team_model_access_denied", "code": "401"}
}
RATE_BODY = {"error": {"message": "Max parallel request limit reached", "type": "None", "code": "429"}}


class TestUntrustedBodies:
    @pytest.mark.parametrize(
        "body",
        ["{[1]: 2}", "{{}}", "{" * 5000 + "}" * 5000, "{'a': " * 600 + "1" + "}" * 600, "{not: valid"],
    )
    def test_a_body_the_parser_chokes_on_is_not_a_policy_refusal_and_does_not_raise(self, body) -> None:
        assert classify_gateway_error(400, body, {}) is None


class TestClassifyGatewayError:
    @pytest.mark.parametrize("status", [400, 401, 403, 429])
    def test_a_budget_refusal_is_a_budget_whatever_the_status(self, status) -> None:
        err = classify_gateway_error(status, BUDGET_BODY)
        assert err.kind is GatewayPolicyKind.BUDGET
        assert err.retryable is False
        assert err.error_code == BUDGET_EXCEEDED_CODE

    @pytest.mark.parametrize("body", [CUSTOMER_ACCESS_BODY, TEAM_ACCESS_BODY])
    def test_model_access_refusals(self, body) -> None:
        err = classify_gateway_error(401, body)
        assert err.kind is GatewayPolicyKind.MODEL_ACCESS
        assert err.provider_code in ("401",)
        assert err.error_code == MODEL_ACCESS_DENIED_CODE

    def test_a_429_with_retry_after_is_a_rate_limit_that_names_its_wait(self) -> None:
        err = classify_gateway_error(429, RATE_BODY, {"Retry-After": "7"}, now=1000.0)
        assert err.kind is GatewayPolicyKind.RATE_LIMIT
        assert err.retryable is True
        assert (err.retry_after, err.reset_at) == (7.0, 1007.0)

    def test_retry_after_as_an_http_date_and_a_cap(self) -> None:
        err = classify_gateway_error(429, RATE_BODY, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, now=0.0)
        assert err.retry_after == 3600.0
        assert classify_gateway_error(429, RATE_BODY, {"retry-after": "999999"}).retry_after == 3600.0

    @pytest.mark.parametrize("value", ["soon", "-5", "nan", ""])
    def test_unusable_retry_after_is_ignored(self, value) -> None:
        assert classify_gateway_error(429, RATE_BODY, {"retry-after": value}).retry_after is None

    @pytest.mark.parametrize(
        "status, body",
        [
            (400, {"error": {"message": "invalid reasoning_effort", "type": "invalid_request_error"}}),
            (401, {"error": {"message": "Invalid API key"}}),
            (500, {"error": {"message": "boom"}}),
            (403, "plain text"),
            (400, None),
            (400, b"\xff\xfe"),
            (400, ["not", "a", "dict"]),
        ],
    )
    def test_other_errors_are_not_policy(self, status, body) -> None:
        assert classify_gateway_error(status, body) is None

    def test_accepts_json_text_python_repr_and_bytes(self) -> None:
        assert classify_gateway_error(400, json.dumps(BUDGET_BODY)).kind is GatewayPolicyKind.BUDGET
        assert classify_gateway_error(400, repr(BUDGET_BODY)).kind is GatewayPolicyKind.BUDGET
        assert classify_gateway_error(400, json.dumps(BUDGET_BODY).encode()).kind is GatewayPolicyKind.BUDGET

    def test_error_as_a_bare_string(self) -> None:
        err = classify_gateway_error(400, {"error": "Budget has been exceeded!"})
        assert err.kind is GatewayPolicyKind.BUDGET


class TestUserMessages:
    def test_budget_message_says_when_it_resets(self) -> None:
        err = GatewayPolicyError(GatewayPolicyKind.BUDGET, 400, reset_at=1000.0 + 600)
        assert "about 10 minutes" in err.user_message(now=1000.0)
        assert "resets automatically" in GatewayPolicyError(GatewayPolicyKind.BUDGET).user_message()

    @pytest.mark.parametrize(
        "remaining, phrase",
        [(-5, "shortly"), (30, "about a minute"), (7200, "about 2 hours"), (3 * 86400, "about 3 days")],
    )
    def test_wait_phrasing(self, remaining, phrase) -> None:
        err = GatewayPolicyError(GatewayPolicyKind.BUDGET, reset_at=remaining)
        assert phrase in err.user_message(now=0.0)

    def test_model_access_message_does_not_blame_the_api_key(self) -> None:
        text = GatewayPolicyError(GatewayPolicyKind.MODEL_ACCESS).user_message()
        assert "access" in text and "key" not in text.lower()


class TestFromExceptions:
    def test_sdk_exception_with_body_and_headers(self) -> None:
        class APIStatusError(Exception):
            status_code = 429
            body = RATE_BODY
            response = httpx.Response(429, headers={"retry-after": "3"})

        err = policy_error_from_exception(APIStatusError("x"))
        assert err.kind is GatewayPolicyKind.RATE_LIMIT and err.retry_after == 3.0

    def test_flattened_message_with_sdk_suffix(self) -> None:
        exc = RuntimeError(
            f"LLM call failed: Error code: 400 - {BUDGET_BODY!r}\nFor troubleshooting, visit: https://x/y"
        )
        assert policy_error_from_exception(exc).kind is GatewayPolicyKind.BUDGET

    def test_walks_the_cause_chain(self) -> None:
        inner = RuntimeError(f"Error code: 401 - {CUSTOMER_ACCESS_BODY!r}")
        try:
            try:
                raise inner
            except RuntimeError as e:
                raise ValueError("wrapped") from e
        except ValueError as outer:
            assert policy_error_from_exception(outer).kind is GatewayPolicyKind.MODEL_ACCESS

    def test_its_own_exception_passes_through_and_can_be_raised(self) -> None:
        err = GatewayPolicyError(GatewayPolicyKind.BUDGET, 400)
        with pytest.raises(GatewayPolicyError):
            try:
                raise err
            except GatewayPolicyError as caught:
                assert policy_error_from_exception(caught) is err
                raise

    def test_unrelated_errors_and_cycles_are_none(self) -> None:
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a
        assert policy_error_from_exception(a) is None
        assert policy_error_from_exception(ConnectionError("down")) is None


class TestUserFacingClassification:
    def test_budget_and_access_get_their_own_codes_not_rate_limit_or_auth(self) -> None:
        code, message = classify_error(f"Error code: 429 - {BUDGET_BODY!r}")
        assert code == "budget_exceeded" and "usage limit" in message
        code, message = classify_error(f"Error code: 403 - {CUSTOMER_ACCESS_BODY!r}")
        assert code == "model_access_denied" and "API key" not in message

    def test_a_plain_rate_limit_keeps_its_existing_handling(self) -> None:
        assert classify_error(f"Error code: 429 - {RATE_BODY!r}")[0] == "rate_limit"
        assert classify_for_user(f"Error code: 429 - {RATE_BODY!r}") is None

    def test_a_real_auth_error_is_still_an_auth_error(self) -> None:
        assert classify_error("Error code: 401 - {'error': {'message': 'Invalid API key'}}")[0] == "auth_error"

    def test_status_codes_for_the_stream(self) -> None:
        from app.agents.agent_loop.protocol.stream_collector import (
            _ERROR_CODE_STATUS as codes,
        )

        assert codes["budget_exceeded"] == 429 and codes["model_access_denied"] == 403


class TestHookStopsSdkRetries:
    """The hook marks refusals so the SDKs do not retry what cannot change."""

    def _client(self, status, body, headers=None, *, calls) -> httpx.Client:
        from app.services.model_identity.forwarders.litellm_headers import (
            LiteLLMHeadersForwarder,
        )
        from app.services.model_identity.settings import ConnectionForwardingSettings
        from app.services.model_identity.transport.httpx_hook import (
            install_identity_hook,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(status, json=body, headers=headers or {})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        install_identity_hook(
            client,
            ConnectionForwardingSettings.parse({"enabled": True, "mode": "headers"}),
            LiteLLMHeadersForwarder(),
        )
        return client

    @pytest.mark.parametrize(
        "status, body, kind",
        [(400, BUDGET_BODY, "budget"), (401, CUSTOMER_ACCESS_BODY, "model_access"), (422, BUDGET_BODY, "budget"), (429, BUDGET_BODY, "budget")],
    )
    def test_refusals_are_marked_do_not_retry_and_counted(self, status, body, kind) -> None:
        calls = []
        response = self._client(status, body, calls=calls).get("http://gw/v1/chat")
        assert response.headers["x-should-retry"] == "false"
        assert any(
            labels[0] == kind for labels in metrics.snapshot().get(metrics.GATEWAY_POLICY_REFUSALS, {})
        )

    def test_a_rate_limit_is_left_retryable(self) -> None:
        response = self._client(429, RATE_BODY, {"retry-after": "1"}, calls=[]).get("http://gw/v1/chat")
        assert "x-should-retry" not in response.headers

    def test_success_and_other_errors_are_untouched(self) -> None:
        ok = self._client(200, {"ok": True}, calls=[]).get("http://gw/x")
        assert "x-should-retry" not in ok.headers
        other = self._client(500, {"error": {"message": "boom"}}, calls=[]).get("http://gw/x")
        assert "x-should-retry" not in other.headers

    def test_the_openai_sdk_makes_one_attempt_not_three(self) -> None:
        openai = pytest.importorskip("openai")
        calls = []
        http = self._client(400, BUDGET_BODY, calls=calls)
        client = openai.OpenAI(api_key="k", base_url="http://gw/v1", http_client=http, max_retries=2)
        with pytest.raises(openai.APIStatusError) as raised:
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
        assert len(calls) == 1
        assert policy_error_from_exception(raised.value).kind is GatewayPolicyKind.BUDGET

    def test_the_openai_sdk_retries_a_rate_limit(self) -> None:
        openai = pytest.importorskip("openai")
        calls = []
        http = self._client(429, RATE_BODY, {"retry-after": "0"}, calls=calls)
        client = openai.OpenAI(api_key="k", base_url="http://gw/v1", http_client=http, max_retries=1)
        with pytest.raises(openai.RateLimitError):
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
        assert len(calls) == 2

    async def test_async_clients_are_marked_too(self) -> None:
        from app.services.model_identity.forwarders.litellm_headers import (
            LiteLLMHeadersForwarder,
        )
        from app.services.model_identity.settings import ConnectionForwardingSettings
        from app.services.model_identity.transport.httpx_hook import (
            install_identity_hook,
        )

        async def handler(request) -> httpx.Response:
            return httpx.Response(400, json=BUDGET_BODY)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        install_identity_hook(
            client, ConnectionForwardingSettings.parse({"enabled": True, "mode": "headers"}), LiteLLMHeadersForwarder()
        )
        response = await client.get("http://gw/x")
        assert response.headers["x-should-retry"] == "false"


class TestBackgroundRefusals:
    def test_reports_throttle_and_logs_one_alert_per_interval(self, caplog) -> None:
        import logging

        from app.services.model_identity import errors
        from app.services.resource_governor.feedback import (
            get_default_downstream_feedback,
        )

        errors._last_alert.clear()
        feedback = get_default_downstream_feedback()
        feedback.drain()
        err = GatewayPolicyError(GatewayPolicyKind.BUDGET, 400)
        with caplog.at_level(logging.ERROR, logger=errors.logger.name):
            report_background_refusal(err, now=1000.0)
            report_background_refusal(err, now=1010.0)
            report_background_refusal(err, now=1000.0 + 301)
        assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2
        assert feedback.drain().throttles["llm_gateway"] == 3

    def test_a_rate_limit_throttles_without_an_alert(self, caplog) -> None:
        from app.services.model_identity import errors

        errors._last_alert.clear()
        with caplog.at_level("ERROR", logger=errors.logger.name):
            report_background_refusal(GatewayPolicyError(GatewayPolicyKind.RATE_LIMIT, 429), now=1.0)
        assert not caplog.records

    def test_record_refusal_counts_by_kind_and_class(self) -> None:
        record_refusal(GatewayPolicyError(GatewayPolicyKind.BUDGET), "background")
        assert metrics.counter_value(metrics.GATEWAY_POLICY_REFUSALS, "budget", "background") == 1


class TestThrottledInvoke:
    async def test_a_budget_refusal_is_not_retried(self) -> None:
        from app.utils.streaming import _ainvoke_throttled

        calls = []

        class Llm:
            async def ainvoke(self, messages, **kwargs) -> Never:
                calls.append(1)
                raise RuntimeError(f"Error code: 429 - {BUDGET_BODY!r}")

        with pytest.raises(RuntimeError):
            await _ainvoke_throttled(Llm(), [])
        assert calls == [1]

    async def test_a_budget_refusal_does_not_trip_the_provider_breaker(self) -> None:
        from app.services.llm_gateway.gateway import LLMGateway

        gateway = LLMGateway(2, failure_threshold=1)

        class Llm:
            async def ainvoke(self, messages, **kwargs) -> Never:
                raise RuntimeError(f"Error code: 429 - {BUDGET_BODY!r}")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await gateway.invoke(Llm(), [], provider="litellm")
        assert not gateway.breaker("litellm").is_open

    @pytest.mark.parametrize(
        ("workload", "reported"),
        [(Workload.CHAT, False), (Workload.HEALTH_CHECK, False), (Workload.INDEXING, True), (None, True)],
    )
    async def test_only_background_refusals_feed_the_throttle(self, monkeypatch, alice, workload, reported) -> None:
        from app.services.llm_gateway import gateway as gateway_module
        from app.services.model_identity.call_context import (
            ModelCallContext,
            bind_call_context,
        )

        seen = []
        monkeypatch.setattr(gateway_module, "report_background_refusal", seen.append)

        class Llm:
            async def ainvoke(self, messages, **kwargs) -> Never:
                raise RuntimeError(f"Error code: 429 - {BUDGET_BODY!r}")

        gateway = gateway_module.LLMGateway(2, failure_threshold=5)
        with pytest.raises(RuntimeError):
            if workload is None:
                await gateway.invoke(Llm(), [], provider="litellm")
            else:
                async with bind_call_context(ModelCallContext(principal=alice, workload=workload)):
                    await gateway.invoke(Llm(), [], provider="litellm")
        assert bool(seen) is reported

    async def test_a_gateway_rate_limit_waits_for_retry_after(self, monkeypatch) -> None:
        from app.utils import streaming

        waits = []

        async def fake_sleep(seconds) -> None:
            waits.append(seconds)

        monkeypatch.setattr(streaming.asyncio, "sleep", fake_sleep)
        attempts = []

        class Limited(Exception):
            status_code = 429
            body = RATE_BODY
            response = httpx.Response(429, headers={"retry-after": "9"})

        class Llm:
            async def ainvoke(self, messages, **kwargs) -> str:
                attempts.append(1)
                if len(attempts) == 1:
                    raise Limited("slow down")
                return "ok"

        assert await streaming._ainvoke_throttled(Llm(), []) == "ok"
        assert waits and waits[0] >= 9.0
