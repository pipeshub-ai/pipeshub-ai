"""
Unit tests for app.agents.actions.google.gmail.utils

Tests the GmailUtils helper class for email validation,
MIME message construction, and attachment handling.
"""

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from app.agents.actions.google.gmail.utils import GmailUtils


# ============================================================================
# validate_email
# ============================================================================

class TestValidateEmail:
    def test_valid_email(self):
        assert GmailUtils.validate_email("user@example.com") is True

    def test_valid_email_with_plus(self):
        assert GmailUtils.validate_email("user+tag@example.com") is True

    def test_valid_email_with_dots(self):
        assert GmailUtils.validate_email("first.last@example.com") is True

    def test_valid_email_with_hyphen(self):
        assert GmailUtils.validate_email("user-name@example.com") is True

    def test_empty_string_returns_false(self):
        assert GmailUtils.validate_email("") is False

    def test_none_returns_false(self):
        assert GmailUtils.validate_email(None) is False

    def test_whitespace_only_returns_false(self):
        assert GmailUtils.validate_email("   ") is False

    def test_no_at_symbol_returns_false(self):
        assert GmailUtils.validate_email("userexample.com") is False

    def test_no_domain_returns_false(self):
        assert GmailUtils.validate_email("user@") is False

    def test_no_username_returns_false(self):
        assert GmailUtils.validate_email("@example.com") is False

    def test_missing_tld_returns_false(self):
        assert GmailUtils.validate_email("user@example") is False

    def test_spaces_in_email_returns_false(self):
        assert GmailUtils.validate_email("user @example.com") is False

    def test_strips_whitespace(self):
        assert GmailUtils.validate_email("  user@example.com  ") is True


# ============================================================================
# validate_email_list
# ============================================================================

class TestValidateEmailList:
    def test_valid_email_list(self):
        emails = ["user1@example.com", "user2@example.com"]
        assert GmailUtils.validate_email_list(emails) is True

    def test_empty_list_returns_true(self):
        assert GmailUtils.validate_email_list([]) is True

    def test_single_invalid_email_returns_false(self):
        emails = ["user1@example.com", "invalid-email"]
        assert GmailUtils.validate_email_list(emails) is False

    def test_all_invalid_returns_false(self):
        emails = ["invalid1", "invalid2"]
        assert GmailUtils.validate_email_list(emails) is False

    def test_first_valid_second_invalid_returns_false(self):
        emails = ["valid@example.com", "no-at-sign.com"]
        assert GmailUtils.validate_email_list(emails) is False


# ============================================================================
# validate_subject
# ============================================================================

class TestValidateSubject:
    def test_non_empty_subject_returns_true(self):
        assert GmailUtils.validate_subject("Hello World") is True

    def test_empty_string_returns_false(self):
        assert GmailUtils.validate_subject("") is False

    def test_whitespace_only_returns_false(self):
        assert GmailUtils.validate_subject("   ") is False

    def test_none_returns_false(self):
        assert GmailUtils.validate_subject(None) is False

    def test_subject_with_special_chars(self):
        assert GmailUtils.validate_subject("Re: Important!!! #123") is True


# ============================================================================
# encode_message
# ============================================================================

class TestEncodeMessage:
    def test_encodes_simple_message(self):
        result = GmailUtils.encode_message("Hello")
        decoded = base64.urlsafe_b64decode(result).decode("utf-8")
        assert decoded == "Hello"

    def test_empty_string_returns_empty(self):
        assert GmailUtils.encode_message("") == ""

    def test_none_returns_empty(self):
        assert GmailUtils.encode_message(None) == ""

    def test_encodes_special_characters(self):
        message = "Hello! 你好"
        result = GmailUtils.encode_message(message)
        decoded = base64.urlsafe_b64decode(result).decode("utf-8")
        assert decoded == message


# ============================================================================
# transform_message_body
# ============================================================================

