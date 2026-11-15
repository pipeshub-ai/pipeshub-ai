"""A damaged file fails once, for good, with its own reason, however and wherever it is raised."""
from __future__ import annotations

import io
import pickle
import zipfile

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers import parse_pool
from app.modules.parsers.email.msg_to_html import msg_to_html
from app.modules.parsers.parse_worker import _portable
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.parsing.client import ParsingClientError
from app.services.parsing.interface import (
    ParseErrorCode,
    UnreadableFileError,
    damaged_file_reason,
)
from app.utils.user_errors import UNREADABLE_FILE, to_user_reason
from tests.support.new_type_samples import not_a_msg

REASON = damaged_file_reason("docm")


def _bad_zip() -> None:
    zipfile.ZipFile(io.BytesIO(b"not a zip"))


def _raised(how: str) -> UnreadableFileError:
    try:
        try:
            _bad_zip()
        except zipfile.BadZipFile as exc:
            if how == "from e":
                raise UnreadableFileError("docm", REASON) from exc
            if how == "from None":
                raise UnreadableFileError("docm", REASON) from None
            raise UnreadableFileError("docm", REASON)  # noqa: B904 - the plain raise is a case under test
    except UnreadableFileError as error:
        return error
    raise AssertionError("not raised")


def _wrapped(error: BaseException) -> DocumentProcessingError:
    """As a Processor method wraps a parser's error on the in-process path."""
    try:
        raise DocumentProcessingError("Failed to process document", doc_id="r1") from error
    except DocumentProcessingError as wrapped:
        return wrapped


@pytest.mark.parametrize("how", ["from e", "from None", "plain"])
def test_the_classifier_stops_at_it_however_it_was_raised(how: str) -> None:
    error = _raised(how)
    assert MessageErrorClassifier.classify_by_exception(error) == MessageErrorType.TERMINAL
    assert MessageErrorClassifier.classify_by_exception(_wrapped(error)) == MessageErrorType.TERMINAL


@pytest.mark.parametrize("how", ["from e", "from None", "plain"])
def test_the_record_gets_the_file_s_own_reason(how: str) -> None:
    error = _raised(how)
    assert to_user_reason(error) == REASON
    # In-process, a Processor wraps it in a DocumentProcessingError, which alone reads as
    # "unreadable file": the reason under it wins.
    assert to_user_reason(_wrapped(error)) == REASON
    assert to_user_reason(_wrapped(ValueError("boom"))) == UNREADABLE_FILE


def test_the_parsing_service_s_answer_carries_the_reason_to_the_record() -> None:
    answer = UnreadableFileError("msg", damaged_file_reason("msg")).to_dict()
    error = ParsingClientError(ParseErrorCode(answer["code"]), answer["message"], answer["details"])
    assert to_user_reason(error) == damaged_file_reason("msg")


def test_it_pickles_whole() -> None:
    error = _raised("from e")
    copy = pickle.loads(pickle.dumps(error))
    assert type(copy) is UnreadableFileError
    assert copy.details == {"extension": "docm", "user_reason": REASON}
    assert copy.message == REASON
    assert copy.code == ParseErrorCode.PARSE_FAILED
    assert copy.status_code == 422
    assert MessageErrorClassifier.classify_by_exception(copy) == MessageErrorType.TERMINAL


def test_the_parse_worker_sends_it_back_as_it_is() -> None:
    error = _raised("plain")
    assert _portable(error) is error


@pytest.mark.timeout(120)
def test_a_damaged_msg_comes_back_from_a_real_parse_worker_as_itself() -> None:
    with pytest.raises(UnreadableFileError) as caught:
        parse_pool.run_blocking(msg_to_html, not_a_msg(), label="bad.msg", size=6111, timeout=60)
    assert caught.value.details == {"extension": "msg", "user_reason": damaged_file_reason("msg")}
    assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TERMINAL
