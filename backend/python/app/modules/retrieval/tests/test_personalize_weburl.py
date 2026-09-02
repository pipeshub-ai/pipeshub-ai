from app.modules.retrieval.retrieval_service import personalize_gmail_weburl


def test_personalize_gmail_weburl_replaces_owner_with_viewer():
    stored = "https://mail.google.com/mail?authuser=alice@corp.com#all/12345"
    result = personalize_gmail_weburl(stored, "bob@corp.com")
    assert result == "https://mail.google.com/mail?authuser=bob@corp.com#all/12345"


def test_personalize_gmail_weburl_noop_on_non_gmail_url():
    stored = "https://drive.google.com/file/d/xyz"
    result = personalize_gmail_weburl(stored, "bob@corp.com")
    assert result == stored