class TestTransformMessageBody:
    def test_plain_html_body_with_newlines(self):
        """Newlines are converted to <br> tags."""
        result = GmailUtils.transform_message_body(
            mail_to=["recipient@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Line 1\nLine 2",
            mail_attachments=None,
        )
        
        assert "raw" in result
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "Line 1<br>Line 2" in raw_decoded

    def test_escaped_newlines_converted(self):
        """\\n is converted to <br>."""
        result = GmailUtils.transform_message_body(
            mail_to=["recipient@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Line 1\\nLine 2",
            mail_attachments=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "Line 1<br>Line 2" in raw_decoded

    def test_sets_to_header(self):
        """To header is set correctly."""
        result = GmailUtils.transform_message_body(
            mail_to=["user1@example.com", "user2@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "to: user1@example.com, user2@example.com" in raw_decoded

    def test_sets_subject_header(self):
        """Subject header is set correctly."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Important Subject",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "subject: Important Subject" in raw_decoded

    def test_sets_cc_header(self):
        """Cc header is set when provided."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Test",
            mail_cc=["cc1@example.com", "cc2@example.com"],
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert ("Cc: cc1@example.com, cc2@example.com" in raw_decoded or 
                "CC: cc1@example.com, cc2@example.com" in raw_decoded)

    def test_sets_bcc_header(self):
        """Bcc header is set when provided."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=["bcc@example.com"],
            mail_body="Body",
            mail_attachments=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert ("Bcc: bcc@example.com" in raw_decoded or 
                "BCC: bcc@example.com" in raw_decoded)

    def test_sets_reply_headers_for_threading(self):
        """In-Reply-To and References headers are set for replies."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Re: Original",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Reply body",
            mail_attachments=None,
            thread_id="thread-123",
            message_id="<msg-456@mail.example.com>",
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "In-Reply-To: <msg-456@mail.example.com>" in raw_decoded
        assert "References: <msg-456@mail.example.com>" in raw_decoded
        assert result["threadId"] == "thread-123"

    def test_thread_id_without_message_id_skips_reply_headers(self):
        """Thread ID without message ID does not set reply headers."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=None,
            thread_id="thread-123",
            message_id=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "In-Reply-To:" not in raw_decoded
        assert result["threadId"] == "thread-123"

    def test_attaches_existing_file(self, tmp_path):
        """Existing file is attached correctly."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content")
        
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="With Attachment",
            mail_cc=None,
            mail_bcc=None,
            mail_body="See attachment",
            mail_attachments=[str(test_file)],
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "Content-Type: multipart/mixed" in raw_decoded
        assert "test.txt" in raw_decoded

    def test_skips_missing_attachment_path(self, tmp_path):
        """Missing file does not abort entire send."""
        # Create one valid file
        valid_file = tmp_path / "valid.txt"
        valid_file.write_text("Valid content")
        
        # Reference a non-existent file
        missing_file = tmp_path / "missing.txt"
        
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Partial Attachments",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=[str(valid_file), str(missing_file)],
        )
        
        # Should succeed with at least the valid file
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "valid.txt" in raw_decoded
        # Missing file should not appear
        assert "missing.txt" not in raw_decoded

    def test_attachment_read_failure_continues(self, tmp_path, monkeypatch):
        """File read error does not abort entire send."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content")
        
        # Mock open to raise an exception
        original_open = open
        def mock_open(path, *args, **kwargs):
            if "test.txt" in str(path):
                raise IOError("Read error")
            return original_open(path, *args, **kwargs)
        
        monkeypatch.setattr("builtins.open", mock_open)
        
        # Should not raise, just log error
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=[str(test_file)],
        )
        
        # Message should still be created
        assert "raw" in result

    def test_empty_body_handled(self):
        """Empty body does not cause errors."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="No Body",
            mail_cc=None,
            mail_bcc=None,
            mail_body=None,
            mail_attachments=None,
        )
        
        assert "raw" in result
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "subject: No Body" in raw_decoded

    def test_multiple_attachments(self, tmp_path):
        """Multiple attachments are all included."""
        file1 = tmp_path / "file1.txt"
        file1.write_text("Content 1")
        file2 = tmp_path / "file2.txt"
        file2.write_text("Content 2")
        
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Multiple Files",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=[str(file1), str(file2)],
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "file1.txt" in raw_decoded
        assert "file2.txt" in raw_decoded

    def test_from_header_always_me(self):
        """From header is always 'me' for Gmail API."""
        result = GmailUtils.transform_message_body(
            mail_to=["user@example.com"],
            mail_subject="Test",
            mail_cc=None,
            mail_bcc=None,
            mail_body="Body",
            mail_attachments=None,
        )
        
        raw_decoded = base64.urlsafe_b64decode(result["raw"]).decode("utf-8")
        assert "from: me" in raw_decoded
