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
  *)
    # gemini, groq and ollama are helper providers too, but these workflows pass none of their
    # settings, and a named provider gets no fallback, so model setup would fail later.
    echo "::error::TEST_AI_MODEL_PROVIDER must be openAI or azureOpenAI (check its case); the workflows configure no other provider."
    exit 1
    ;;
esac
