"""PIPESHUB_OFFLINE=1: the switch mapping, the embedding fail-fast, telemetry, tokenizer cache, table mode."""

import hashlib
import importlib.util
import logging
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.offline import (
    OFFLINE_ENV_DEFAULTS,
    OFFLINE_FLAG,
    apply_offline_env,
    offline_enabled,
)

REPO = Path(__file__).resolve().parents[5]


def _clean(monkeypatch) -> None:
    """Unset every switch variable, and make the test put back whatever it sets (even from apply_offline_env)."""
    for name in (*OFFLINE_ENV_DEFAULTS, OFFLINE_FLAG):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)


class TestSwitchMapping:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
    def test_on_values(self, value) -> None:
        assert offline_enabled({OFFLINE_FLAG: value})

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2"])
    def test_off_values(self, value) -> None:
        env = {OFFLINE_FLAG: value}
        assert not offline_enabled(env)
        assert apply_offline_env(env) == []
        assert env == {OFFLINE_FLAG: value}

    def test_unset_changes_nothing(self) -> None:
        env: dict[str, str] = {}
        assert apply_offline_env(env) == []
        assert env == {}

    def test_sets_every_switch(self) -> None:
        env = {OFFLINE_FLAG: "1"}
        apply_offline_env(env)
        assert {k: env[k] for k in OFFLINE_ENV_DEFAULTS} == OFFLINE_ENV_DEFAULTS
        assert env["TELEMETRY_COLLECTION_FLAG"] == "false"

    def test_operator_value_wins(self) -> None:
        env = {OFFLINE_FLAG: "1", "TELEMETRY_COLLECTION_FLAG": "true", "HF_HUB_OFFLINE": "0"}
        changed = apply_offline_env(env)
        assert env["TELEMETRY_COLLECTION_FLAG"] == "true"
        assert env["HF_HUB_OFFLINE"] == "0"
        assert "HF_HUB_OFFLINE" not in changed

    def test_the_flag_and_explicit_values_in_dotenv_count(self, tmp_path, monkeypatch) -> None:
        """A service started directly reads .env only after the package import: the switch must see it."""
        (tmp_path / ".env").write_text("PIPESHUB_OFFLINE=1\nHF_HUB_OFFLINE=0\nDO_NOT_TRACK=\n")
        monkeypatch.chdir(tmp_path)
        _clean(monkeypatch)
        assert offline_enabled()
        changed = apply_offline_env()
        assert "HF_HUB_OFFLINE" not in changed and "HF_HUB_OFFLINE" not in os.environ  # .env keeps precedence
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1" and os.environ["DO_NOT_TRACK"] == "1"
        import dotenv

        dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True))
        assert os.environ["HF_HUB_OFFLINE"] == "0"

    def test_the_process_environment_beats_dotenv_for_the_flag(self, tmp_path, monkeypatch) -> None:
        (tmp_path / ".env").write_text("PIPESHUB_OFFLINE=1\n")
        monkeypatch.chdir(tmp_path)
        _clean(monkeypatch)
        monkeypatch.setenv(OFFLINE_FLAG, "0")
        assert not offline_enabled() and apply_offline_env() == []

    def test_shell_script_sets_the_same_switches(self) -> None:
        script = (REPO / "deployment" / "offline-env.sh").read_text()
        pairs = dict(re.findall(r"\b([A-Z][A-Z0-9_]+)=(\S+?)(?=[\s;\\]|$)", script.split("for _pair in", 1)[1].split("; do", 1)[0]))
        assert pairs == OFFLINE_ENV_DEFAULTS


