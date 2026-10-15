"""Every turn of one record's extraction asks the provider for the same prompt cache."""

from unittest.mock import AsyncMock, patch

from langchain_openai import AzureChatOpenAI

from app.modules.named_entities.extractor import indexing_llm


def _azure() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint="https://acme.openai.azure.com", azure_deployment="gpt-5.4",
        api_version="2025-04-01-preview", api_key="test",
    )


async def _cache_kwargs(llm, **kwargs) -> dict:
    config = {"provider": "azureOpenAI", "configuration": {"model": "gpt-5.4"}}
    with patch("app.utils.llm.get_llm_for_role", AsyncMock(return_value=(llm, config))):
        _, transport, _, _ = await indexing_llm(object(), **kwargs)
    return transport._inner._cache.finalize([])[1]


async def test_indexing_llm_names_the_named_entity_cache() -> None:
    kwargs = await _cache_kwargs(_azure(), org_id="org-1", shard_key="report.pdf")
    assert ":named-entities:" in kwargs["prompt_cache_key"]
    assert kwargs == await _cache_kwargs(_azure(), org_id="org-1", shard_key="report.pdf")


async def test_an_unknown_provider_gets_no_cache_parameters() -> None:
    from unittest.mock import MagicMock

    assert await _cache_kwargs(MagicMock(), org_id="org-1", shard_key="report.pdf") == {}
