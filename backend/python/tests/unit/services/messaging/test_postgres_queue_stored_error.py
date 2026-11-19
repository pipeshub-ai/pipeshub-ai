from app.services.messaging.postgres.queue import stored_error


def test_urls_and_secret_pairs_are_redacted() -> None:
    out = stored_error("connect postgresql://app:hunter2pw@db:5432/x failed password=hunter2pw Bearer abc.def.ghi")
    assert "hunter2pw" not in out and "abc.def.ghi" not in out


def test_text_is_cut_to_500_chars() -> None:
    assert len(stored_error("x" * 5000)) == 500


def test_secret_at_the_cut_is_not_left_half_written() -> None:
    out = stored_error("a" * 495 + " password=hunter2pw")
    assert "hunter2" not in out
