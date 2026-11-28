"""`.github/scripts/check_ai_model_provider.sh`: each provider passes with its settings and fails without them.

The check runs before the stack is built. A provider it rejects fails every job that uses it, and a provider it
admits without its settings fails much later, at model setup, after the stack has been built.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "check_ai_model_provider.sh"

COMPATIBLE = {
    "TEST_OPENAI_COMPATIBLE_ENDPOINT": "https://gateway.example/v1",
    "TEST_OPENAI_COMPATIBLE_API_KEY": "k",
    "TEST_OPENAI_COMPATIBLE_LLM_MODEL": "m",
    "TEST_OPENAI_COMPATIBLE_EMBEDDING_MODEL": "e",
}


def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("TEST_")}
    return subprocess.run(["bash", str(SCRIPT)], env={**clean, **env}, capture_output=True, text=True, check=False)


class ProviderCheck(unittest.TestCase):
    def test_each_provider_passes_with_its_settings(self) -> None:
        cases = {
            "openAI": {"TEST_OPENAI_API_KEY": "k"},
            "azureOpenAI": {"TEST_AZURE_OPENAI_API_KEY": "k", "TEST_AZURE_OPENAI_ENDPOINT": "https://x.example"},
            "openAICompatible": COMPATIBLE,
            "ollama": {"TEST_OLLAMA_ENDPOINT": "http://fakellm:11434"},
        }
        for provider, settings in cases.items():
            with self.subTest(provider=provider):
                result = run({"TEST_AI_MODEL_PROVIDER": provider, **settings})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_each_provider_fails_without_its_settings(self) -> None:
        for provider in ("openAI", "azureOpenAI", "openAICompatible", "ollama"):
            with self.subTest(provider=provider):
                result = run({"TEST_AI_MODEL_PROVIDER": provider})
                self.assertEqual(result.returncode, 1)
                self.assertIn("::error::", result.stdout)

    def test_unknown_and_unset_providers_fail(self) -> None:
        for provider in ("", "gemini", "Ollama"):
            with self.subTest(provider=provider):
                self.assertEqual(run({"TEST_AI_MODEL_PROVIDER": provider}).returncode, 1)


if __name__ == "__main__":
    unittest.main()