class TestTelemetry:
    def test_off_under_the_switch_when_flag_unset(self, monkeypatch) -> None:
        from app.telemetry.flag import TELEMETRY_COLLECTION_FLAG, telemetry_enabled

        monkeypatch.delenv(TELEMETRY_COLLECTION_FLAG, raising=False)
        monkeypatch.setenv(OFFLINE_FLAG, "1")
        assert telemetry_enabled() is False

    def test_explicit_value_still_wins(self, monkeypatch) -> None:
        from app.telemetry.flag import TELEMETRY_COLLECTION_FLAG, telemetry_enabled

        monkeypatch.setenv(TELEMETRY_COLLECTION_FLAG, "true")
        monkeypatch.setenv(OFFLINE_FLAG, "1")
        assert telemetry_enabled() is True

    def test_online_default_unchanged(self, monkeypatch) -> None:
        from app.edition import TELEMETRY_DEFAULT_ENABLED
        from app.telemetry.flag import TELEMETRY_COLLECTION_FLAG, telemetry_enabled

        monkeypatch.delenv(TELEMETRY_COLLECTION_FLAG, raising=False)
        monkeypatch.delenv(OFFLINE_FLAG, raising=False)
        assert telemetry_enabled() is TELEMETRY_DEFAULT_ENABLED

    @pytest.mark.asyncio
    async def test_pusher_does_not_start(self, monkeypatch) -> None:
        from app.telemetry.flag import TELEMETRY_COLLECTION_FLAG
        from app.telemetry.pusher import MetricsPusher

        monkeypatch.delenv(TELEMETRY_COLLECTION_FLAG, raising=False)
        monkeypatch.setenv(OFFLINE_FLAG, "1")
        pusher = MetricsPusher(MagicMock(), "svc", logging.getLogger("t"))
        await pusher.start()
        assert pusher._task is None


class TestEmbeddingOffline:
    @staticmethod
    def _switch_on(monkeypatch, **explicit) -> None:
        """The state a service is in after ``import app`` with the switch on and these values set."""
        _clean(monkeypatch)
        monkeypatch.setenv(OFFLINE_FLAG, "1")
        for name, value in explicit.items():
            monkeypatch.setenv(name, value)
        apply_offline_env()

    @staticmethod
    async def _load(model: str = "BAAI/bge-large-en-v1.5"):  # noqa: ANN205
        from app.embedding_main import ModelManager

        async def inline(func, *a, **k):  # noqa: ANN202
            return func(*a, **k)

        manager = ModelManager(device="cpu")
        with patch("sentence_transformers.SentenceTransformer", side_effect=OSError("not cached")) as st, \
             patch("app.embedding_main._download_with_progress") as download, \
             patch("app.embedding_main.asyncio.to_thread", side_effect=inline):
            try:
                await manager.get_model(model)
                error = None
            except Exception as exc:
                error = exc
        return error, download, st

    def test_message_names_the_situation_and_the_ways_out(self) -> None:
        from app.embedding_main import _offline_missing_model_message

        text = _offline_missing_model_message("my/model")
        assert "'my/model' is not in this image and cannot be downloaded" in text
        assert "full image" in text and "embedding service" in text and "HF_HUB_OFFLINE=0" in text

    @pytest.mark.asyncio
    async def test_missing_model_fails_without_touching_the_hub(self, monkeypatch) -> None:
        self._switch_on(monkeypatch)
        with patch("huggingface_hub.HfApi") as hub:
            error, download, _ = await self._load()
        assert isinstance(error, RuntimeError) and "cannot be downloaded" in str(error)
        download.assert_not_called()
        hub.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_explicit_hf_hub_offline_0_still_downloads_under_the_switch(self, monkeypatch) -> None:
        self._switch_on(monkeypatch, HF_HUB_OFFLINE="0")
        error, download, _ = await self._load()
        download.assert_called_once()
        assert error is None or "cannot be downloaded" not in str(error)

    @pytest.mark.asyncio
    async def test_hf_hub_offline_alone_refuses_and_says_how_to_allow_the_download(self, monkeypatch) -> None:
        _clean(monkeypatch)
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        error, download, _ = await self._load()
        assert "HF_HUB_OFFLINE=0" in str(error)
        download.assert_not_called()

    def test_model_info_has_a_timeout_in_every_mode(self, monkeypatch) -> None:
        from app.embedding_main import (
            HUB_METADATA_TIMEOUT_SECONDS,
            _compute_expected_total_bytes,
        )

        monkeypatch.delenv(OFFLINE_FLAG, raising=False)
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.model_info.return_value.siblings = []
            _compute_expected_total_bytes("some/model")
        assert api.return_value.model_info.call_args.kwargs["timeout"] == HUB_METADATA_TIMEOUT_SECONDS


