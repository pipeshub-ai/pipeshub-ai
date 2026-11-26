#!/usr/bin/env bash
# Say which AI model provider the integration helper will configure, and fail fast when its
# credentials are missing, before the stack build rather than at model setup.
set -euo pipefail

provider="${TEST_AI_MODEL_PROVIDER:-}"
case "$provider" in
  openAI)
    echo "AI model provider: OpenAI"
    if [ -z "${TEST_OPENAI_API_KEY:-}" ]; then
      echo "::error::TEST_AI_MODEL_PROVIDER is openAI but the secret TEST_OPENAI_API_KEY is not set."
      exit 1
    fi
    ;;
  azureOpenAI)
    echo "AI model provider: Azure OpenAI"
    if [ -z "${TEST_AZURE_OPENAI_API_KEY:-}" ] || [ -z "${TEST_AZURE_OPENAI_ENDPOINT:-}" ]; then
      echo "::error::TEST_AI_MODEL_PROVIDER is azureOpenAI but TEST_AZURE_OPENAI_API_KEY or TEST_AZURE_OPENAI_ENDPOINT is not set."
      exit 1
    fi
    ;;
  openAICompatible)
    echo "AI model provider: OpenAI-compatible endpoint"
    missing=""
    for name in TEST_OPENAI_COMPATIBLE_ENDPOINT TEST_OPENAI_COMPATIBLE_API_KEY \
      TEST_OPENAI_COMPATIBLE_LLM_MODEL TEST_OPENAI_COMPATIBLE_EMBEDDING_MODEL; do
      if [ -z "${!name:-}" ]; then
        missing="$missing $name"
      fi
    done
    if [ -n "$missing" ]; then
      echo "::error::TEST_AI_MODEL_PROVIDER is openAICompatible but these are not set:$missing"
      exit 1
    fi
    ;;
  *)
    # gemini, groq and ollama are helper providers too, but these workflows pass none of their
    # settings, and a named provider gets no fallback, so model setup would fail later.
    echo "::error::TEST_AI_MODEL_PROVIDER must be openAI, azureOpenAI or openAICompatible (check its case); the workflows configure no other provider."
    exit 1
    ;;
esac
