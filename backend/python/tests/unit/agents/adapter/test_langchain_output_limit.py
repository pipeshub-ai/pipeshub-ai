"""P4.17 WF-6: the LangChain arm (the product's default transport) puts the call's output limit where each
integration's provider reads it, keeps a smaller configured cap and a thinking budget inside the limit."""

from __future__ import annotations

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agents.agent_loop.langchain_output_limit import limited, output_limit_field
from app.agents.agent_loop.langchain_transport import LangChainTransport

_INPUT = [HumanMessage(content="write the report")]


def _azure(**kwargs: Any) -> AzureChatOpenAI:  # noqa: ANN401
    return AzureChatOpenAI(
        azure_endpoint="https://example.openai.azure.com",
        azure_deployment="gpt-6-luna",
        api_version="2025-04-01-preview",
        api_key="k",  # pyright: ignore[reportArgumentType]
        **kwargs,
    )


def test_wf6_azure_chat_completions_payload_has_max_completion_tokens() -> None:
    payload = limited(_azure(), 32_768)._get_request_payload(_INPUT)  # pyright: ignore[reportAttributeAccessIssue]
    assert payload["max_completion_tokens"] == 32_768
    assert "max_tokens" not in payload


def test_wf6_responses_payload_has_max_output_tokens() -> None:
    llm = ChatOpenAI(model="gpt-6-luna", api_key="k", use_responses_api=True, reasoning={"effort": "high"})  # pyright: ignore[reportArgumentType]
    payload = limited(llm, 32_768)._get_request_payload(_INPUT)  # pyright: ignore[reportAttributeAccessIssue]
    assert payload["max_output_tokens"] == 32_768
    assert "max_completion_tokens" not in payload


def test_wf6_a_smaller_configured_cap_is_kept_and_no_limit_changes_nothing() -> None:
    llm = _azure(max_tokens=4_000)
    assert limited(llm, 32_768).max_tokens == 4_000  # pyright: ignore[reportAttributeAccessIssue]
    plain = _azure()
    assert limited(plain, None) is plain
    assert "max_completion_tokens" not in plain._get_request_payload(_INPUT)  # pyright: ignore[reportAttributeAccessIssue]


def test_wf6_anthropic_payload_keeps_thinking_inside_the_limit() -> None:
    llm = ChatAnthropic(
        model="claude-sonnet-5", api_key="k", max_tokens=64_000, thinking={"type": "enabled", "budget_tokens": 50_000}  # pyright: ignore[reportCallIssue, reportArgumentType]
    )
    payload = limited(llm, 32_768)._get_request_payload(_INPUT)  # pyright: ignore[reportAttributeAccessIssue]
    assert payload["max_tokens"] == 32_768
    assert payload["thinking"]["budget_tokens"] == 28_672


def test_wf6_bedrock_converse_params_have_max_tokens() -> None:
    llm = ChatBedrockConverse(
        model="anthropic.claude-x", region_name="us-east-1", max_tokens=64_000,
        aws_access_key_id="AKID", aws_secret_access_key="secret",  # pyright: ignore[reportArgumentType]
        additional_model_request_fields={"thinking": {"type": "enabled", "budget_tokens": 50_000}},
    )
    bounded = limited(llm, 32_768)
    params = bounded._converse_params()  # pyright: ignore[reportAttributeAccessIssue]
    assert params["inferenceConfig"]["maxTokens"] == 32_768
    assert bounded.additional_model_request_fields["thinking"]["budget_tokens"] == 28_672  # pyright: ignore[reportAttributeAccessIssue]


def test_wf6_gemini_and_ollama_fields() -> None:
    gemini = ChatGoogleGenerativeAI(model="gemini-3-flash-preview", google_api_key="k")  # pyright: ignore[reportCallIssue, reportArgumentType]
    ollama = ChatOllama(model="llama3.2")
    assert limited(gemini, 32_768).max_output_tokens == 32_768  # pyright: ignore[reportAttributeAccessIssue]
    assert limited(ollama, 32_768).num_predict == 32_768  # pyright: ignore[reportAttributeAccessIssue]
    assert output_limit_field(object()) is None


def test_wf6_the_transport_binds_tools_onto_the_limited_model() -> None:
    transport = LangChainTransport(_azure(), model_name="gpt-6-luna")
    assert transport.accepts_output_limit
    tools = [ToolSchema(name="lookup", description="Look up", input_schema={"type": "object", "properties": {}})]
    bound = transport._bind_tools(tools, limit=32_768)  # pyright: ignore[reportPrivateUsage]
    unbound = transport._bind_tools(tools)  # pyright: ignore[reportPrivateUsage]
    assert bound.bound.max_tokens == 32_768  # pyright: ignore[reportAttributeAccessIssue]
    assert unbound.bound.max_tokens is None  # pyright: ignore[reportAttributeAccessIssue]
    # The binding cache is per limit: the unlimited call never gets the limited model, and vice versa.
    assert transport._bind_tools(tools, limit=32_768) is bound  # pyright: ignore[reportPrivateUsage]
    assert transport._bind_tools(tools, limit=8_192).bound.max_tokens == 8_192  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