class TestTokenizerCache:
    def test_failed_load_is_cached_once(self) -> None:
        from app.utils import chat_helpers

        chat_helpers._cl100k_encoder.cache_clear()
        try:
            with patch("tiktoken.get_encoding", side_effect=OSError("no network")) as get:
                for _ in range(5):
                    assert chat_helpers.count_tokens_text("abcdefgh", None) == 2
                chat_helpers.count_tokens([], [[{"type": "text", "text": "abcdefgh"}]])
            assert get.call_count == 1
        finally:
            chat_helpers._cl100k_encoder.cache_clear()

    def test_image_bakes_the_table_where_tiktoken_looks(self) -> None:
        dockerfile = (REPO / "Dockerfile").read_text()
        assert "ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken" in dockerfile
        assert "bake-tiktoken.py" in dockerfile
        dev = (REPO / "deployment" / "docker-compose" / "hot-reload" / "Dockerfile.dev").read_text()
        assert "ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken" in dev and "bake-tiktoken.py" in dev

    def test_bake_script_names_the_file_tiktoken_looks_up(self) -> None:
        spec = importlib.util.spec_from_file_location("bake_tiktoken", REPO / "deployment" / "bake-tiktoken.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hashlib.sha1(mod.URL.encode()).hexdigest() == "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
        import inspect

        import tiktoken_ext.openai_public as pub

        assert mod.SHA256 in inspect.getsource(pub.cl100k_base)


class TestTableMode:
    """effective_table_mode (the models module) falls back the same way offline as online."""

    def _baked(self, tmp_path, monkeypatch, *modes):  # noqa: ANN202
        from app.config.constants import docling_models as dm

        monkeypatch.setenv(dm.DOCLING_ARTIFACTS_ENV, str(tmp_path))
        files = [(dm.LAYOUT_HERON_REPO, f) for f in dm.LAYOUT_HERON_FILES]
        files += [(dm.TABLEFORMER_REPO, f) for mode in modes for f in dm.tableformer_files(mode)]
        for repo, f in files:
            p = tmp_path / dm._folder(repo) / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        dm._warn_mode_once.cache_clear()
        return dm

    def test_offline_fast_without_weights_falls_back_with_one_warning(self, tmp_path, monkeypatch, caplog) -> None:
        dm = self._baked(tmp_path, monkeypatch, "accurate")
        monkeypatch.setenv(OFFLINE_FLAG, "1")
        with caplog.at_level(logging.WARNING):
            assert dm.effective_table_mode("fast") == "accurate"
            assert dm.effective_table_mode("fast") == "accurate"
        assert len([r for r in caplog.records if "Table mode" in r.getMessage()]) == 1

    def test_offline_fast_with_weights_keeps_fast(self, tmp_path, monkeypatch) -> None:
        dm = self._baked(tmp_path, monkeypatch, "accurate", "fast")
        monkeypatch.setenv(OFFLINE_FLAG, "1")
        assert dm.effective_table_mode("fast") == "fast"


class TestSmokeLoopbackMatching:
    """The image smoke records every non-loopback connect or lookup; only exact loopback names pass."""

    @staticmethod
    def _smoke() -> object:
        path = REPO / "deployment" / "docker-compose" / "tests" / "offline_image_smoke.py"
        spec = importlib.util.spec_from_file_location("offline_image_smoke_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost.", "127.0.0.1", "127.9.9.9", "::1", "0.0.0.0", "::ffff:127.0.0.1", "", None])
    def test_local_hosts_pass(self, host) -> None:
        assert self._smoke().is_local("socket.getaddrinfo", host)

    @pytest.mark.parametrize("host", ["localhost.example.com", "127.evil.example", "127.0.0.1.example.com", "::1.example.com", "1.1.1.1", "example.com", "10.0.0.1"])
    def test_external_names_are_recorded(self, host) -> None:
        assert not self._smoke().is_local("socket.getaddrinfo", host)
        assert not self._smoke().is_local("socket.connect", (host, 443))

    def test_a_unix_socket_path_is_local_only_for_connect(self) -> None:
        smoke = self._smoke()
        assert smoke.is_local("socket.connect", "/var/run/x.sock")
        assert not smoke.is_local("socket.getaddrinfo", "/var/run/example.com")
