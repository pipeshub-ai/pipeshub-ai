"""Unit tests for text_splitting helpers."""

from unittest.mock import patch

from app.modules.parsers.text_splitting import (
    MAX_TEXT_BLOCK_CHARS,
    detect_language,
    split_into_sentences,
    split_long_text,
)


class TestSplitLongText:
    def test_short_text_unchanged(self):
        text = "Hello world. Second sentence."
        assert split_long_text(text) == [text]

    def test_empty_text(self):
        assert split_long_text("") == []

    def test_splits_at_sentence_boundaries(self):
        sentence = "A" * 300_000 + "."
        text = " ".join([sentence] * 4)
        chunks = split_long_text(text, max_chars=500_000)
        assert len(chunks) >= 2
        assert all(len(chunk) <= 500_000 for chunk in chunks)
        assert "".join(chunks).replace(" ", "") == text.replace(" ", "")

    def test_hard_splits_sentence_without_boundaries(self):
        text = "x" * (MAX_TEXT_BLOCK_CHARS + 1)
        chunks = split_long_text(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == MAX_TEXT_BLOCK_CHARS
        assert len(chunks[1]) == 1

    def test_respects_custom_max_chars(self):
        text = "word " * 200
        chunks = split_long_text(text, max_chars=50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 50 for chunk in chunks)


class TestSplitIntoSentences:
    """Tests for the pysbd-based multilingual sentence splitter."""

    def test_empty_text_returns_empty_list(self):
        assert split_into_sentences("", "en") == []
        assert split_into_sentences("   ", "en") == []

    def test_english_multi_sentence(self):
        result = split_into_sentences("First sentence. Second sentence.", "en")
        assert len(result) == 2

    def test_english_abbreviation_not_split(self):
        result = split_into_sentences("Dr. Smith went home.", "en")
        assert len(result) == 1

    def test_natively_supported_language_spanish(self):
        result = split_into_sentences("Hola mundo. Esta es otra frase.", "es")
        assert len(result) == 2

    def test_natively_supported_language_hindi(self):
        result = split_into_sentences("यह पहला वाक्य है। यह दूसरा वाक्य है।", "hi")
        assert len(result) >= 1

    def test_natively_supported_language_chinese(self):
        result = split_into_sentences("这是第一句。这是第二句。", "zh")
        assert len(result) >= 1

    def test_unaliased_unsupported_language_falls_back_to_english_rules(self):
        """A language with no native rule set and no alias defaults to English rules."""
        result = split_into_sentences("First sentence. Second sentence.", "sw")
        assert len(result) == 2

    def test_aliased_language_portuguese_uses_spanish_rules(self):
        result = split_into_sentences("Ola mundo. Esta e outra frase.", "pt")
        assert len(result) == 2

    def test_aliased_language_bengali_uses_hindi_rules(self):
        result = split_into_sentences("এটি প্রথম বাক্য। এটি দ্বিতীয় বাক্য।", "bn")
        assert len(result) >= 1

    def test_segmenter_failure_falls_back_to_regex(self):
        with patch(
            "app.modules.parsers.text_splitting._get_segmenter",
            side_effect=RuntimeError("boom"),
        ):
            result = split_into_sentences("First sentence. Second sentence.", "en")
        assert len(result) == 2


class TestDetectLanguage:
    """Tests for the lingua-based language detector."""

    def test_empty_text_returns_default(self):
        assert detect_language("") == "en"
        assert detect_language("   ") == "en"

    def test_detects_english(self):
        text = "This is a perfectly ordinary English sentence about the weather today."
        assert detect_language(text) == "en"

    def test_detects_spanish(self):
        text = "Este es un texto en espanol sobre el clima de hoy en la ciudad."
        assert detect_language(text) == "es"

    def test_custom_default_used_on_failure(self):
        with patch(
            "app.modules.parsers.text_splitting._get_detector",
            side_effect=RuntimeError("boom"),
        ):
            assert detect_language("some text", default="fr") == "fr"

    def test_none_detection_result_falls_back_to_default(self):
        mock_detector = type(
            "MockDetector", (), {"detect_language_of": staticmethod(lambda _: None)}
        )()
        with patch(
            "app.modules.parsers.text_splitting._get_detector",
            return_value=mock_detector,
        ):
            assert detect_language("gibberish", default="en") == "en"
